from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, PatientTimelineEvent, get_db
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
