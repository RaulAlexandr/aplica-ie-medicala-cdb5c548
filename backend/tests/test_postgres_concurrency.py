"""PostgreSQL concurrency tests for T04/T05 unique timer per doctor.

These tests require a real PostgreSQL database and verify concurrent request handling.
All tests in this module require PostgreSQL and are marked with @pytest.mark.postgres.
"""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import User, get_db
from app.main import app

# All tests in this module require PostgreSQL
pytestmark = pytest.mark.postgres


@pytest.fixture
async def postgres_context():
    """Create an AsyncClient connected to a temporary PostgreSQL database with migrations applied."""
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")

    # Create temporary database
    db_name = f"dentacare_concurrency_{uuid4().hex[:12]}"
    admin_url = _postgres_dsn(url, "postgres")

    admin_conn = await asyncpg.connect(admin_url)
    try:
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        except asyncpg.PostgresError:
            # Database might already exist from a previous run
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin_conn.close()

    # Build target URL
    target_url = url.rsplit("/", 1)[0] + f"/{db_name}"

    # Apply migrations
    env = {**os.environ, "DATABASE_URL": target_url, "JWT_SECRET": "concurrency-test-secret", "ENVIRONMENT": "test"}
    result = await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", "head"],
        cwd=os.path.dirname(__file__) + "/..",
        env=env,
        capture_output=True,
        text=True,
        check=True
    )
    if result.returncode != 0:
        pytest.skip(f"Alembic upgrade failed: {result.stderr}")

    # Setup engine and session
    engine = create_async_engine(target_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions, target_url, db_name
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        # Drop temporary database
        try:
            admin_conn = await asyncpg.connect(admin_url)
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()


def _postgres_dsn(url: str, database: str) -> str:
    """Convert connection URL to a plain postgres DSN for admin operations."""
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


# Helper functions


async def register_pg(client):
    """Register a clinic manager for PostgreSQL tests."""
    response = await client.post("/api/auth/register", json={
        "clinic_name": f"Test Clinic {uuid4().hex[:8]}",
        "full_name": "Test Manager",
        "email": f"manager-{uuid4().hex}@test.com",
        "password": "test_password_123"
    })
    assert response.status_code == 201
    return response.json()


async def create_room(client, headers):
    """Create a room and return its ID."""
    room = await client.post("/api/rooms", headers=headers, json={"name": f"Room {uuid4().hex[:8]}"})
    assert room.status_code == 201, f"Failed to create room: {room.status_code}, {room.text}"
    return room.json()["id"]


async def create_patient(client, headers):
    """Create a patient and return its ID."""
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": f"Patient_{uuid4().hex[:8]}",
        "last_name": "Test"
    })
    assert patient.status_code == 201, f"Failed to create patient: {patient.status_code}"
    return patient.json()["id"]


async def create_procedure_catalog(client, headers):
    """Create a procedure catalog and return its ID."""
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": f"PROC_{uuid4().hex[:8]}",
        "name": "Test Procedure",
        "base_price": "100.00",
        "default_duration_minutes": 30
    })
    assert catalog.status_code == 201, f"Failed to create catalog: {catalog.status_code}"
    return catalog.json()["id"]


async def create_appointment(client, headers, patient_id, doctor_id, room_id, start_time="2026-10-01T10:00:00+00:00"):
    """Create an appointment and return its ID."""
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": start_time,
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appointment.status_code == 201, f"Failed to create appointment: {appointment.status_code}, {appointment.text}"
    return appointment.json()["id"]


async def create_performed_procedure(client, headers, appointment_id, catalog_id):
    """Create a performed procedure and return its ID."""
    procedure = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    assert procedure.status_code == 201, f"Failed to create procedure: {procedure.status_code}"
    return procedure.json()["id"]


async def create_doctor_via_db(sessions, clinic_id):
    """Create a doctor user directly in the database."""
    async with sessions() as session:
        user = User(
            clinic_id=clinic_id,
            full_name=f"Dr. {uuid4().hex[:8]}",
            email=f"doctor-{uuid4().hex}@test.com",
            password_hash=hash_password("doctor_password_123456"),
            role="doctor",
            is_active=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def get_manager_info(client, headers):
    """Get the user info of the current manager."""
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    return me.json()


# Test 1: Two concurrent start attempts for the same doctor

@pytest.mark.asyncio
async def test_concurrent_start_same_doctor(postgres_context):
    """PostgreSQL test: Two concurrent start attempts for the same doctor should fail.

    Setup: two distinct procedures, both pending, same doctor, no active timer.
    Send concurrent start requests.
    Verify: exactly one 200 and one 409, exactly one procedure running.
    """
    client, _sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create test environment: room, patient, catalog, two appointments for manager (as doctor)
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)

    # Manager acts as doctor for both procedures
    appointment1_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T10:00:00+00:00")
    appointment2_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T11:00:00+00:00")

    procedure1_id = await create_performed_procedure(client, headers, appointment1_id, catalog_id)
    procedure2_id = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Create two independent clients for concurrent requests
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client1, \
         AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client2:

        headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
        headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Start both procedures concurrently
        start_task1 = client1.post(f"/api/performed-procedures/{procedure1_id}/start", headers=headers1)
        start_task2 = client2.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers2)

        response1, response2 = await asyncio.gather(start_task1, start_task2)

    # Verify exactly one succeeded and one failed
    statuses = [response1.status_code, response2.status_code]
    assert 200 in statuses, "Expected at least one 200 response"
    assert 409 in statuses, "Expected at least one 409 response"
    assert statuses.count(200) == 1, "Expected exactly one 200"
    assert statuses.count(409) == 1, "Expected exactly one 409"

    # Verify the 409 response has the correct error message
    failed_response = response1 if response1.status_code == 409 else response2
    assert "already has a running procedure" in failed_response.json().get("detail", "").lower()

    # Verify persistent state: exactly one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    assert list_resp.status_code == 200
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1, f"Expected exactly 1 running procedure, got {len(running_procs)}"


# Test 2: Concurrent start and resume for the same doctor

@pytest.mark.asyncio
async def test_concurrent_start_resume_same_doctor(postgres_context):
    """PostgreSQL test: Start and concurrent resume for the same doctor should fail.

    Setup: one procedure pending, one procedure paused, same doctor, no active timer.
    Send concurrent start (on pending) and resume (on paused) requests.
    Verify: exactly one 200 and one 409, exactly one procedure running.
    """
    client, _sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create test environment
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)

    # Manager acts as doctor
    appointment1_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T12:00:00+00:00")
    appointment2_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T13:00:00+00:00")

    procedure1_id = await create_performed_procedure(client, headers, appointment1_id, catalog_id)
    procedure2_id = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Start and pause first procedure
    start_resp = await client.post(f"/api/performed-procedures/{procedure1_id}/start", headers=headers)
    assert start_resp.status_code == 200

    pause_resp = await client.post(f"/api/performed-procedures/{procedure1_id}/pause", headers=headers)
    assert pause_resp.status_code == 200

    # Now procedure1 is paused, procedure2 is pending
    # Try to start procedure2 and resume procedure1 concurrently
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client1, \
         AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client2:

        headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
        headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

        start_task = client1.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers1)
        resume_task = client2.post(f"/api/performed-procedures/{procedure1_id}/resume", headers=headers2)

        start_resp, resume_resp = await asyncio.gather(start_task, resume_task)

    # Verify exactly one succeeded and one failed
    statuses = [start_resp.status_code, resume_resp.status_code]
    assert 200 in statuses, "Expected at least one 200 response"
    assert 409 in statuses, "Expected at least one 409 response"
    assert statuses.count(200) == 1, "Expected exactly one 200"
    assert statuses.count(409) == 1, "Expected exactly one 409"

    # Verify the 409 response has the correct error message
    failed_response = start_resp if start_resp.status_code == 409 else resume_resp
    assert "already has a running procedure" in failed_response.json().get("detail", "").lower()

    # Verify persistent state: exactly one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    assert list_resp.status_code == 200
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1, f"Expected exactly 1 running procedure, got {len(running_procs)}"


# Test 3: Manager can start procedures for other doctors

@pytest.mark.asyncio
async def test_manager_start_for_other_doctor(postgres_context):
    """PostgreSQL test: Manager can start procedures for other doctors.

    Verifies that manager role can start procedures on behalf of other doctors,
    and the unique timer constraint applies to the executed_by_id (the doctor), not the actor.
    """
    client, sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create a doctor via DB
    doctor = await create_doctor_via_db(sessions, manager_info["clinic_id"])

    # Create environment for the doctor
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)
    appointment_id = await create_appointment(client, headers, patient_id, str(doctor.id), room_id)

    # Create procedure for the doctor
    procedure_id = await create_performed_procedure(client, headers, appointment_id, catalog_id)

    # Manager can start procedure for doctor
    start_resp = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert start_resp.status_code == 200

    # Create another procedure for the same doctor
    appointment2_id = await create_appointment(client, headers, patient_id, str(doctor.id), room_id, "2026-10-01T15:00:00+00:00")
    procedure_id_2 = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Manager cannot start second procedure for the same doctor (unique timer per doctor)
    start_resp2 = await client.post(f"/api/performed-procedures/{procedure_id_2}/start", headers=headers)
    assert start_resp2.status_code == 409
    assert "already has a running procedure" in start_resp2.json().get("detail", "").lower()


# Test 4: Two different doctors can have independent running procedures

@pytest.mark.asyncio
async def test_different_doctors_independent(postgres_context):
    """PostgreSQL test: Two different doctors can have running procedures independently.

    Verifies that the unique timer constraint is per doctor, not global.
    Different doctors should be able to have their own running procedures.
    """
    client, sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create two doctors via DB
    doctor2 = await create_doctor_via_db(sessions, manager_info["clinic_id"])
    doctor3 = await create_doctor_via_db(sessions, manager_info["clinic_id"])

    # Create environment for doctor2 (separate room and time)
    room_id_2 = await create_room(client, headers)
    patient2_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)
    appointment2_id = await create_appointment(client, headers, patient2_id, str(doctor2.id), room_id_2, "2026-10-01T16:00:00+00:00")
    procedure2_id = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Create environment for doctor3 (different room and time)
    room_id_3 = await create_room(client, headers)
    patient3_id = await create_patient(client, headers)
    appointment3_id = await create_appointment(client, headers, patient3_id, str(doctor3.id), room_id_3, "2026-10-01T17:00:00+00:00")
    procedure3_id = await create_performed_procedure(client, headers, appointment3_id, catalog_id)

    # Start procedure for doctor2
    start_resp2 = await client.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers)
    assert start_resp2.status_code == 200

    # Start procedure for doctor3 - should succeed (different doctor)
    start_resp3 = await client.post(f"/api/performed-procedures/{procedure3_id}/start", headers=headers)
    assert start_resp3.status_code == 200

    # Verify persistent state: both doctors have running procedures
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 2, f"Expected 2 running procedures, got {len(running_procs)}"

    running_ids = {str(p["id"]) for p in running_procs}
    assert procedure2_id in running_ids
    assert procedure3_id in running_ids

    # Verify each has exactly one active run segment
    for proc_id in [procedure2_id, procedure3_id]:
        segments_resp = await client.get(f"/api/performed-procedures/{proc_id}/segments", headers=headers)
        assert segments_resp.status_code == 200
        segments = segments_resp.json()
        run_segments = [s for s in segments if s["segment_type"] == "run" and s["ended_at"] is None]
        assert len(run_segments) == 1, f"Expected exactly 1 active run segment for procedure {proc_id}"


# Test 5: Race condition - two truly concurrent start requests for same procedure

@pytest.mark.asyncio
async def test_race_condition_two_starts_same_doctor(postgres_context):
    """PostgreSQL test: Race condition with two concurrent start requests for same doctor.

    Setup: two distinct procedures, both pending, same doctor, no active timer.
    Start first procedure, then send two start requests for the second procedure concurrently.
    Verify: at most one succeeds, no 500 errors.
    """
    client, _sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create test environment
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)

    appointment1_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T18:00:00+00:00")
    appointment2_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T19:00:00+00:00")

    procedure1_id = await create_performed_procedure(client, headers, appointment1_id, catalog_id)
    procedure2_id = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Start first procedure
    start_resp1 = await client.post(f"/api/performed-procedures/{procedure1_id}/start", headers=headers)
    assert start_resp1.status_code == 200

    # Try concurrent start on second procedure
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client1, \
         AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client2:

        headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
        headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

        start_task1 = client1.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers1)
        start_task2 = client2.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers2)

        response1, response2 = await asyncio.gather(start_task1, start_task2, return_exceptions=False)

    # At most one request can succeed
    success_count = sum(1 for r in [response1, response2] if r.status_code == 200)
    assert success_count <= 1, f"More than one concurrent start succeeded: {success_count}"

    # No 500 errors
    for response in [response1, response2]:
        assert response.status_code != 500, f"Unexpected 500 error: {response.text}"

    # Verify persistent state: only one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1


# Test 6: Race condition - start and resume concurrently

@pytest.mark.asyncio
async def test_race_condition_start_resume_same_doctor(postgres_context):
    """PostgreSQL test: Race condition with concurrent start and resume.

    Setup: procedure1 paused, procedure2 pending, same doctor, no active timer.
    Start procedure2 and resume procedure1 concurrently.
    Verify: exactly one 200 and one 409, exactly one procedure running.
    """
    client, _sessions, _target_url, _db_name = postgres_context

    # Register manager
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    manager_info = await get_manager_info(client, headers)

    # Create test environment
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)

    appointment1_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T20:00:00+00:00")
    appointment2_id = await create_appointment(client, headers, patient_id, manager_info["id"], room_id, "2026-10-01T21:00:00+00:00")

    procedure1_id = await create_performed_procedure(client, headers, appointment1_id, catalog_id)
    procedure2_id = await create_performed_procedure(client, headers, appointment2_id, catalog_id)

    # Start and pause first procedure
    await client.post(f"/api/performed-procedures/{procedure1_id}/start", headers=headers)
    await client.post(f"/api/performed-procedures/{procedure1_id}/pause", headers=headers)

    # Try concurrent resume on first and start on second
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client1, \
         AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client2:

        headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
        headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

        resume_task = client1.post(f"/api/performed-procedures/{procedure1_id}/resume", headers=headers1)
        start_task = client2.post(f"/api/performed-procedures/{procedure2_id}/start", headers=headers2)

        resume_resp, start_resp = await asyncio.gather(resume_task, start_task, return_exceptions=False)

    # Verify exactly one succeeded and one failed
    statuses = [resume_resp.status_code, start_resp.status_code]
    assert 200 in statuses, "Expected at least one 200 response"
    assert 409 in statuses, "Expected at least one 409 response"
    assert statuses.count(200) == 1, "Expected exactly one 200"
    assert statuses.count(409) == 1, "Expected exactly one 409"

    # Verify persistent state: exactly one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1, f"Expected exactly 1 running procedure, got {len(running_procs)}"


# ============================================================================
# CLOSURE CONCURRENCY TESTS
# Test race conditions between closure and procedure create/start/resume
# ============================================================================


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_create_concurrent(postgres_context):
    """Test concurrent closure and procedure creation - closure should win if it starts first."""
    client, _sessions, _target_url, _db_name = postgres_context

    # Setup: Create clinic, patient, doctor, appointment
    clinic_resp = await client.post("/api/clinics", json={
        "name": "Test Clinic",
        "address": "Test Address",
        "phone": "1234567890",
        "email": "test@clinic.com"
    })
    assert clinic_resp.status_code == 201

    # Register as clinic manager
    auth_resp = await client.post("/api/auth/register", json={
        "clinic_name": "Test Clinic",
        "full_name": "Manager User",
        "email": "manager@test.com",
        "password": "testpass123"
    })
    assert auth_resp.status_code == 201
    tokens = auth_resp.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient_resp = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient_resp.status_code == 201
    patient_id = patient_resp.json()["id"]

    # Get doctor (manager is also a doctor in this context)
    me_resp = await client.get("/api/auth/me", headers=headers)
    doctor_id = me_resp.json()["id"]

    # Create room
    room_resp = await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    # Create appointment
    appt_resp = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appt_resp.status_code == 201
    appt_id = appt_resp.json()["id"]

    # Create procedure catalog
    catalog_resp = await client.post("/api/procedure-catalog", headers=headers, json={
        "code": "TEST_CLOSURE_CREATE",
        "name": "Test Procedure",
        "base_price": 100.00,
        "currency": "RON",
        "default_duration_minutes": 30
    })
    assert catalog_resp.status_code == 201
    catalog_id = catalog_resp.json()["id"]

    # Create two separate clients for concurrent operations
    # Client 1 will try to close the appointment
    client1 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Client 2 will try to create a procedure
    client2 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

    try:
        # Execute concurrently
        close_task = client1.patch(
            f"/api/appointments/{appt_id}/status",
            headers=headers1,
            json={"status": "completed"}
        )
        create_task = client2.post(
            "/api/performed-procedures",
            headers=headers2,
            json={
                "appointment_id": appt_id,
                "procedure_catalog_id": catalog_id
            }
        )

        close_resp, create_resp = await asyncio.gather(close_task, create_task, return_exceptions=False)

        # One should succeed, one should fail

        # If closure succeeds, create should fail
        if close_resp.status_code == 200:
            assert create_resp.status_code == 409, f"Create should fail when closure succeeds, got {create_resp.status_code}"
        # If create succeeds, closure should fail
        elif create_resp.status_code == 201:
            assert close_resp.status_code == 409, f"Closure should fail when create succeeds, got {close_resp.status_code}"
        else:
            # Both failed or unexpected status
            assert False, f"Unexpected statuses: close={close_resp.status_code}, create={create_resp.status_code}"

        # Verify final state consistency
        appt_check = await client.get(f"/api/appointments/{appt_id}", headers=headers)
        appt_status = appt_check.json()["status"]

        if appt_status == "completed":
            # Closure won - no procedures should be created
            proc_list = await client.get(f"/api/appointments/{appt_id}/procedures", headers=headers)
            procedures = proc_list.json()
            assert len(procedures) == 0, "No procedures should exist if closure succeeded"
        else:
            # Create won - appointment should not be completed
            assert appt_status != "completed", "Appointment should not be completed if create succeeded"

    finally:
        await client1.aclose()
        await client2.aclose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_start_concurrent(postgres_context):
    """Test concurrent closure and procedure start - closure should win if it starts first."""
    client, _sessions, _target_url, _db_name = postgres_context

    # Setup: Create clinic, patient, doctor, appointment, procedure
    clinic_resp = await client.post("/api/clinics", json={
        "name": "Test Clinic",
        "address": "Test Address",
        "phone": "1234567890",
        "email": "test@clinic.com"
    })
    assert clinic_resp.status_code == 201

    # Register as clinic manager
    auth_resp = await client.post("/api/auth/register", json={
        "clinic_name": "Test Clinic",
        "full_name": "Manager User",
        "email": "manager2@test.com",
        "password": "testpass123"
    })
    assert auth_resp.status_code == 201
    tokens = auth_resp.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient_resp = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient_resp.status_code == 201
    patient_id = patient_resp.json()["id"]

    # Get doctor
    me_resp = await client.get("/api/auth/me", headers=headers)
    doctor_id = me_resp.json()["id"]

    # Create room
    room_resp = await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    # Create appointment
    appt_resp = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appt_resp.status_code == 201
    appt_id = appt_resp.json()["id"]

    # Create procedure catalog
    catalog_resp = await client.post("/api/procedure-catalog", headers=headers, json={
        "code": "TEST_CLOSURE_START",
        "name": "Test Procedure",
        "base_price": 100.00,
        "currency": "RON",
        "default_duration_minutes": 30
    })
    assert catalog_resp.status_code == 201
    catalog_id = catalog_resp.json()["id"]

    # Create procedure (pending)
    proc_resp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appt_id,
        "procedure_catalog_id": catalog_id
    })
    assert proc_resp.status_code == 201
    proc_id = proc_resp.json()["id"]

    # Create two separate clients for concurrent operations
    client1 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
    client2 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

    try:
        # Execute concurrently
        close_task = client1.patch(
            f"/api/appointments/{appt_id}/status",
            headers=headers1,
            json={"status": "completed"}
        )
        start_task = client2.post(
            f"/api/performed-procedures/{proc_id}/start",
            headers=headers2
        )

        close_resp, start_resp = await asyncio.gather(close_task, start_task, return_exceptions=False)

        # One should succeed, one should fail

        # If closure succeeds, start should fail
        if close_resp.status_code == 200:
            assert start_resp.status_code == 409, f"Start should fail when closure succeeds, got {start_resp.status_code}"
        # If start succeeds, closure should fail
        elif start_resp.status_code == 200:
            assert close_resp.status_code == 409, f"Closure should fail when start succeeds, got {close_resp.status_code}"
        else:
            # Both failed or unexpected status
            assert False, f"Unexpected statuses: close={close_resp.status_code}, start={start_resp.status_code}"

        # Verify final state consistency
        appt_check = await client.get(f"/api/appointments/{appt_id}", headers=headers)
        appt_status = appt_check.json()["status"]
        proc_check = await client.get(f"/api/performed-procedures/{proc_id}", headers=headers)
        proc_status = proc_check.json()["status"]

        if appt_status == "completed":
            # Closure won - procedure should still be pending (not started)
            assert proc_status == "pending", f"Procedure should be pending if closure succeeded, got {proc_status}"
        else:
            # Start won - appointment should not be completed
            assert appt_status != "completed", "Appointment should not be completed if start succeeded"
            assert proc_status == "running", f"Procedure should be running if start succeeded, got {proc_status}"

    finally:
        await client1.aclose()
        await client2.aclose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_resume_concurrent(postgres_context):
    """Test concurrent closure and procedure resume - closure should win if it starts first."""
    client, _sessions, _target_url, _db_name = postgres_context

    # Setup: Create clinic, patient, doctor, appointment, procedure
    clinic_resp = await client.post("/api/clinics", json={
        "name": "Test Clinic",
        "address": "Test Address",
        "phone": "1234567890",
        "email": "test@clinic.com"
    })
    assert clinic_resp.status_code == 201

    # Register as clinic manager
    auth_resp = await client.post("/api/auth/register", json={
        "clinic_name": "Test Clinic",
        "full_name": "Manager User",
        "email": "manager3@test.com",
        "password": "testpass123"
    })
    assert auth_resp.status_code == 201
    tokens = auth_resp.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient_resp = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient_resp.status_code == 201
    patient_id = patient_resp.json()["id"]

    # Get doctor
    me_resp = await client.get("/api/auth/me", headers=headers)
    doctor_id = me_resp.json()["id"]

    # Create room
    room_resp = await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})
    assert room_resp.status_code == 201
    room_id = room_resp.json()["id"]

    # Create appointment
    appt_resp = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appt_resp.status_code == 201
    appt_id = appt_resp.json()["id"]

    # Create procedure catalog
    catalog_resp = await client.post("/api/procedure-catalog", headers=headers, json={
        "code": "TEST_CLOSURE_RESUME",
        "name": "Test Procedure",
        "base_price": 100.00,
        "currency": "RON",
        "default_duration_minutes": 30
    })
    assert catalog_resp.status_code == 201
    catalog_id = catalog_resp.json()["id"]

    # Create procedure (pending)
    proc_resp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appt_id,
        "procedure_catalog_id": catalog_id
    })
    assert proc_resp.status_code == 201
    proc_id = proc_resp.json()["id"]

    # Start and pause procedure
    start_resp = await client.post(f"/api/performed-procedures/{proc_id}/start", headers=headers)
    assert start_resp.status_code == 200
    pause_resp = await client.post(f"/api/performed-procedures/{proc_id}/pause", headers=headers)
    assert pause_resp.status_code == 200

    # Create two separate clients for concurrent operations
    client1 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers1 = {"Authorization": f"Bearer {tokens['access_token']}"}
    client2 = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    headers2 = {"Authorization": f"Bearer {tokens['access_token']}"}

    try:
        # Execute concurrently
        close_task = client1.patch(
            f"/api/appointments/{appt_id}/status",
            headers=headers1,
            json={"status": "completed"}
        )
        resume_task = client2.post(
            f"/api/performed-procedures/{proc_id}/resume",
            headers=headers2
        )

        close_resp, resume_resp = await asyncio.gather(close_task, resume_task, return_exceptions=False)

        # One should succeed, one should fail

        # If closure succeeds, resume should fail
        if close_resp.status_code == 200:
            assert resume_resp.status_code == 409, f"Resume should fail when closure succeeds, got {resume_resp.status_code}"
        # If resume succeeds, closure should fail
        elif resume_resp.status_code == 200:
            assert close_resp.status_code == 409, f"Closure should fail when resume succeeds, got {close_resp.status_code}"
        else:
            # Both failed or unexpected status
            assert False, f"Unexpected statuses: close={close_resp.status_code}, resume={resume_resp.status_code}"

        # Verify final state consistency
        appt_check = await client.get(f"/api/appointments/{appt_id}", headers=headers)
        appt_status = appt_check.json()["status"]
        proc_check = await client.get(f"/api/performed-procedures/{proc_id}", headers=headers)
        proc_status = proc_check.json()["status"]

        if appt_status == "completed":
            # Closure won - procedure should still be paused (not resumed)
            assert proc_status == "paused", f"Procedure should be paused if closure succeeded, got {proc_status}"
        else:
            # Resume won - appointment should not be completed
            assert appt_status != "completed", "Appointment should not be completed if resume succeeded"
            assert proc_status == "running", f"Procedure should be running if resume succeeded, got {proc_status}"

    finally:
        await client1.aclose()
        await client2.aclose()
