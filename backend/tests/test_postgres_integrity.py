import asyncio
import os
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import Appointment, Room, User, get_db
from app.main import app


@pytest.fixture
async def postgres_context():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(url, pool_pre_ping=True)
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


async def create_resources(client: AsyncClient, sessions: async_sessionmaker):
    registration = await client.post(
        "/api/auth/register",
        json={
            "clinic_name": f"Concurrency Clinic {uuid4().hex[:8]}",
            "full_name": "Concurrency Manager",
            "email": f"manager-{uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert registration.status_code == 201
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    manager = (await client.get("/api/auth/me", headers=headers)).json()
    async with sessions() as session:
        users = [
            User(clinic_id=manager["clinic_id"], full_name=f"Doctor {index}", email=f"doctor-{uuid4().hex}@example.com", password_hash=hash_password("correct horse battery staple"), role="doctor", is_active=True)
            for index in range(3)
        ]
        assistants = [
            User(clinic_id=manager["clinic_id"], full_name=f"Assistant {index}", email=f"assistant-{uuid4().hex}@example.com", password_hash=hash_password("correct horse battery staple"), role="assistant", is_active=True)
            for index in range(2)
        ]
        rooms = [Room(clinic_id=manager["clinic_id"], name=f"Room {index}", is_active=True) for index in range(6)]
        session.add_all(users + assistants + rooms)
        await session.commit()
        for entity in users + assistants + rooms:
            await session.refresh(entity)
    return headers, users, assistants, rooms


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_concurrent_doctor_room_and_assistant_conflicts(postgres_context):
    client, sessions = postgres_context
    headers, doctors, assistants, rooms = await create_resources(client, sessions)
    patients = []
    for index in range(6):
        response = await client.post("/api/patients", headers=headers, json={"first_name": f"Patient{index}", "last_name": "Synthetic"})
        assert response.status_code == 201
        patients.append(response.json()["id"])

    async def concurrent_pair(first: dict, second: dict) -> list[int]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
            responses = await asyncio.gather(
                left.post("/api/appointments", headers=headers, json=first),
                right.post("/api/appointments", headers=headers, json=second),
            )
            return [response.status_code for response in responses]

    base = {"starts_at": "2027-01-05T10:00:00+00:00", "duration_minutes": 60, "appointment_type": "Synthetic"}
    doctor_conflict = await concurrent_pair(
        {**base, "patient_id": patients[0], "doctor_id": str(doctors[0].id), "room_id": str(rooms[0].id)},
        {**base, "patient_id": patients[1], "doctor_id": str(doctors[0].id), "room_id": str(rooms[1].id)},
    )
    room_conflict = await concurrent_pair(
        {**base, "starts_at": "2027-01-05T12:00:00+00:00", "patient_id": patients[2], "doctor_id": str(doctors[1].id), "room_id": str(rooms[2].id)},
        {**base, "starts_at": "2027-01-05T12:00:00+00:00", "patient_id": patients[3], "doctor_id": str(doctors[2].id), "room_id": str(rooms[2].id)},
    )
    assistant_conflict = await concurrent_pair(
        {**base, "starts_at": "2027-01-05T14:00:00+00:00", "patient_id": patients[4], "doctor_id": str(doctors[1].id), "room_id": str(rooms[3].id), "assistant_id": str(assistants[0].id)},
        {**base, "starts_at": "2027-01-05T14:00:00+00:00", "patient_id": patients[5], "doctor_id": str(doctors[0].id), "room_id": str(rooms[4].id), "assistant_id": str(assistants[0].id)},
    )
    assert sorted(doctor_conflict) == [201, 409]
    assert sorted(room_conflict) == [201, 409]
    assert sorted(assistant_conflict) == [201, 409]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_adjacent_bookings_and_cancellation_reactivation(postgres_context):
    client, sessions = postgres_context
    headers, doctors, _, rooms = await create_resources(client, sessions)
    patients = []
    for index in range(3):
        response = await client.post("/api/patients", headers=headers, json={"first_name": f"Adjacent{index}", "last_name": "Synthetic"})
        assert response.status_code == 201
        patients.append(response.json()["id"])
    base = {"duration_minutes": 60, "appointment_type": "Adjacent test", "doctor_id": str(doctors[0].id), "room_id": str(rooms[0].id)}
    first = await client.post("/api/appointments", headers=headers, json={**base, "patient_id": patients[0], "starts_at": "2027-01-06T10:00:00+00:00"})
    adjacent = await client.post("/api/appointments", headers=headers, json={**base, "patient_id": patients[1], "starts_at": "2027-01-06T11:00:00+00:00"})
    assert first.status_code == 201 and adjacent.status_code == 201
    cancelled = await client.patch(f"/api/appointments/{first.json()['id']}/status", headers=headers, json={"status": "cancelled"})
    assert cancelled.status_code == 200
    replacement = await client.post("/api/appointments", headers=headers, json={**base, "patient_id": patients[2], "starts_at": "2027-01-06T10:00:00+00:00"})
    assert replacement.status_code == 201
    reactivation = await client.patch(f"/api/appointments/{first.json()['id']}/status", headers=headers, json={"status": "scheduled"})
    assert reactivation.status_code == 409


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_same_time_bookings_in_different_clinics_are_independent(postgres_context):
    client, sessions = postgres_context
    first_headers, first_doctors, _, first_rooms = await create_resources(client, sessions)
    second_headers, second_doctors, _, second_rooms = await create_resources(client, sessions)
    first_patient = await client.post("/api/patients", headers=first_headers, json={"first_name": "First", "last_name": "Clinic"})
    second_patient = await client.post("/api/patients", headers=second_headers, json={"first_name": "Second", "last_name": "Clinic"})
    start = "2027-01-07T10:00:00+00:00"
    first = await client.post("/api/appointments", headers=first_headers, json={"patient_id": first_patient.json()["id"], "doctor_id": str(first_doctors[0].id), "room_id": str(first_rooms[0].id), "starts_at": start, "duration_minutes": 60, "appointment_type": "Tenant test"})
    second = await client.post("/api/appointments", headers=second_headers, json={"patient_id": second_patient.json()["id"], "doctor_id": str(second_doctors[0].id), "room_id": str(second_rooms[0].id), "starts_at": start, "duration_minutes": 60, "appointment_type": "Tenant test"})
    assert first.status_code == 201 and second.status_code == 201


def _postgres_dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _migration_database(overlapping: bool, start_revision: str = "0001_initial") -> tuple[str, str, int]:
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    assert source is not None
    name = f"dentacare_migration_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_postgres_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    target = source.rsplit("/", 1)[0] + f"/{name}"
    env = {**os.environ, "DATABASE_URL": target, "JWT_SECRET": "migration-test-secret", "ENVIRONMENT": "test"}
    await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", start_revision], cwd=os.path.dirname(__file__) + "/..", env=env, check=True, capture_output=True, text=True)
    connection = await asyncpg.connect(_postgres_dsn(target, name))
    clinic_id, doctor_id, room_id, patient_id = [str(uuid4()) for _ in range(4)]
    await connection.execute("INSERT INTO clinics (id, name, created_at) VALUES ($1, 'Legacy clinic', now())", clinic_id)
    await connection.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active) VALUES ($1, $2, $3, 'hash', 'Legacy doctor', 'doctor', true)", doctor_id, clinic_id, f"{doctor_id}@example.com")
    await connection.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Legacy room', true)", room_id, clinic_id)
    await connection.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Legacy', 'Patient', now(), now())", patient_id, clinic_id)
    if start_revision == "0002_integrity_and_history":
        await connection.execute("INSERT INTO patient_revisions (id, clinic_id, patient_id, actor_id, field, previous_value, new_value, occurred_at) VALUES ($1, $2, $3, $4, 'notes', NULL, 'legacy', now())", str(uuid4()), clinic_id, patient_id, doctor_id)
    starts = [datetime(2027, 1, 8, 10, tzinfo=UTC), datetime(2027, 1, 8, 10, 30, tzinfo=UTC)] if overlapping else [datetime(2027, 1, 8, 10, tzinfo=UTC)]
    for index, start in enumerate(starts):
        await connection.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 60, 'scheduled', 'Legacy', now(), now())", str(uuid4()), clinic_id, patient_id, doctor_id, room_id, start)
    await connection.close()
    result = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)
    return target, name, result.returncode


async def _drop_migration_database(source: str, name: str) -> None:
    admin = await asyncpg.connect(_postgres_dsn(source, "postgres"))
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_upgrade_from_0001_preserves_valid_records():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _migration_database(overlapping=False)
    try:
        assert returncode == 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0003_clinic_setup"
        assert await connection.fetchval("SELECT count(*) FROM patients") == 1
        assert await connection.fetchval("SELECT count(*) FROM patient_revisions") == 0
        assert await connection.fetchval("SELECT count(*) FROM users WHERE created_at IS NULL OR updated_at IS NULL") == 0
        assert await connection.fetchval("SELECT count(*) FROM clinics WHERE updated_at IS NULL") == 0
        await connection.close()
    finally:
        await _drop_migration_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_upgrade_from_0002_preserves_clinical_history():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _migration_database(overlapping=False, start_revision="0002_integrity_and_history")
    try:
        assert returncode == 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0003_clinic_setup"
        assert await connection.fetchval("SELECT count(*) FROM clinics") == 1
        assert await connection.fetchval("SELECT count(*) FROM users") == 1
        assert await connection.fetchval("SELECT count(*) FROM rooms") == 1
        assert await connection.fetchval("SELECT count(*) FROM patients") == 1
        assert await connection.fetchval("SELECT count(*) FROM appointments") == 1
        assert await connection.fetchval("SELECT count(*) FROM patient_revisions") == 1
        assert await connection.fetchval("SELECT count(*) FROM users WHERE created_at IS NULL OR updated_at IS NULL") == 0
        await connection.close()
    finally:
        await _drop_migration_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_upgrade_rejects_existing_overlaps_without_mutating_0001():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _migration_database(overlapping=True)
    try:
        assert returncode != 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0001_initial"
        assert await connection.fetchval("SELECT count(*) FROM appointments") == 2
        assert await connection.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name = 'appointments' AND column_name = 'ends_at'") == 0
        await connection.close()
    finally:
        await _drop_migration_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_empty_database_upgrade_reaches_corrected_head():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_empty_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_postgres_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    target = source.rsplit("/", 1)[0] + f"/{name}"
    env = {**os.environ, "DATABASE_URL": target, "JWT_SECRET": "empty-migration-secret", "ENVIRONMENT": "test"}
    try:
        result = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0003_clinic_setup"
        assert await connection.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' AND column_name IN ('created_at', 'updated_at')") == 2
        await connection.close()
    finally:
        await _drop_migration_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_concurrent_case_insensitive_room_create_and_rename(postgres_context):
    client, sessions = postgres_context
    headers, _, _, rooms = await create_resources(client, sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
        created = await asyncio.gather(
            left.post("/api/rooms", headers=headers, json={"name": "Concurrent Room"}),
            right.post("/api/rooms", headers=headers, json={"name": "concurrent room"}),
        )
    assert sorted(response.status_code for response in created) == [201, 409]
    first_room, second_room = rooms[0], rooms[1]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
        renamed = await asyncio.gather(
            left.patch(f"/api/rooms/{first_room.id}", headers=headers, json={"name": "Shared Rename"}),
            right.patch(f"/api/rooms/{second_room.id}", headers=headers, json={"name": "shared rename"}),
        )
    assert sorted(response.status_code for response in renamed) == [200, 409]


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_booking_and_room_deactivation_do_not_leave_invalid_assignment(postgres_context):
    client, sessions = postgres_context
    headers, doctors, _, rooms = await create_resources(client, sessions)
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Race", "last_name": "Patient"})
    payload = {"patient_id": patient.json()["id"], "doctor_id": str(doctors[0].id), "room_id": str(rooms[0].id), "starts_at": "2027-02-01T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Race"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as booking_client, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as management_client:
        booking, deactivation = await asyncio.gather(
            booking_client.post("/api/appointments", headers=headers, json=payload),
            management_client.patch(f"/api/rooms/{rooms[0].id}", headers=headers, json={"is_active": False}),
        )
    assert (booking.status_code, deactivation.status_code) in {(201, 200), (422, 200)}
    async with sessions() as session:
        active_assignment = await session.scalar(select(Appointment.id).where(Appointment.room_id == rooms[0].id, Appointment.status.in_(["scheduled", "confirmed", "arrived", "in_progress", "completed"])))
        room = await session.get(Room, rooms[0].id)
    assert not (active_assignment and room and not room.is_active)


async def _create_future_appointment(client: AsyncClient, headers: dict[str, str], doctor_id: str, room_id: str, patient_name: str, assistant_id: str | None = None, start: str = "2027-03-05T10:00:00+00:00") -> dict:
    patient = await client.post("/api/patients", headers=headers, json={"first_name": patient_name, "last_name": "Reactivation"})
    assert patient.status_code == 201
    payload = {
        "patient_id": patient.json()["id"],
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": start,
        "duration_minutes": 30,
        "appointment_type": "Reactivation regression",
    }
    if assistant_id is not None:
        payload["assistant_id"] = assistant_id
    appointment = await client.post("/api/appointments", headers=headers, json=payload)
    assert appointment.status_code == 201, appointment.text
    cancelled = await client.patch(f"/api/appointments/{appointment.json()['id']}/status", headers=headers, json={"status": "cancelled"})
    assert cancelled.status_code == 200
    return {**appointment.json(), **payload}


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_reactivation_rejects_deactivated_room_doctor_and_assistant(postgres_context):
    client, sessions = postgres_context
    headers, doctors, assistants, rooms = await create_resources(client, sessions)
    cases = [
        ("room", rooms[0], None),
        ("doctor", doctors[1], None),
        ("assistant", assistants[0], assistants[0]),
    ]
    for resource, entity, assistant in cases:
        appointment = await _create_future_appointment(
            client,
            headers,
            str(doctors[0].id),
            str(rooms[1].id if resource != "room" else entity.id),
            f"{resource.title()} Case",
            assistant_id=str(assistant.id) if assistant is not None else None,
        )
        if resource == "room":
            deactivation = await client.patch(f"/api/rooms/{entity.id}", headers=headers, json={"is_active": False})
        else:
            deactivation = await client.post(f"/api/staff/{entity.id}/deactivate", headers=headers)
        assert deactivation.status_code == 200
        reactivation = await client.patch(f"/api/appointments/{appointment['id']}/status", headers=headers, json={"status": "scheduled"})
        assert reactivation.status_code == 422
        current = await client.get("/api/appointments", headers=headers)
        saved = next(item for item in current.json() if item["id"] == appointment["id"])
        assert saved["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_reactivation_racing_resource_deactivation_never_assigns_inactive_resource(postgres_context):
    client, sessions = postgres_context
    headers, doctors, assistants, rooms = await create_resources(client, sessions)
    cases = [
        ("room", rooms[0], {"doctor_id": str(doctors[0].id), "room_id": str(rooms[0].id)}),
        ("doctor", doctors[1], {"doctor_id": str(doctors[1].id), "room_id": str(rooms[1].id)}),
        ("assistant", assistants[0], {"doctor_id": str(doctors[0].id), "room_id": str(rooms[2].id), "assistant_id": str(assistants[0].id)}),
    ]
    for resource, entity, refs in cases:
        appointment = await _create_future_appointment(client, headers, patient_name=f"Race {resource}", **refs)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
            if resource == "room":
                deactivation_request = right.patch(f"/api/rooms/{entity.id}", headers=headers, json={"is_active": False})
            else:
                deactivation_request = right.post(f"/api/staff/{entity.id}/deactivate", headers=headers)
            reactivation, deactivation = await asyncio.gather(
                left.patch(f"/api/appointments/{appointment['id']}/status", headers=headers, json={"status": "scheduled"}),
                deactivation_request,
            )
        assert reactivation.status_code in {200, 422}
        assert deactivation.status_code == 200
        async with sessions() as session:
            saved = await session.get(Appointment, appointment["id"])
            resource_active = await session.scalar(select(Room.is_active).where(Room.id == entity.id)) if resource == "room" else await session.scalar(select(User.is_active).where(User.id == entity.id))
        assert saved is not None
        assert not (saved.status in {"scheduled", "confirmed", "arrived", "in_progress", "completed"} and resource_active is False)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_booking_racing_doctor_and_assistant_deactivation_never_assigns_inactive_staff(postgres_context):
    client, sessions = postgres_context
    headers, doctors, assistants, rooms = await create_resources(client, sessions)
    cases = [
        ("doctor", doctors[1], {"doctor_id": str(doctors[1].id), "room_id": str(rooms[0].id)}),
        ("assistant", assistants[0], {"doctor_id": str(doctors[0].id), "room_id": str(rooms[1].id), "assistant_id": str(assistants[0].id)}),
    ]
    for resource, entity, refs in cases:
        patient = await client.post("/api/patients", headers=headers, json={"first_name": f"Booking {resource}", "last_name": "Race"})
        payload = {"patient_id": patient.json()["id"], **refs, "starts_at": "2027-03-06T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Booking race"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
            booking, deactivation = await asyncio.gather(
                left.post("/api/appointments", headers=headers, json=payload),
                right.post(f"/api/staff/{entity.id}/deactivate", headers=headers),
            )
        assert booking.status_code in {201, 422}
        assert deactivation.status_code == 200
        async with sessions() as session:
            active_appointment = await session.scalar(select(Appointment).where(Appointment.clinic_id == entity.clinic_id, Appointment.status.in_(["scheduled", "confirmed", "arrived", "in_progress", "completed"]), (Appointment.doctor_id == entity.id) | (Appointment.assistant_id == entity.id)))
            staff_active = await session.scalar(select(User.is_active).where(User.id == entity.id))
        assert not (active_appointment is not None and staff_active is False)
