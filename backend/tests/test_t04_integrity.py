"""HTTP regressions for T04 patient association and visit closure integrity."""

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    Appointment,
    AuditEvent,
    Base,
    ClinicalNote,
    PerformedProcedure,
    PerformedProcedureRevision,
    ProcedureExecutionSegment,
    get_db,
)
from app.main import app


@pytest.fixture
async def integrity_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    await engine.dispose()


async def _visit(client, *, closed=False):
    tokens = await client.post("/api/auth/register", json={
        "clinic_name": f"Integrity {uuid4().hex[:8]}",
        "full_name": "Integrity Manager",
        "email": f"manager-{uuid4().hex}@example.com",
        "password": "correct horse battery staple",
        "role": "clinic_manager",
    })
    assert tokens.status_code == 201, tokens.text
    headers = {"Authorization": f"Bearer {tokens.json()['access_token']}"}
    patient_a = await client.post("/api/patients", headers=headers, json={"first_name": "A", "last_name": "Patient"})
    patient_b = await client.post("/api/patients", headers=headers, json={"first_name": "B", "last_name": "Patient"})
    room = await client.post("/api/rooms", headers=headers, json={"name": "Room"})
    me = await client.get("/api/auth/me", headers=headers)
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_a.json()["id"],
        "doctor_id": me.json()["id"],
        "room_id": room.json()["id"],
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation",
    })
    assert appointment.status_code == 201, appointment.text
    if closed:
        cancelled = await client.patch(
            f"/api/appointments/{appointment.json()['id']}/status",
            headers=headers,
            json={"status": "cancelled"},
        )
        assert cancelled.status_code == 200, cancelled.text
    return headers, patient_a.json()["id"], patient_b.json()["id"], appointment.json()["id"]


async def _add_note(client, headers, patient_id, appointment_id):
    response = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "appointment_id": appointment_id,
        "note_type": "note",
        "content": "Fictitious test note",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _add_procedure(client, headers, appointment_id):
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": f"TEST_{uuid4().hex[:8]}",
        "name": "Test procedure",
        "base_price": "10.00",
        "currency": "RON",
        "default_duration_minutes": 15,
    })
    assert catalog.status_code == 201, catalog.text
    response = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog.json()["id"],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("clinical_data", ["note", "procedure", "both"])
async def test_patient_reassignment_blocked_after_any_clinical_data(integrity_context, clinical_data):
    client, sessions = integrity_context
    headers, patient_a, patient_b, appointment_id = await _visit(client)
    note_id = None
    procedure_id = None
    if clinical_data in {"note", "both"}:
        note_id = await _add_note(client, headers, patient_a, appointment_id)
        finalized = await client.post(f"/api/clinical-notes/{note_id}/finalize", headers=headers)
        assert finalized.status_code == 200, finalized.text
    if clinical_data in {"procedure", "both"}:
        procedure_id = await _add_procedure(client, headers, appointment_id)
        cancelled = await client.post(
            f"/api/performed-procedures/{procedure_id}/cancel",
            headers=headers,
            json={"transition": "cancel", "notes": "Explicit test resolution"},
        )
        assert cancelled.status_code == 200, cancelled.text

    response = await client.patch(
        f"/api/appointments/{appointment_id}", headers=headers, json={"patient_id": patient_b}
    )
    assert response.status_code == 409
    assert "Fictitious test note" not in response.text

    same_patient = await client.patch(
        f"/api/appointments/{appointment_id}",
        headers=headers,
        json={"patient_id": patient_a, "appointment_type": "Updated visit type"},
    )
    assert same_patient.status_code == 200, same_patient.text
    assert same_patient.json()["appointment_type"] == "Updated visit type"

    async with sessions() as session:
        appointment = await session.get(Appointment, UUID(appointment_id))
        assert str(appointment.patient_id) == patient_a
        if note_id:
            note = await session.get(ClinicalNote, UUID(note_id))
            assert str(note.patient_id) == patient_a
            assert str(note.appointment_id) == appointment_id
        if procedure_id:
            procedure = await session.get(PerformedProcedure, UUID(procedure_id))
            assert str(procedure.patient_id) == patient_a
            assert str(procedure.appointment_id) == appointment_id


@pytest.mark.asyncio
async def test_reassignment_without_clinical_data_and_legitimate_patch_work(integrity_context):
    client, _sessions = integrity_context
    headers, _patient_a, patient_b, appointment_id = await _visit(client)
    response = await client.patch(
        f"/api/appointments/{appointment_id}", headers=headers, json={"patient_id": patient_b}
    )
    assert response.status_code == 200, response.text
    same_patient = await client.patch(
        f"/api/appointments/{appointment_id}",
        headers=headers,
        json={"patient_id": patient_b, "appointment_type": "Follow-up"},
    )
    assert same_patient.status_code == 200, same_patient.text
    assert same_patient.json()["patient_id"] == patient_b
    assert same_patient.json()["appointment_type"] == "Follow-up"


@pytest.mark.asyncio
@pytest.mark.parametrize("procedure_status", ["pending", "running", "paused"])
@pytest.mark.parametrize("closure", ["completed", "cancelled", "no_show"])
async def test_unresolved_procedure_blocks_every_visit_closure(integrity_context, procedure_status, closure):
    client, _sessions = integrity_context
    headers, _patient_a, _patient_b, appointment_id = await _visit(client)
    appointment_path = ["confirmed", "arrived"]
    if closure != "no_show":
        appointment_path.append("in_progress")
    for status in appointment_path:
        response = await client.patch(f"/api/appointments/{appointment_id}/status", headers=headers, json={"status": status})
        assert response.status_code == 200, response.text
    procedure_id = await _add_procedure(client, headers, appointment_id)
    if procedure_status in {"running", "paused"}:
        response = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        assert response.status_code == 200, response.text
    if procedure_status == "paused":
        response = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        assert response.status_code == 200, response.text

    blocked = await client.patch(f"/api/appointments/{appointment_id}/status", headers=headers, json={"status": closure})
    assert blocked.status_code == 409
    current = await client.get(f"/api/appointments/{appointment_id}", headers=headers)
    assert current.json()["status"] == ("arrived" if closure == "no_show" else "in_progress")


@pytest.mark.asyncio
async def test_explicit_resolution_allows_cancelled_visit_closure(integrity_context):
    client, _sessions = integrity_context
    headers, _patient_a, _patient_b, appointment_id = await _visit(client)
    for status in ["confirmed", "arrived", "in_progress"]:
        response = await client.patch(f"/api/appointments/{appointment_id}/status", headers=headers, json={"status": status})
        assert response.status_code == 200, response.text
    pending_id = await _add_procedure(client, headers, appointment_id)
    cancelled = await client.post(
        f"/api/performed-procedures/{pending_id}/cancel", headers=headers,
        json={"transition": "cancel", "notes": "Resolved explicitly"},
    )
    assert cancelled.status_code == 200, cancelled.text
    response = await client.patch(f"/api/appointments/{appointment_id}/status", headers=headers, json={"status": "cancelled"})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,status", [
    ("start", "pending"),
    ("pause", "running"),
    ("resume", "paused"),
    ("complete", "running"),
    ("cancel", "running"),
])
@pytest.mark.parametrize("closed_status", ["completed", "cancelled", "no_show"])
async def test_execution_mutations_rejected_for_closed_visit_without_history_changes(
    integrity_context, operation, status, closed_status
):
    client, sessions = integrity_context
    headers, _patient_a, _patient_b, appointment_id = await _visit(client)
    procedure_id = await _add_procedure(client, headers, appointment_id)
    if status in {"running", "paused"}:
        response = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        assert response.status_code == 200, response.text
    if status == "paused":
        response = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        assert response.status_code == 200, response.text
    # Model a pre-existing inconsistent record; the production code must not repair it.
    async with sessions() as session:
        appointment = await session.get(Appointment, UUID(appointment_id))
        appointment.status = closed_status
        await session.commit()

    async with sessions() as session:
        proc = await session.get(PerformedProcedure, UUID(procedure_id))
        before = (proc.status, proc.updated_at)
        procedure_uuid = UUID(procedure_id)
        segments_before = list((await session.scalars(select(ProcedureExecutionSegment).where(ProcedureExecutionSegment.performed_procedure_id == procedure_uuid))).all())
        revisions_before = list((await session.scalars(select(PerformedProcedureRevision).where(PerformedProcedureRevision.performed_procedure_id == procedure_uuid))).all())
        events_before = list((await session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "performed_procedure", AuditEvent.entity_id == procedure_uuid))).all())
        segment_snapshot = [(s.id, s.segment_type, s.started_at, s.ended_at, s.duration_seconds) for s in segments_before]
        revision_ids = [r.id for r in revisions_before]
        event_ids = [e.id for e in events_before]

    payload = {"transition": "cancel", "notes": "test"} if operation == "cancel" else None
    request_args = {"headers": headers}
    if payload is not None:
        request_args["json"] = payload
    response = await client.post(
        f"/api/performed-procedures/{procedure_id}/{operation}", **request_args
    )
    assert response.status_code == 409
    async with sessions() as session:
        proc = await session.get(PerformedProcedure, UUID(procedure_id))
        assert (proc.status, proc.updated_at) == before
        segments_after = list((await session.scalars(select(ProcedureExecutionSegment).where(ProcedureExecutionSegment.performed_procedure_id == UUID(procedure_id)))).all())
        assert [(s.id, s.segment_type, s.started_at, s.ended_at, s.duration_seconds) for s in segments_after] == segment_snapshot
        revisions_after = list((await session.scalars(select(PerformedProcedureRevision.id).where(PerformedProcedureRevision.performed_procedure_id == UUID(procedure_id)))).all())
        events_after = list((await session.scalars(select(AuditEvent.id).where(AuditEvent.entity_type == "performed_procedure", AuditEvent.entity_id == UUID(procedure_id)))).all())
        assert revisions_after == revision_ids
        assert events_after == event_ids
