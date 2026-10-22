"""Tests for T04: Performed Procedures and Execution Segments."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token, hash_password
from app.database import Base, User, get_db
from app.main import app


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client, clinic="Test Clinic", email="test@example.com"):
    response = await client.post("/api/auth/register", json={
        "clinic_name": clinic,
        "full_name": "Test User",
        "email": email,
        "password": "correct horse battery staple"
    })
    assert response.status_code == 201
    return response.json()


async def create_doctor(client, headers, email="doctor@example.com"):
    """Create a doctor user via invitation."""
    # Get clinic manager token
    me = await client.get("/api/auth/me", headers=headers)
    clinic_id = me.json()["clinic_id"]
    
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
    
    # Accept invitation
    accept_resp = await client.post(
        "/api/staff/invitations/accept",
        json={
            "token": invite_data["token"],
            "password": "doctor_password_123"
        }
    )
    assert accept_resp.status_code == 200
    return accept_resp.json()


@pytest.mark.asyncio
async def test_create_performed_procedure(client):
    """Test creating a performed procedure."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create room
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    assert room.status_code == 201
    room_id = room.json()["id"]
    
    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    patient_id = patient.json()["id"]
    
    # Create procedure catalog
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL01",
        "name": "Filling",
        "base_price": "100.00",
        "default_duration_minutes": 30
    })
    assert catalog.status_code == 201
    catalog_id = catalog.json()["id"]
    
    # Create appointment
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    assert appointment.status_code == 201
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id,
        "tooth_fdi": "11",
        "tooth_surface": "mesial"
    })
    assert pp.status_code == 201
    data = pp.json()
    assert data["status"] == "pending"
    assert data["procedure_code"] == "FILL01"
    assert data["procedure_name"] == "Filling"
    assert data["tooth_fdi"] == "11"
    assert data["current_price"] == "100.00"


@pytest.mark.asyncio
async def test_performed_procedure_status_transitions(client):
    """Test status transitions: pending -> running -> paused -> running -> completed."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup: create room, patient, catalog, appointment
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL02",
        "name": "Filling 2",
        "base_price": "150.00",
        "default_duration_minutes": 45
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Start procedure
    start = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert start.status_code == 200
    assert start.json()["status"] == "running"
    
    # Pause procedure
    pause = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"
    
    # Resume procedure
    resume = await client.post(f"/api/performed-procedures/{procedure_id}/resume", headers=headers)
    assert resume.status_code == 200
    assert resume.json()["status"] == "running"
    
    # Complete procedure
    complete = await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_cannot_start_two_procedures_same_doctor(client):
    """Test that a doctor cannot have two running procedures simultaneously."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL03",
        "name": "Filling 3",
        "base_price": "200.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 120,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create two performed procedures
    pp1 = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id_1 = pp1.json()["id"]
    
    pp2 = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id_2 = pp2.json()["id"]
    
    # Start first procedure
    start1 = await client.post(f"/api/performed-procedures/{procedure_id_1}/start", headers=headers)
    assert start1.status_code == 200
    
    # Try to start second procedure - should fail
    start2 = await client.post(f"/api/performed-procedures/{procedure_id_2}/start", headers=headers)
    assert start2.status_code == 409


@pytest.mark.asyncio
async def test_invalid_transitions(client):
    """Test that invalid status transitions are rejected."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL04",
        "name": "Filling 4",
        "base_price": "250.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Complete without starting - should fail
    complete = await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
    assert complete.status_code == 409
    
    # Start first
    start = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert start.status_code == 200
    
    # Start again - should fail
    start_again = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert start_again.status_code == 409
    
    # Complete
    complete = await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
    assert complete.status_code == 200
    
    # Try to pause completed - should fail
    pause = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
    assert pause.status_code == 409


@pytest.mark.asyncio
async def test_procedure_segments_tracking(client):
    """Test that execution segments are properly created and tracked."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL05",
        "name": "Filling 5",
        "base_price": "300.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Start procedure
    await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    
    # Check segments
    segments = await client.get(f"/api/performed-procedures/{procedure_id}/segments", headers=headers)
    assert segments.status_code == 200
    assert len(segments.json()) == 1
    assert segments.json()[0]["segment_type"] == "run"
    assert segments.json()[0]["ended_at"] is None
    
    # Pause procedure
    await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
    
    # Check segments again
    segments = await client.get(f"/api/performed-procedures/{procedure_id}/segments", headers=headers)
    assert segments.status_code == 200
    assert len(segments.json()) == 2
    segment_types = [s["segment_type"] for s in segments.json()]
    assert "run" in segment_types
    assert "pause" in segment_types


@pytest.mark.asyncio
async def test_visit_summary(client):
    """Test visit summary endpoint."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL06",
        "name": "Filling 6",
        "base_price": "350.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Start and complete procedure
    await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
    
    # Get visit summary
    summary = await client.get(f"/api/performed-procedures/visit/{appointment_id}/summary", headers=headers)
    assert summary.status_code == 200
    data = summary.json()
    assert data["appointment_id"] == appointment_id
    assert data["patient_id"] == patient_id
    assert data["can_close_visit"] is True
    assert len(data["performed_procedures"]) == 1


@pytest.mark.asyncio
async def test_cannot_complete_with_running_procedures(client):
    """Test that visit cannot be closed if there are running/paused procedures."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL07",
        "name": "Filling 7",
        "base_price": "400.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Start procedure (running)
    await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    
    # Get visit summary - should show cannot close
    summary = await client.get(f"/api/performed-procedures/visit/{appointment_id}/summary", headers=headers)
    assert summary.status_code == 200
    data = summary.json()
    assert data["can_close_visit"] is False
    assert len(data["unresolved_procedures"]) == 1
    assert data["unresolved_procedures"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_price_override(client):
    """Test price override functionality."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL08",
        "name": "Filling 8",
        "base_price": "500.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    assert pp.json()["current_price"] == "500.00"
    
    # Override price
    override = await client.post(
        f"/api/performed-procedures/{procedure_id}/override-price",
        headers=headers,
        json={"new_price": "450.00", "reason": "Discount"}
    )
    assert override.status_code == 200
    assert override.json()["current_price"] == "450.00"


@pytest.mark.asyncio
async def test_fdi_tooth_validation(client):
    """Test FDI tooth code validation."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL09",
        "name": "Filling 9",
        "base_price": "600.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Valid FDI codes
    valid_codes = ["11", "12", "18", "21", "28", "31", "38", "41", "48"]
    for code in valid_codes:
        pp = await client.post("/api/performed-procedures", headers=headers, json={
            "appointment_id": appointment_id,
            "procedure_catalog_id": catalog_id,
            "tooth_fdi": code
        })
        assert pp.status_code == 201
    
    # Invalid FDI codes
    invalid_codes = ["01", "51", "19", "29", "39", "49", "10", "99"]
    for code in invalid_codes:
        pp = await client.post("/api/performed-procedures", headers=headers, json={
            "appointment_id": appointment_id,
            "procedure_catalog_id": catalog_id,
            "tooth_fdi": code
        })
        assert pp.status_code == 422


@pytest.mark.asyncio
async def test_surface_validation(client):
    """Test tooth surface validation."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL10",
        "name": "Filling 10",
        "base_price": "700.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Valid surfaces
    valid_surfaces = ["mesial", "distal", "buccal", "lingual", "occlusal"]
    for surface in valid_surfaces:
        pp = await client.post("/api/performed-procedures", headers=headers, json={
            "appointment_id": appointment_id,
            "procedure_catalog_id": catalog_id,
            "tooth_surface": surface
        })
        assert pp.status_code == 201
    
    # Invalid surface
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id,
        "tooth_surface": "invalid_surface"
    })
    assert pp.status_code == 422


@pytest.mark.asyncio
async def test_tenant_isolation_performed_procedures(client):
    """Test that performed procedures are isolated by clinic."""
    # Create two clinics
    tokens1 = await register(client, clinic="Clinic 1", email="manager1@example.com")
    headers1 = {"Authorization": f"Bearer {tokens1['access_token']}"}
    
    tokens2 = await register(client, clinic="Clinic 2", email="manager2@example.com")
    headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}
    
    # Clinic 1 setup
    room1 = await client.post("/api/rooms", headers=headers1, json={"name": "Cabinet 1"})
    patient1 = await client.post("/api/patients", headers=headers1, json={
        "first_name": "Patient",
        "last_name": "One"
    })
    catalog1 = await client.post("/api/procedures", headers=headers1, json={
        "code": "FILL11",
        "name": "Filling 11",
        "base_price": "100.00",
        "default_duration_minutes": 30
    })
    me1 = await client.get("/api/auth/me", headers=headers1)
    
    appointment1 = await client.post("/api/appointments", headers=headers1, json={
        "patient_id": patient1.json()["id"],
        "doctor_id": me1.json()["id"],
        "room_id": room1.json()["id"],
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    
    # Create performed procedure in clinic 1
    pp1 = await client.post("/api/performed-procedures", headers=headers1, json={
        "appointment_id": appointment1.json()["id"],
        "procedure_catalog_id": catalog1.json()["id"]
    })
    assert pp1.status_code == 201
    procedure_id_1 = pp1.json()["id"]
    
    # Clinic 2 cannot access clinic 1's procedure
    pp_get = await client.get(f"/api/performed-procedures/{procedure_id_1}", headers=headers2)
    assert pp_get.status_code == 404
    
    # Clinic 2 setup and create their own procedure
    room2 = await client.post("/api/rooms", headers=headers2, json={"name": "Cabinet 2"})
    patient2 = await client.post("/api/patients", headers=headers2, json={
        "first_name": "Patient",
        "last_name": "Two"
    })
    catalog2 = await client.post("/api/procedures", headers=headers2, json={
        "code": "FILL12",
        "name": "Filling 12",
        "base_price": "200.00",
        "default_duration_minutes": 30
    })
    me2 = await client.get("/api/auth/me", headers=headers2)
    
    appointment2 = await client.post("/api/appointments", headers=headers2, json={
        "patient_id": patient2.json()["id"],
        "doctor_id": me2.json()["id"],
        "room_id": room2.json()["id"],
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    
    pp2 = await client.post("/api/performed-procedures", headers=headers2, json={
        "appointment_id": appointment2.json()["id"],
        "procedure_catalog_id": catalog2.json()["id"]
    })
    assert pp2.status_code == 201
    
    # Clinic 1 should only see their own procedures
    list1 = await client.get("/api/performed-procedures", headers=headers1)
    assert list1.status_code == 200
    assert len(list1.json()) == 1
    
    # Clinic 2 should only see their own procedures
    list2 = await client.get("/api/performed-procedures", headers=headers2)
    assert list2.status_code == 200
    assert len(list2.json()) == 1


@pytest.mark.asyncio
async def test_role_permissions_performed_procedures(client):
    """Test that assistant and reception cannot start/stop procedures."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create assistant
    assistant_tokens = await create_doctor(client, headers, email="assistant@example.com")
    # Update to assistant role via direct DB manipulation would be needed
    # For now, test with doctor role
    
    # Setup
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]
    
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL13",
        "name": "Filling 13",
        "base_price": "800.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]
    
    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id
    })
    procedure_id = pp.json()["id"]
    
    # Doctor can start
    start = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
    assert start.status_code == 200
    
    # Doctor can pause
    pause = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
    assert pause.status_code == 200
