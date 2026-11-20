"""HTTP contract tests for performed procedure timer transitions."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import AuditEvent, Base, get_db
from app.main import app


async def register(client):
    response = await client.post(
        "/api/auth/register",
        json={
            "clinic_name": "Timer Clinic",
            "full_name": "Timer User",
            "email": "timer@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    return response.json()


async def create_procedure(client, headers):
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    patient = await client.post(
        "/api/patients", headers=headers, json={"first_name": "Test", "last_name": "Patient"}
    )
    catalog = await client.post(
        "/api/procedures",
        headers=headers,
        json={
            "code": "TIMER",
            "name": "Timer test",
            "base_price": "10.00",
            "default_duration_minutes": 20,
        },
    )
    doctor = await client.get("/api/auth/me", headers=headers)
    appointment = await client.post(
        "/api/appointments",
        headers=headers,
        json={
            "patient_id": patient.json()["id"],
            "doctor_id": doctor.json()["id"],
            "room_id": room.json()["id"],
            "starts_at": "2026-10-01T10:00:00+00:00",
            "duration_minutes": 60,
            "appointment_type": "Consultation",
        },
    )
    procedure = await client.post(
        "/api/performed-procedures",
        headers=headers,
        json={"appointment_id": appointment.json()["id"], "procedure_catalog_id": catalog.json()["id"]},
    )
    assert procedure.status_code == 201
    return procedure.json()["id"]


@pytest.fixture
async def procedure_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            tokens = await register(client)
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            procedure_id = await create_procedure(client, headers)
            yield client, headers, procedure_id, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def segments(client, headers, procedure_id):
    response = await client.get(f"/api/performed-procedures/{procedure_id}/segments", headers=headers)
    assert response.status_code == 200
    return response.json()


async def test_resume_pending_is_rejected_without_mutations(procedure_context):
    client, headers, procedure_id, sessions = procedure_context
    response = await client.post(f"/api/performed-procedures/{procedure_id}/resume", headers=headers)
    assert response.status_code == 409
    assert (await client.get(f"/api/performed-procedures/{procedure_id}", headers=headers)).json()["status"] == "pending"
    assert await segments(client, headers, procedure_id) == []
    async with sessions() as session:
        assert await session.scalar(
            select(AuditEvent.id).where(AuditEvent.entity_id == procedure_id, AuditEvent.action == "resumed")
        ) is None


async def test_start_paused_is_rejected_without_mutations(procedure_context):
    client, headers, procedure_id, sessions = procedure_context
    started = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert started.status_code == 200
    paused = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
    assert paused.status_code == 200
    before_segments = await segments(client, headers, procedure_id)
    async with sessions() as session:
        before_audit = list(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id == procedure_id)
            )
        )

    response = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == "Cannot start procedure from status: paused"
    assert (await client.get(f"/api/performed-procedures/{procedure_id}", headers=headers)).json()["status"] == "paused"
    assert await segments(client, headers, procedure_id) == before_segments
    async with sessions() as session:
        after_audit = list(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.entity_id == procedure_id)
            )
        )
    assert after_audit == before_audit


async def test_valid_timer_flow_closes_segments_and_excludes_pause(procedure_context):
    client, headers, procedure_id, _sessions = procedure_context
    from app.performed_procedures import router as timer_router

    ticks = iter(
        [
            timer_router.datetime(2026, 1, 1, 0, 0, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 0, 10, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 0, 10, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 1, 10, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 1, 10, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 1, 40, tzinfo=timer_router.UTC),
            timer_router.datetime(2026, 1, 1, 0, 1, 40, tzinfo=timer_router.UTC),
        ]
    )
    with patch.object(timer_router, "utcnow", side_effect=lambda: next(ticks)):
        started = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        assert started.status_code == 200
        repeated_start = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        assert repeated_start.status_code == 409
        assert len(await segments(client, headers, procedure_id)) == 1
        paused = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        assert paused.status_code == 200
        resumed = await client.post(f"/api/performed-procedures/{procedure_id}/resume", headers=headers)
        assert resumed.status_code == 200
        repeated_resume = await client.post(f"/api/performed-procedures/{procedure_id}/resume", headers=headers)
        assert repeated_resume.status_code == 409
        assert len(await segments(client, headers, procedure_id)) == 3
        completed = await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
        assert completed.status_code == 200

    timer_segments = await segments(client, headers, procedure_id)
    assert [segment["segment_type"] for segment in timer_segments] == ["run", "pause", "run"]
    assert all(segment["ended_at"] is not None for segment in timer_segments)
    assert [segment["duration_seconds"] for segment in timer_segments] == [10, 60, 30]
    assert completed.json()["total_duration_seconds"] == 40


@pytest.mark.parametrize("terminal,source_status", [("complete", "running"), ("cancel", "running"), ("cancel", "paused")])
async def test_terminal_transition_closes_active_segment(procedure_context, terminal, source_status):
    client, headers, procedure_id, _sessions = procedure_context
    started = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert started.status_code == 200
    if source_status == "paused":
        paused = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        assert paused.status_code == 200
    response = await client.post(
        f"/api/performed-procedures/{procedure_id}/{terminal}",
        headers=headers,
        json={"transition": "cancel"} if terminal == "cancel" else None,
    )
    assert response.status_code == 200
    assert all(segment["ended_at"] is not None for segment in await segments(client, headers, procedure_id))
