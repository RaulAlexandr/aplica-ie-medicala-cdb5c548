"""PostgreSQL concurrency tests for T04/T05 unique timer per doctor.

These tests require a real PostgreSQL database and verify concurrent request handling.
All tests in this module require PostgreSQL and are marked with @pytest.mark.postgres.
"""

import asyncio
import os
import subprocess
from contextvars import ContextVar
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import User, get_db
from app.main import app

# All tests in this module require PostgreSQL
pytestmark = pytest.mark.postgres

SYNC_TIMEOUT = 10
OP_TIMEOUT = 20
_request_role = ContextVar("concurrency_request_role", default=None)


class _TaggedASGI:
    """Attach a test-only role to a request so its DB session can be gated."""

    def __init__(self, app, role):
        self.app = app
        self.role = role

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _request_role.set(self.role)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_role.reset(token)


def _appointment_lock(statement):
    return (
        getattr(statement, "_for_update_arg", None) is not None
        and any(table.name == "appointments" for table in statement.get_final_froms())
    )


async def _install_lock_gate(target_url, winner_role):
    """Install a test-only AsyncSession gate after the winner obtains appointment FOR UPDATE."""
    acquired = asyncio.Event()
    release = asyncio.Event()
    contender_at_lock = asyncio.Event()
    gate_seen = False
    previous_overrides = app.dependency_overrides.copy()

    class GatedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            nonlocal gate_seen
            is_lock = _appointment_lock(statement)
            role = _request_role.get()
            if is_lock and role != winner_role:
                contender_at_lock.set()
            result = await super().execute(statement, *args, **kwargs)
            if is_lock and role == winner_role and not gate_seen:
                gate_seen = True
                acquired.set()
                try:
                    await asyncio.wait_for(release.wait(), SYNC_TIMEOUT)
                except TimeoutError as exc:
                    raise AssertionError("Timed out at winner appointment-lock gate") from exc
            return result

    gated_engine = create_async_engine(target_url, pool_pre_ping=True)
    gated_sessions = async_sessionmaker(gated_engine, expire_on_commit=False, class_=GatedSession)

    async def override():
        async with gated_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override

    async def wait_for_postgres_lock_wait():
        """Require the second backend to be queued on PostgreSQL's row lock."""
        try:
            await asyncio.wait_for(contender_at_lock.wait(), SYNC_TIMEOUT)
        except TimeoutError as exc:
            raise AssertionError("Timed out waiting for contender to reach appointment lock") from exc
        admin_url = _postgres_dsn(target_url, "postgres")
        deadline = asyncio.get_running_loop().time() + SYNC_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            conn = await asyncio.wait_for(asyncpg.connect(admin_url), SYNC_TIMEOUT)
            try:
                waiting = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = $1 AND wait_event_type = 'Lock')",
                    urlparse(target_url).path.rsplit("/", 1)[-1],
                )
            finally:
                await conn.close()
            if waiting:
                return
            await asyncio.sleep(0.025)
        raise AssertionError("Timed out waiting for PostgreSQL row-lock wait")

    async def cleanup():
        release.set()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        await gated_engine.dispose()

    return acquired, release, wait_for_postgres_lock_wait, cleanup


async def _bounded(task, phase):
    try:
        return await asyncio.wait_for(task, OP_TIMEOUT)
    except TimeoutError as exc:
        raise AssertionError(f"Timed out during concurrent operation phase: {phase}") from exc


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
        raise RuntimeError(f"Alembic upgrade failed: {result.stderr}")

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


async def get_appointment(client, headers, appointment_id):
    """Get an appointment by ID from the list endpoint."""
    appts = await client.get("/api/appointments", headers=headers)
    assert appts.status_code == 200, f"Failed to list appointments: {appts.status_code}"
    for appt in appts.json():
        if str(appt["id"]) == str(appointment_id):
            return appt
    return None


async def get_appointment_procedures(client, headers, appointment_id):
    """Get all procedures for an appointment from the performed-procedures endpoint."""
    procs = await client.get("/api/performed-procedures", headers=headers)
    assert procs.status_code == 200, f"Failed to list procedures: {procs.status_code}"
    return [p for p in procs.json() if str(p["appointment_id"]) == str(appointment_id)]


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


async def _closure_scenario(client, headers, with_procedure=False, paused=False):
    patient_id = await create_patient(client, headers)
    doctor_id = (await get_manager_info(client, headers))["id"]
    room_id = await create_room(client, headers)
    appt_id = await create_appointment(
        client, headers, patient_id, doctor_id, room_id, "2026-10-01T10:00:00+00:00"
    )
    catalog_id = await create_procedure_catalog(client, headers)
    if with_procedure:
        proc_id = await create_performed_procedure(client, headers, appt_id, catalog_id)
    else:
        proc_id = None
    for status in ["confirmed", "arrived", "in_progress"]:
        response = await client.patch(
            f"/api/appointments/{appt_id}/status", headers=headers, json={"status": status}
        )
        assert response.status_code == 200
    if paused:
        assert (await client.post(f"/api/performed-procedures/{proc_id}/start", headers=headers)).status_code == 200
        assert (await client.post(f"/api/performed-procedures/{proc_id}/pause", headers=headers)).status_code == 200
    return appt_id, catalog_id, proc_id


async def _manager_context(client, email):
    response = await client.post("/api/auth/register", json={
        "clinic_name": "Concurrency Clinic", "full_name": "Manager User",
        "email": email, "password": "testpass1234"
    })
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return headers


async def _run_controlled_race(target_url, winner_role, winner_request,
                               loser_role, loser_request):
    acquired, release, wait_for_lock, cleanup = await _install_lock_gate(
        target_url, winner_role
    )
    winner_client = AsyncClient(transport=ASGITransport(app=_TaggedASGI(app, winner_role)), base_url="http://test")
    loser_client = AsyncClient(transport=ASGITransport(app=_TaggedASGI(app, loser_role)), base_url="http://test")
    winner_task = loser_task = None
    try:
        winner_task = asyncio.create_task(winner_request(winner_client))
        await _bounded(acquired.wait(), f"{winner_role} acquiring appointment lock")
        loser_task = asyncio.create_task(loser_request(loser_client))
        await _bounded(wait_for_lock(), f"{loser_role} waiting on PostgreSQL appointment lock")
        release.set()
        return await _bounded(asyncio.gather(winner_task, loser_task), "releasing controlled race")
    finally:
        release.set()
        for task in (winner_task, loser_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (winner_task, loser_task) if t is not None), return_exceptions=True)
        await winner_client.aclose()
        await loser_client.aclose()
        await cleanup()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_create_concurrent(postgres_context):
    """Exercise both serialized orders at the appointment row lock."""
    client, _sessions, target_url, _db_name = postgres_context
    headers = await _manager_context(client, f"manager-closure-create-{uuid4().hex}@test.com")

    # Closure wins: it holds the appointment lock while the create request queues behind it.
    appt_id, catalog_id, _ = await _closure_scenario(client, headers)
    close_req = lambda c: c.patch(f"/api/appointments/{appt_id}/status", headers=headers, json={"status": "completed"})
    create_req = lambda c: c.post("/api/performed-procedures", headers=headers, json={"appointment_id": appt_id, "procedure_catalog_id": catalog_id})
    close_resp, create_resp = await _run_controlled_race(target_url, "close", close_req, "create", create_req)
    assert close_resp.status_code == 200
    assert create_resp.status_code == 409
    assert (await get_appointment(client, headers, appt_id))["status"] == "completed"
    assert await get_appointment_procedures(client, headers, appt_id) == []

    # Create wins under the same controlled lock handoff.
    appt_id, catalog_id, _ = await _closure_scenario(client, headers)
    create_req = lambda c: c.post("/api/performed-procedures", headers=headers, json={"appointment_id": appt_id, "procedure_catalog_id": catalog_id})
    close_req = lambda c: c.patch(f"/api/appointments/{appt_id}/status", headers=headers, json={"status": "completed"})
    create_resp, close_resp = await _run_controlled_race(target_url, "create", create_req, "close", close_req)
    assert create_resp.status_code == 201
    assert close_resp.status_code == 409
    assert (await get_appointment(client, headers, appt_id))["status"] == "in_progress"
    procedures = await get_appointment_procedures(client, headers, appt_id)
    assert len(procedures) == 1 and procedures[0]["status"] == "pending"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_start_concurrent(postgres_context):
    client, _sessions, target_url, _db_name = postgres_context
    headers = await _manager_context(client, f"manager-closure-start-{uuid4().hex}@test.com")
    appt_id, _catalog_id, proc_id = await _closure_scenario(client, headers, with_procedure=True)
    start_req = lambda c: c.post(f"/api/performed-procedures/{proc_id}/start", headers=headers)
    close_req = lambda c: c.patch(f"/api/appointments/{appt_id}/status", headers=headers, json={"status": "completed"})
    start_resp, close_resp = await _run_controlled_race(target_url, "start", start_req, "close", close_req)
    assert close_resp.status_code == 409
    assert start_resp.status_code == 200
    assert (await get_appointment(client, headers, appt_id))["status"] == "in_progress"
    proc = await client.get(f"/api/performed-procedures/{proc_id}", headers=headers)
    assert proc.status_code == 200 and proc.json()["status"] == "running"
    segments = (await client.get(f"/api/performed-procedures/{proc_id}/segments", headers=headers)).json()
    assert len([s for s in segments if s["segment_type"] == "run" and s["ended_at"] is None]) == 1


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_closure_vs_resume_concurrent(postgres_context):
    client, _sessions, target_url, _db_name = postgres_context
    headers = await _manager_context(client, f"manager-closure-resume-{uuid4().hex}@test.com")
    appt_id, _catalog_id, proc_id = await _closure_scenario(
        client, headers, with_procedure=True, paused=True
    )
    resume_req = lambda c: c.post(f"/api/performed-procedures/{proc_id}/resume", headers=headers)
    close_req = lambda c: c.patch(f"/api/appointments/{appt_id}/status", headers=headers, json={"status": "completed"})
    resume_resp, close_resp = await _run_controlled_race(target_url, "resume", resume_req, "close", close_req)
    assert close_resp.status_code == 409
    assert resume_resp.status_code == 200
    assert (await get_appointment(client, headers, appt_id))["status"] == "in_progress"
    proc = await client.get(f"/api/performed-procedures/{proc_id}", headers=headers)
    assert proc.status_code == 200 and proc.json()["status"] == "running"
    segments = (await client.get(f"/api/performed-procedures/{proc_id}/segments", headers=headers)).json()
    pauses = [s for s in segments if s["segment_type"] == "pause"]
    assert len(pauses) == 1 and pauses[0]["ended_at"] is not None
    assert len([s for s in segments if s["segment_type"] == "run" and s["ended_at"] is None]) == 1
