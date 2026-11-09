from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import AppointmentHistory, Base, PatientTimelineEvent, get_db
from app.main import app


@pytest.fixture
async def client_and_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client, email: str, clinic_name: str = "Timeline Clinic"):
    response = await client.post("/api/auth/register", json={
        "clinic_name": clinic_name,
        "full_name": "Clinic Manager",
        "email": email,
        "password": "correct horse battery staple",
    })
    assert response.status_code == 201, response.text
    return response.json()


async def invite_doctor(client, manager_headers, email: str):
    response = await client.post("/api/staff/invitations", headers=manager_headers, json={
        "email": email, "full_name": "Invited Doctor", "role": "doctor",
    })
    assert response.status_code == 201, response.text
    token = parse_qs(urlparse(response.json()["invitation_url"]).query)["token"][0]
    accepted = await client.post("/api/staff/invitations/accept", params={"token": token}, json={
        "password": "correct horse battery staple",
    })
    assert accepted.status_code == 200, accepted.text
    login = await client.post("/api/auth/login", json={
        "email": email, "password": "correct horse battery staple",
    })
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_api_operations_project_stable_timeline_and_revisions(client_and_db):
    client, sessions = client_and_db
    manager = await register(client, "timeline-main@example.com")
    headers = {"Authorization": f"Bearer {manager['access_token']}"}
    me = (await client.get("/api/auth/me", headers=headers)).json()
    patient = (await client.post("/api/patients", headers=headers, json={
        "first_name": "Ana", "last_name": "Test",
    })).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Room A"})).json()
    appointment_response = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient["id"], "doctor_id": me["id"], "room_id": room["id"],
        "starts_at": "2027-03-02T10:00:00+02:00", "duration_minutes": 30,
        "appointment_type": "Consultation",
    })
    assert appointment_response.status_code == 201, appointment_response.text
    appointment = appointment_response.json()

    catalog_response = await client.post("/api/procedures", headers=headers, json={
        "code": "EXAM", "name": "Examination", "default_duration_minutes": 10,
        "base_price": "50.00", "currency": "RON",
    })
    assert catalog_response.status_code == 201, catalog_response.text
    procedure_response = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment["id"],
        "procedure_catalog_id": catalog_response.json()["id"],
    })
    assert procedure_response.status_code == 201, procedure_response.text
    procedure = procedure_response.json()
    started = await client.post(f"/api/performed-procedures/{procedure['id']}/start", headers=headers)
    assert started.status_code == 200, started.text

    note_response = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient["id"], "note_type": "finding",
        "title": "Finding", "content": "Initial finding",
    })
    assert note_response.status_code == 201, note_response.text
    note = note_response.json()
    finalized = await client.post(f"/api/clinical-notes/{note['id']}/finalize", headers=headers)
    assert finalized.status_code == 200, finalized.text
    addendum = await client.post(f"/api/clinical-notes/{note['id']}/addendum", headers=headers, json={
        "addendum_content": "Correction recorded", "reason": "Observed correction",
    })
    assert addendum.status_code == 200, addendum.text

    url = f"/api/clinical-notes/patients/{patient['id']}/timeline"
    first = await client.get(url, headers=headers)
    assert first.status_code == 200, first.text
    events = first.json()
    assert any(e["entity_type"] == "appointment" and e["entity_id"] == appointment["id"] for e in events)
    assert any(e["source"] == "procedure_execution_segment" for e in events)
    assert any(e["source"] == "clinical_note_revision" for e in events)
    first_ids = [e["id"] for e in events]

    for _ in range(2):
        assert (await client.post("/api/clinical-notes/timeline/refresh", headers=headers)).status_code == 200
    second = await client.get(url, headers=headers)
    assert [e["id"] for e in second.json()] == first_ids
    record = await client.get(f"/api/clinical-notes/patients/{patient['id']}/record", headers=headers)
    assert record.status_code == 200, record.text
    assert [e["id"] for e in record.json()["timeline"]] == first_ids

    # Existing import rows with equal timestamps have deterministic ID tie-breaks
    # and remain stable across offset pagination.
    tie_time = datetime(2027, 3, 2, 8, 0, tzinfo=UTC)
    tie_ids = [UUID(int=1001), UUID(int=1002), UUID(int=1003)]
    async with sessions() as session:
        for event_id in tie_ids:
            session.add(PatientTimelineEvent(
                id=event_id, clinic_id=UUID(me["clinic_id"]), patient_id=UUID(patient["id"]),
                event_type="appointment", entity_type="legacy_import", entity_id=event_id,
                title="Imported appointment", description="Imported", actor_id=UUID(me["id"]),
                occurred_at=tie_time, recorded_at=tie_time, source="legacy_import", metadata={},
            ))
        await session.commit()
    pages = []
    for offset in range(3):
        page = await client.get(url, headers=headers, params={"event_type": "appointment", "limit": 1, "offset": offset})
        assert page.status_code == 200, page.text
        pages.append(page.json()[0]["id"])
    assert pages == [str(tie_ids[2]), str(tie_ids[1]), str(tie_ids[0])]
    repeated_page = await client.get(url, headers=headers, params={"event_type": "appointment", "limit": 1, "offset": 1})
    assert repeated_page.json()[0]["id"] == pages[1]


@pytest.mark.asyncio
async def test_draft_timeline_is_author_only_and_patient_is_clinic_scoped(client_and_db):
    client, _sessions = client_and_db
    manager = await register(client, "timeline-manager@example.com")
    manager_headers = {"Authorization": f"Bearer {manager['access_token']}"}
    doctor_headers = await invite_doctor(client, manager_headers, "timeline-author@example.com")
    other_clinic = await register(client, "timeline-other@example.com", "Other Timeline Clinic")
    other_headers = {"Authorization": f"Bearer {other_clinic['access_token']}"}

    patient = (await client.post("/api/patients", headers=doctor_headers, json={
        "first_name": "Draft", "last_name": "Patient",
    })).json()
    draft = await client.post("/api/clinical-notes", headers=doctor_headers, json={
        "patient_id": patient["id"], "note_type": "finding",
        "title": "PRIVATE TITLE", "content": "PRIVATE CONTENT",
    })
    assert draft.status_code == 201, draft.text

    timeline_url = f"/api/clinical-notes/patients/{patient['id']}/timeline"
    author_timeline = await client.get(timeline_url, headers=doctor_headers)
    manager_timeline = await client.get(timeline_url, headers=manager_headers)
    author_record = await client.get(f"/api/clinical-notes/patients/{patient['id']}/record", headers=doctor_headers)
    manager_record = await client.get(f"/api/clinical-notes/patients/{patient['id']}/record", headers=manager_headers)
    assert draft.json()["id"] in {e["entity_id"] for e in author_timeline.json()}
    assert draft.json()["id"] not in {e["entity_id"] for e in manager_timeline.json()}
    assert draft.json()["id"] in {e["id"] for e in author_record.json()["clinical_notes"]}
    assert draft.json()["id"] not in {e["id"] for e in manager_record.json()["clinical_notes"]}
    assert draft.json()["id"] not in {e["entity_id"] for e in manager_record.json()["timeline"]}
    assert "PRIVATE TITLE" not in manager_timeline.text and "PRIVATE CONTENT" not in manager_timeline.text
    assert (await client.get(timeline_url, headers=other_headers)).status_code == 404


async def _create_timeline_appointment(client, headers, doctor_id, patient_id, room_id, starts_at):
    response = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id, "doctor_id": doctor_id, "room_id": room_id,
        "starts_at": starts_at, "duration_minutes": 30, "appointment_type": "Consultation",
    })
    assert response.status_code == 201, response.text
    return response.json()


async def _add_legacy_note_event(
    sessions, *, clinic_id, patient_id, note_id, event_id, occurred_at, source="legacy_import",
):
    async with sessions() as session:
        session.add(PatientTimelineEvent(
            id=event_id, clinic_id=UUID(clinic_id), patient_id=UUID(patient_id),
            event_type="clinical_note", entity_type="clinical_note", entity_id=UUID(note_id),
            title="PRIVATE IMPORT TITLE", description="PRIVATE IMPORT CONTENT", actor_id=None,
            occurred_at=occurred_at, recorded_at=occurred_at, source=source,
            metadata={"type": "diagnosis", "status": "draft", "is_alert": True},
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_legacy_note_references_require_canonical_patient_clinic_and_draft_access(client_and_db):
    client, sessions = client_and_db
    manager = await register(client, "legacy-ref-manager@example.com")
    manager_headers = {"Authorization": f"Bearer {manager['access_token']}"}
    doctor_headers = await invite_doctor(client, manager_headers, "legacy-ref-author@example.com")
    other_clinic = await register(client, "legacy-ref-other-clinic@example.com", "Other Clinic")
    other_clinic_headers = {"Authorization": f"Bearer {other_clinic['access_token']}"}

    patient_a = (await client.post("/api/patients", headers=manager_headers, json={
        "first_name": "A", "last_name": "Reference",
    })).json()
    patient_b = (await client.post("/api/patients", headers=manager_headers, json={
        "first_name": "B", "last_name": "Reference",
    })).json()
    draft = await client.post("/api/clinical-notes", headers=doctor_headers, json={
        "patient_id": patient_a["id"], "note_type": "diagnosis",
        "title": "PRIVATE TITLE", "content": "PRIVATE CONTENT",
    })
    assert draft.status_code == 201, draft.text
    valid_note_b = await client.post("/api/clinical-notes", headers=manager_headers, json={
        "patient_id": patient_b["id"], "note_type": "finding", "content": "Fictitious valid note",
    })
    assert valid_note_b.status_code == 201, valid_note_b.text
    finalized_b = await client.post(
        f"/api/clinical-notes/{valid_note_b.json()['id']}/finalize", headers=manager_headers,
    )
    assert finalized_b.status_code == 200, finalized_b.text
    foreign_patient = (await client.post("/api/patients", headers=other_clinic_headers, json={
        "first_name": "Foreign", "last_name": "Patient",
    })).json()
    foreign_note = await client.post("/api/clinical-notes", headers=other_clinic_headers, json={
        "patient_id": foreign_patient["id"], "note_type": "note", "content": "Foreign clinic note",
    })
    assert foreign_note.status_code == 201, foreign_note.text

    await _add_legacy_note_event(
        sessions, clinic_id=manager["clinic_id"], patient_id=patient_a["id"],
        note_id=draft.json()["id"], event_id=uuid4(), occurred_at=datetime(2032, 1, 1, tzinfo=UTC),
        source=None,
    )
    await _add_legacy_note_event(
        sessions, clinic_id=manager["clinic_id"], patient_id=patient_b["id"],
        note_id=draft.json()["id"], event_id=uuid4(), occurred_at=datetime(2032, 1, 2, tzinfo=UTC),
    )
    await _add_legacy_note_event(
        sessions, clinic_id=manager["clinic_id"], patient_id=patient_b["id"],
        note_id=str(uuid4()), event_id=uuid4(), occurred_at=datetime(2032, 1, 3, tzinfo=UTC),
    )
    await _add_legacy_note_event(
        sessions, clinic_id=manager["clinic_id"], patient_id=patient_b["id"],
        note_id=foreign_note.json()["id"], event_id=uuid4(), occurred_at=datetime(2032, 1, 4, tzinfo=UTC),
    )

    timeline_a_url = f"/api/clinical-notes/patients/{patient_a['id']}/timeline"
    timeline_b_url = f"/api/clinical-notes/patients/{patient_b['id']}/timeline"
    author_timeline = (await client.get(timeline_a_url, headers=doctor_headers)).json()
    manager_timeline_a = (await client.get(timeline_a_url, headers=manager_headers)).json()
    author_record_a = await client.get(
        f"/api/clinical-notes/patients/{patient_a['id']}/record", headers=doctor_headers,
    )
    manager_record_a = await client.get(
        f"/api/clinical-notes/patients/{patient_a['id']}/record", headers=manager_headers,
    )
    manager_timeline_b = await client.get(timeline_b_url, headers=manager_headers)
    manager_record_b = await client.get(
        f"/api/clinical-notes/patients/{patient_b['id']}/record", headers=manager_headers,
    )

    assert any(
        event["entity_id"] == draft.json()["id"] and event["source"] is None
        for event in author_timeline
    )
    assert author_record_a.status_code == 200, author_record_a.text
    assert any(
        event["entity_id"] == draft.json()["id"] and event["source"] is None
        for event in author_record_a.json()["timeline"]
    )
    assert not any(event["entity_id"] == draft.json()["id"] for event in manager_timeline_a)
    assert manager_record_a.status_code == 200, manager_record_a.text
    assert not any(
        event["entity_id"] == draft.json()["id"]
        for event in manager_record_a.json()["timeline"]
    )
    assert manager_timeline_b.status_code == 200, manager_timeline_b.text
    assert manager_record_b.status_code == 200, manager_record_b.text
    assert not any(event["entity_id"] == draft.json()["id"] for event in manager_timeline_b.json())
    assert not any(
        event["entity_id"] in {draft.json()["id"], foreign_note.json()["id"]}
        for event in manager_timeline_b.json()
    )
    assert not any(
        event["entity_id"] in {draft.json()["id"], foreign_note.json()["id"]}
        for event in manager_record_b.json()["timeline"]
    )

    # Invalid references must be removed before pagination, leaving valid events in the first page.
    paged = await client.get(timeline_b_url, headers=manager_headers, params={
        "event_type": "clinical_note", "limit": 1, "offset": 0,
    })
    assert paged.status_code == 200, paged.text
    assert len(paged.json()) == 1
    assert paged.json()[0]["entity_id"] == valid_note_b.json()["id"]


@pytest.mark.asyncio
async def test_legacy_appointment_creation_recovers_original_time_and_has_stable_fallback(client_and_db):
    client, sessions = client_and_db
    manager = await register(client, "legacy-appointment-manager@example.com")
    headers = {"Authorization": f"Bearer {manager['access_token']}"}
    me = (await client.get("/api/auth/me", headers=headers)).json()
    patient = (await client.post("/api/patients", headers=headers, json={
        "first_name": "History", "last_name": "Patient",
    })).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "History room"})).json()
    appointment = await _create_timeline_appointment(
        client, headers, me["id"], patient["id"], room["id"], "2031-01-06T09:00:00+00:00",
    )
    async with sessions() as session:
        created = await session.scalar(select(AppointmentHistory).where(
            AppointmentHistory.appointment_id == UUID(appointment["id"]),
            AppointmentHistory.action == "created",
        ))
        created.details = {}
        created.occurred_at = datetime(2030, 12, 1, 12, 0, tzinfo=UTC)
        await session.commit()

    timeline_url = f"/api/clinical-notes/patients/{patient['id']}/timeline"
    date_filter = {"start_date": "2031-01-06T00:00:00+00:00", "end_date": "2031-01-06T23:59:59+00:00"}
    initial = (await client.get(timeline_url, headers=headers)).json()
    creation_before = next(event for event in initial if event["source"] == "appointment_history")
    assert creation_before["occurred_at"].startswith("2031-01-06T09:00:00")
    assert len((await client.get(timeline_url, headers=headers, params=date_filter)).json()) == 1

    await client.post(f"/api/appointments/{appointment['id']}/reschedule", headers=headers, json={
        "starts_at": "2031-01-07T09:00:00+00:00", "reason": "Fictitious reschedule",
    })
    after_first = (await client.get(timeline_url, headers=headers)).json()
    first_creation = next(event for event in after_first if event["id"] == creation_before["id"])
    assert first_creation["occurred_at"].startswith("2031-01-06T09:00:00")
    assert len((await client.get(timeline_url, headers=headers, params=date_filter)).json()) == 1

    await client.post(f"/api/appointments/{appointment['id']}/reschedule", headers=headers, json={
        "starts_at": "2031-01-08T09:00:00+00:00", "reason": "Second fictitious reschedule",
    })
    after_second = (await client.get(timeline_url, headers=headers)).json()
    second_creation = next(event for event in after_second if event["id"] == creation_before["id"])
    assert second_creation["occurred_at"].startswith("2031-01-06T09:00:00")
    assert second_creation["id"] == creation_before["id"]
    assert len((await client.get(timeline_url, headers=headers, params=date_filter)).json()) == 1

    second_appointment = await _create_timeline_appointment(
        client, headers, me["id"], patient["id"], room["id"], "2035-04-05T09:00:00+00:00",
    )
    async with sessions() as session:
        no_snapshot = await session.scalar(select(AppointmentHistory).where(
            AppointmentHistory.appointment_id == UUID(second_appointment["id"]),
            AppointmentHistory.action == "created",
        ))
        no_snapshot.details = {}
        no_snapshot.occurred_at = datetime(2030, 12, 2, 12, 0, tzinfo=UTC)
        await session.commit()
    fallback_events = (await client.get(timeline_url, headers=headers)).json()
    fallback_creation = next(
        event for event in fallback_events
        if event["source"] == "appointment_history" and event["entity_id"] == second_appointment["id"]
    )
    assert fallback_creation["occurred_at"].startswith("2030-12-02T12:00:00")
    fallback_repeated = (await client.get(timeline_url, headers=headers)).json()
    repeated_fallback = next(
        event for event in fallback_repeated
        if event["source"] == "appointment_history" and event["entity_id"] == second_appointment["id"]
    )
    assert (repeated_fallback["id"], repeated_fallback["occurred_at"]) == (
        fallback_creation["id"], fallback_creation["occurred_at"],
    )

    current_snapshot_appointment = await _create_timeline_appointment(
        client, headers, me["id"], patient["id"], room["id"], "2036-05-06T09:00:00+00:00",
    )
    snapshot_before = (await client.get(timeline_url, headers=headers)).json()
    snapshot_creation = next(
        event for event in snapshot_before
        if event["source"] == "appointment_history"
        and event["entity_id"] == current_snapshot_appointment["id"]
    )
    assert snapshot_creation["occurred_at"].startswith("2036-05-06T09:00:00")
    await client.post(
        f"/api/appointments/{current_snapshot_appointment['id']}/reschedule", headers=headers, json={
            "starts_at": "2036-05-07T09:00:00+00:00", "reason": "Snapshot remains historical",
        },
    )
    snapshot_after = (await client.get(timeline_url, headers=headers)).json()
    repeated_snapshot = next(event for event in snapshot_after if event["id"] == snapshot_creation["id"])
    assert repeated_snapshot["occurred_at"] == snapshot_creation["occurred_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_version_notes", [False, True])
async def test_note_finalization_is_single_stable_event_with_safe_distinct_actions(
    client_and_db, with_version_notes,
):
    client, _sessions = client_and_db
    manager = await register(client, f"note-actions-{with_version_notes}@example.com")
    headers = {"Authorization": f"Bearer {manager['access_token']}"}
    patient = (await client.post("/api/patients", headers=headers, json={
        "first_name": "Action", "last_name": "Patient",
    })).json()
    note_response = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient["id"], "note_type": "finding",
        "title": "PRIVATE TITLE", "content": "PRIVATE CONTENT",
    })
    assert note_response.status_code == 201, note_response.text
    note = note_response.json()
    if with_version_notes:
        correction = await client.patch(f"/api/clinical-notes/{note['id']}", headers=headers, json={
            "title": "PRIVATE CORRECTED TITLE", "content": "PRIVATE CORRECTED CONTENT",
        })
        assert correction.status_code == 200, correction.text
    finalize_payload = {"version_notes": "PRIVATE FINALIZATION REASON"} if with_version_notes else {}
    finalized = await client.post(
        f"/api/clinical-notes/{note['id']}/finalize", headers=headers, json=finalize_payload,
    )
    assert finalized.status_code == 200, finalized.text
    if with_version_notes:
        addendum = await client.post(f"/api/clinical-notes/{note['id']}/addendum", headers=headers, json={
            "addendum_content": "PRIVATE ADDENDUM", "reason": "PRIVATE ADDENDUM REASON",
        })
        assert addendum.status_code == 200, addendum.text

    timeline_url = f"/api/clinical-notes/patients/{patient['id']}/timeline"
    timeline_first = await client.get(timeline_url, headers=headers)
    record_first = await client.get(f"/api/clinical-notes/patients/{patient['id']}/record", headers=headers)
    assert timeline_first.status_code == 200, timeline_first.text
    assert record_first.status_code == 200, record_first.text
    note_events = [event for event in timeline_first.json() if event["entity_id"] == note["id"]]
    created = [event for event in note_events if event["metadata"].get("action") == "created"]
    finalized_events = [event for event in note_events if event["metadata"].get("action") == "finalized"]
    addendum_events = [event for event in note_events if event["metadata"].get("action") == "addendum"]
    correction_events = [event for event in note_events if event["metadata"].get("action") == "corrected"]
    assert len(created) == 1
    assert created[0]["metadata"].get("status") == "draft"
    assert len(finalized_events) == 1
    assert finalized_events[0]["title"] == "Clinical note finalized"
    assert finalized_events[0]["description"] == "Clinical note finalized"
    assert len(addendum_events) == (1 if with_version_notes else 0)
    assert len(correction_events) == (2 if with_version_notes else 0)
    assert all(event["title"] == "Clinical note created" for event in created)
    assert all(event["title"] == "Clinical note corrected" for event in correction_events)
    assert not any("PRIVATE" in event["title"] + event["description"] + str(event["metadata"]) for event in note_events)

    if with_version_notes:
        revisions = (await client.get(f"/api/clinical-notes/{note['id']}/revisions", headers=headers)).json()
        assert any(revision["field"] == "status" and revision["new_value"] == "finalized" for revision in revisions)
        assert any(revision["field"] == "content" for revision in revisions)
        assert addendum_events[0]["title"] == "Clinical note addendum added"
    else:
        assert len(note_events) == 2

    record_events = [event for event in record_first.json()["timeline"] if event["entity_id"] == note["id"]]
    assert [(event["id"], event["title"]) for event in record_events] == [
        (event["id"], event["title"]) for event in note_events
    ]
    await client.post("/api/clinical-notes/timeline/refresh", headers=headers)
    timeline_second = (await client.get(timeline_url, headers=headers)).json()
    assert [(event["id"], event["title"]) for event in timeline_second if event["entity_id"] == note["id"]] == [
        (event["id"], event["title"]) for event in note_events
    ]
