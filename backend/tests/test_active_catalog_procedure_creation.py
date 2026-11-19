"""HTTP contract tests for creating procedures from active catalog entries."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    AuditEvent,
    Base,
    PerformedProcedure,
    PerformedProcedureRevision,
    ProcedureExecutionSegment,
    get_db,
)
from app.main import app


@pytest.fixture
async def api_context():
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
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def register_clinic(client, clinic_name, email):
    response = await client.post(
        "/api/auth/register",
        json={
            "clinic_name": clinic_name,
            "full_name": "Clinic Manager",
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_clinic_context(client, headers, code="EXAM-01"):
    room = await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})
    assert room.status_code == 201
    patient = await client.post(
        "/api/patients", headers=headers, json={"first_name": "Fictitious", "last_name": "Patient"}
    )
    assert patient.status_code == 201
    catalog = await client.post(
        "/api/procedures",
        headers=headers,
        json={
            "code": code,
            "name": "Dental examination",
            "default_duration_minutes": 25,
            "base_price": "120.00",
            "currency": "RON",
        },
    )
    assert catalog.status_code == 201
    doctor = await client.get("/api/auth/me", headers=headers)
    appointment = await client.post(
        "/api/appointments",
        headers=headers,
        json={
            "patient_id": patient.json()["id"],
            "doctor_id": doctor.json()["id"],
            "room_id": room.json()["id"],
            "starts_at": "2027-01-04T10:00:00+00:00",
            "duration_minutes": 60,
            "appointment_type": "Consultation",
        },
    )
    assert appointment.status_code == 201
    return {
        "patient_id": patient.json()["id"],
        "catalog_id": catalog.json()["id"],
        "appointment_id": appointment.json()["id"],
        "catalog": catalog.json(),
    }


async def create_performed_procedure(client, headers, appointment_id, catalog_id, tooth_fdi="16"):
    return await client.post(
        "/api/performed-procedures",
        headers=headers,
        json={
            "appointment_id": appointment_id,
            "procedure_catalog_id": catalog_id,
            "tooth_fdi": tooth_fdi,
        },
    )


async def procedure_side_effect_counts(sessions, clinic_id):
    async with sessions() as session:
        procedures = await session.scalar(
            select(func.count()).select_from(PerformedProcedure).where(
                PerformedProcedure.clinic_id == clinic_id
            )
        )
        segments = await session.scalar(
            select(func.count())
            .select_from(ProcedureExecutionSegment)
            .join(PerformedProcedure, PerformedProcedure.id == ProcedureExecutionSegment.performed_procedure_id)
            .where(PerformedProcedure.clinic_id == clinic_id)
        )
        audits = await session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.clinic_id == clinic_id,
                AuditEvent.entity_type == "performed_procedure",
            )
        )
        revisions = await session.scalar(
            select(func.count()).select_from(PerformedProcedureRevision).where(
                PerformedProcedureRevision.clinic_id == clinic_id
            )
        )
        return procedures, segments, audits, revisions


async def test_active_catalog_creation_snapshots_values_without_starting_timer(api_context):
    client, _sessions = api_context
    headers = await register_clinic(client, "Active Catalog Clinic", "active@example.com")
    context = await create_clinic_context(client, headers)

    created = await create_performed_procedure(
        client, headers, context["appointment_id"], context["catalog_id"]
    )
    assert created.status_code == 201
    data = created.json()
    assert data["status"] == "pending"
    assert data["procedure_code"] == "EXAM-01"
    assert data["procedure_name"] == "Dental examination"
    assert data["procedure_base_price"] == "120.00"
    assert data["procedure_currency"] == "RON"
    assert data["procedure_default_duration"] == 25
    segments = await client.get(
        f"/api/performed-procedures/{data['id']}/segments", headers=headers
    )
    assert segments.status_code == 200
    assert segments.json() == []


async def test_deactivation_blocks_new_creation_but_preserves_existing_snapshot_and_reactivation(
    api_context,
):
    client, sessions = api_context
    headers = await register_clinic(client, "Catalog Lifecycle Clinic", "lifecycle@example.com")
    context = await create_clinic_context(client, headers)
    original = await create_performed_procedure(
        client, headers, context["appointment_id"], context["catalog_id"]
    )
    assert original.status_code == 201
    original_data = original.json()

    deactivated = await client.post(
        f"/api/procedures/{context['catalog_id']}/deactivate", headers=headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    catalog_update = await client.patch(
        f"/api/procedures/{context['catalog_id']}",
        headers=headers,
        json={
            "code": "EXAM-UPDATED",
            "name": "Updated examination",
            "default_duration_minutes": 40,
            "base_price": "150.00",
            "currency": "RON",
        },
    )
    assert catalog_update.status_code == 200

    before_rejected_create = await procedure_side_effect_counts(sessions, original_data["clinic_id"])
    rejected = await create_performed_procedure(
        client, headers, context["appointment_id"], context["catalog_id"]
    )
    assert rejected.status_code == 409
    assert "inactive" in rejected.json()["detail"].lower()
    assert await procedure_side_effect_counts(sessions, original_data["clinic_id"]) == before_rejected_create

    readable = await client.get(
        f"/api/performed-procedures/{original_data['id']}", headers=headers
    )
    assert readable.status_code == 200
    for field in (
        "procedure_code",
        "procedure_name",
        "procedure_base_price",
        "procedure_currency",
        "procedure_default_duration",
        "current_price",
    ):
        assert readable.json()[field] == original_data[field]
    assert readable.json()["status"] == "pending"

    segments_before_start = await client.get(
        f"/api/performed-procedures/{original_data['id']}/segments", headers=headers
    )
    assert segments_before_start.json() == []
    started = await client.post(
        f"/api/performed-procedures/{original_data['id']}/start", headers=headers
    )
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    active_segments = await client.get(
        f"/api/performed-procedures/{original_data['id']}/segments", headers=headers
    )
    assert len(active_segments.json()) == 1
    assert active_segments.json()[0]["segment_type"] == "run"
    assert active_segments.json()[0]["ended_at"] is None

    activated = await client.post(
        f"/api/procedures/{context['catalog_id']}/activate", headers=headers
    )
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True
    reactivated_create = await create_performed_procedure(
        client, headers, context["appointment_id"], context["catalog_id"]
    )
    assert reactivated_create.status_code == 201
    assert reactivated_create.json()["id"] != original_data["id"]
    assert reactivated_create.json()["tooth_fdi"] == original_data["tooth_fdi"]
    assert reactivated_create.json()["procedure_code"] == "EXAM-UPDATED"
    assert reactivated_create.json()["procedure_name"] == "Updated examination"
    assert reactivated_create.json()["procedure_base_price"] == "150.00"
    assert reactivated_create.json()["procedure_currency"] == "RON"
    assert reactivated_create.json()["procedure_default_duration"] == 40

    completed = await client.post(
        f"/api/performed-procedures/{original_data['id']}/complete", headers=headers
    )
    assert completed.status_code == 200


async def test_foreign_inactive_catalog_id_is_not_disclosed_or_used(api_context):
    client, sessions = api_context
    owner_headers = await register_clinic(client, "Catalog Owner Clinic", "owner@example.com")
    owner_context = await create_clinic_context(client, owner_headers, code="OWNER-01")
    deactivated = await client.post(
        f"/api/procedures/{owner_context['catalog_id']}/deactivate", headers=owner_headers
    )
    assert deactivated.status_code == 200

    other_headers = await register_clinic(client, "Other Clinic", "other@example.com")
    other_context = await create_clinic_context(client, other_headers, code="OTHER-01")
    other_me = await client.get("/api/auth/me", headers=other_headers)
    other_clinic_id = other_me.json()["clinic_id"]
    before = await procedure_side_effect_counts(sessions, other_clinic_id)

    response = await create_performed_procedure(
        client,
        other_headers,
        other_context["appointment_id"],
        owner_context["catalog_id"],
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Procedure catalog not found or access denied"
    assert "inactive" not in response.json()["detail"].lower()
    assert await procedure_side_effect_counts(sessions, other_clinic_id) == before
