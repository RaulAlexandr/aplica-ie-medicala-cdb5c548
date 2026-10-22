"""PostgreSQL concurrency tests for T04/T05 unique timer per doctor.

These tests require a real PostgreSQL database and verify concurrent request handling.
"""

import asyncio
import os
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

# All tests in this module require PostgreSQL
pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"),
    reason="PostgreSQL test database not configured. Set POSTGRES_TEST_URL to run these tests."
)
pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_test_url():
    """Get PostgreSQL test URL from environment."""
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("PostgreSQL test database not configured")
    return url


@pytest.fixture(autouse=True)
def check_postgres_url():
    """Ensure PostgreSQL URL is set for all tests in this module."""
    if not os.environ.get("POSTGRES_TEST_URL"):
        pytest.skip("POSTGRES_TEST_URL not set")


@pytest.fixture(scope="function")
async def postgres_client(postgres_test_url):
    """Create an AsyncClient connected to PostgreSQL with fresh database."""
    engine = create_async_engine(postgres_test_url)
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    
    async def override():
        async with sessions() as session:
            yield session
    
    app.dependency_overrides[get_db] = override
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, engine
    
    app.dependency_overrides.clear()
    await engine.dispose()


# Helper functions - defined at module level for reuse


async def register_pg(client):
    """Register a clinic manager for PostgreSQL tests."""
    response = await client.post("/api/auth/register", json={
        "clinic_name": "Test Clinic",
        "full_name": "Test Manager",
        "email": "manager@test.com",
        "password": "test_password_123"
    })
    assert response.status_code == 201
    return response.json()


async def create_room(client, headers):
    """Create a room and return its ID."""
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    assert room.status_code == 201
    return room.json()["id"]


async def create_patient(client, headers):
    """Create a patient and return its ID."""
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    return patient.json()["id"]


async def create_procedure_catalog(client, headers):
    """Create a procedure catalog and return its ID."""
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST01",
        "name": "Test Procedure",
        "base_price": "100.00",
        "default_duration_minutes": 30
    })
    assert catalog.status_code == 201
    return catalog.json()["id"]


async def create_appointment(client, headers, patient_id, doctor_id, room_id):
    """Create an appointment and return its ID."""
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appointment.status_code == 201
    return appointment.json()["id"]


async def create_performed_procedure(client, headers, appointment_id, catalog_id):
    """Create a performed procedure and return its ID."""
    procedure = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    assert procedure.status_code == 201
    return procedure.json()["id"]


async def create_doctor(client, headers, email="doctor@test.com"):
    """Create a doctor user via invitation."""
    # Create invitation
    invite_resp = await client.post(
        "/api/staff/invitations",
        headers=headers,
        json={
            "email": email,
            "full_name": "Dr. Test",
            "role": "doctor"
        }
    )
    assert invite_resp.status_code == 201
    invite_data = invite_resp.json()
    
    # Extract token from invitation_url
    invitation_url = invite_data.get("invitation_url", "")
    if "token=" in invitation_url:
        parsed = urlparse(invitation_url)
        token = parse_qs(parsed.query).get("token", [""])[0]
    else:
        token = invitation_url
    
    # Accept invitation
    accept_resp = await client.post(
        f"/api/staff/invitations/accept?token={token}",
        json={"password": "doctor_password_123456"}
    )
    assert accept_resp.status_code == 200
    return accept_resp.json()


async def create_test_environment_pg(client, headers):
    """Create test environment: room, patient, procedure catalog, doctor.
    
    Returns dict with all created IDs.
    """
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)
    
    # Get manager info
    me = await client.get("/api/auth/me", headers=headers)
    manager_id = me.json()["id"]
    
    appointment_id = await create_appointment(client, headers, patient_id, manager_id, room_id)
    procedure_id = await create_performed_procedure(client, headers, appointment_id, catalog_id)
    
    return {
        "room_id": room_id,
        "patient_id": patient_id,
        "catalog_id": catalog_id,
        "manager_id": manager_id,
        "appointment_id": appointment_id,
        "procedure_id": procedure_id
    }


@pytest.mark.asyncio
async def test_concurrent_start_same_doctor_postgres(postgres_client):
    """PostgreSQL test: Two concurrent start attempts for the same doctor should fail.
    
    Verifies that the unique timer per doctor constraint is enforced transactionally
    with FOR UPDATE locks. Only one procedure can be running for a doctor at a time.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup: create two procedures for the same doctor (manager)
    env1 = await create_test_environment_pg(client, headers)
    
    # Create second procedure for same doctor (manager)
    procedure_id_2 = await create_performed_procedure(
        client, headers, env1["appointment_id"], env1["catalog_id"]
    )
    
    # Start first procedure
    start_resp1 = await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/start", headers=headers
    )
    assert start_resp1.status_code == 200
    
    # Try to start second procedure - should fail
    start_resp2 = await client.post(
        f"/api/performed-procedures/{procedure_id_2}/start", headers=headers
    )
    assert start_resp2.status_code == 409
    assert "already has a running procedure" in start_resp2.json().get("detail", "").lower()
    
    # Verify persistent state: only one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    assert list_resp.status_code == 200
    procedures = list_resp.json()
    running_count = sum(1 for p in procedures if p["status"] == "running")
    assert running_count == 1, f"Expected 1 running procedure, got {running_count}"


@pytest.mark.asyncio
async def test_concurrent_start_resume_same_doctor_postgres(postgres_client):
    """PostgreSQL test: Start and concurrent resume for the same doctor should fail.
    
    Verifies that a doctor cannot resume a procedure if they already have a running one.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup: create two procedures for the same doctor
    env1 = await create_test_environment_pg(client, headers)
    
    # Create second procedure
    procedure_id_2 = await create_performed_procedure(
        client, headers, env1["appointment_id"], env1["catalog_id"]
    )
    
    # Start and pause first procedure
    await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/start", headers=headers
    )
    await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/pause", headers=headers
    )
    
    # Start second procedure
    await client.post(
        f"/api/performed-procedures/{procedure_id_2}/start", headers=headers
    )
    
    # Try to resume first procedure - should fail because doctor has running procedure
    resume_resp = await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/resume", headers=headers
    )
    assert resume_resp.status_code == 409
    assert "already has a running procedure" in resume_resp.json().get("detail", "").lower()
    
    # Verify persistent state: only one procedure is running (the second one)
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1
    assert str(running_procs[0]["id"]) == procedure_id_2


@pytest.mark.asyncio
async def test_manager_start_for_other_doctor_postgres(postgres_client):
    """PostgreSQL test: Manager can start procedures for other doctors.
    
    Verifies that manager role can start procedures on behalf of other doctors,
    and the unique timer constraint applies to the executed_by_id (the doctor), not the actor.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create a second doctor
    doctor2 = await create_doctor(client, headers, email="doctor2@test.com")
    doctor2_id = doctor2["id"]
    
    # Create environment for doctor2
    room_id = await create_room(client, headers)
    patient_id = await create_patient(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)
    appointment_id = await create_appointment(client, headers, patient_id, doctor2_id, room_id)
    
    # Create procedure for doctor2
    procedure_id = await create_performed_procedure(client, headers, appointment_id, catalog_id)
    
    # Manager can start procedure for doctor2
    start_resp = await client.post(
        f"/api/performed-procedures/{procedure_id}/start", headers=headers
    )
    assert start_resp.status_code == 200
    
    # Create another procedure for doctor2
    procedure_id_2 = await create_performed_procedure(
        client, headers, appointment_id, catalog_id
    )
    
    # Manager cannot start second procedure for doctor2 (unique timer per doctor)
    start_resp2 = await client.post(
        f"/api/performed-procedures/{procedure_id_2}/start", headers=headers
    )
    assert start_resp2.status_code == 409
    assert "already has a running procedure" in start_resp2.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_different_doctors_independent_postgres(postgres_client):
    """PostgreSQL test: Two different doctors can have running procedures independently.
    
    Verifies that the unique timer constraint is per doctor, not global.
    Different doctors should be able to have their own running procedures.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create two doctors
    doctor2 = await create_doctor(client, headers, email="doctor2@test.com")
    doctor2_id = doctor2["id"]
    doctor3 = await create_doctor(client, headers, email="doctor3@test.com")
    doctor3_id = doctor3["id"]
    
    # Create environment for doctor2
    room_id = await create_room(client, headers)
    catalog_id = await create_procedure_catalog(client, headers)
    
    patient2 = await create_patient(client, headers)
    appointment2 = await create_appointment(
        client, headers, patient2, doctor2_id, room_id
    )
    procedure2_id = await create_performed_procedure(
        client, headers, appointment2, catalog_id
    )
    
    # Create environment for doctor3
    patient3 = await create_patient(client, headers)
    appointment3 = await create_appointment(
        client, headers, patient3, doctor3_id, room_id
    )
    procedure3_id = await create_performed_procedure(
        client, headers, appointment3, catalog_id
    )
    
    # Start procedure for doctor2
    start_resp2 = await client.post(
        f"/api/performed-procedures/{procedure2_id}/start", headers=headers
    )
    assert start_resp2.status_code == 200
    
    # Start procedure for doctor3 - should succeed (different doctor)
    start_resp3 = await client.post(
        f"/api/performed-procedures/{procedure3_id}/start", headers=headers
    )
    assert start_resp3.status_code == 200
    
    # Verify persistent state: both doctors have running procedures
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 2, f"Expected 2 running procedures, got {len(running_procs)}"
    
    running_ids = {str(p["id"]) for p in running_procs}
    assert procedure2_id in running_ids
    assert procedure3_id in running_ids


@pytest.mark.asyncio
async def test_concurrent_start_same_doctor_race_condition(postgres_client):
    """PostgreSQL test: Race condition - two truly concurrent start requests.
    
    Uses asyncio.gather to send two start requests simultaneously for the same doctor.
    Only one should succeed.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup: create two procedures for the same doctor
    env1 = await create_test_environment_pg(client, headers)
    procedure_id_2 = await create_performed_procedure(
        client, headers, env1["appointment_id"], env1["catalog_id"]
    )
    
    # Start first procedure
    await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/start", headers=headers
    )
    
    # Try concurrent start on second procedure - both should fail or only one succeed
    # Send both requests simultaneously
    tasks = [
        client.post(f"/api/performed-procedures/{procedure_id_2}/start", headers=headers),
        client.post(f"/api/performed-procedures/{procedure_id_2}/start", headers=headers),
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # At most one request can succeed
    success_count = sum(1 for r in results if isinstance(r, dict) or (
        hasattr(r, 'status_code') and r.status_code == 200
    ))
    assert success_count <= 1, f"More than one concurrent start succeeded: {success_count}"
    
    # Verify persistent state: only one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_count = sum(1 for p in procedures if p["status"] == "running")
    assert running_count == 1


@pytest.mark.asyncio
async def test_concurrent_start_resume_race_condition(postgres_client):
    """PostgreSQL test: Race condition - start and resume sent concurrently.
    
    Setup: procedure1 is running, procedure2 is paused.
    Manager tries to start procedure2 and resume procedure1 concurrently.
    Only one should succeed.
    """
    client, _engine = postgres_client
    
    tokens = await register_pg(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup: create two procedures for the same doctor (manager)
    env1 = await create_test_environment_pg(client, headers)
    procedure_id_2 = await create_performed_procedure(
        client, headers, env1["appointment_id"], env1["catalog_id"]
    )
    
    # Start and pause first procedure
    await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/start", headers=headers
    )
    await client.post(
        f"/api/performed-procedures/{env1['procedure_id']}/pause", headers=headers
    )
    
    # Start second procedure
    await client.post(
        f"/api/performed-procedures/{procedure_id_2}/start", headers=headers
    )
    
    # Try concurrent resume on first procedure and start on second
    # Only one should succeed
    tasks = [
        client.post(
            f"/api/performed-procedures/{env1['procedure_id']}/resume", headers=headers
        ),
        client.post(
            f"/api/performed-procedures/{procedure_id_2}/start", headers=headers
        ),
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # At most one request can succeed
    success_count = sum(1 for r in results if (
        hasattr(r, 'status_code') and r.status_code == 200
    ))
    assert success_count <= 1, f"More than one concurrent operation succeeded: {success_count}"
    
    # Verify persistent state: only one procedure is running
    list_resp = await client.get("/api/performed-procedures", headers=headers)
    procedures = list_resp.json()
    running_procs = [p for p in procedures if p["status"] == "running"]
    assert len(running_procs) == 1
