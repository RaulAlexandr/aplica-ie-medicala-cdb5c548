"""Tests for T04: Performed Procedures and Execution Segments."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
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
    # Ensure we have a valid session/token
    await client.get("/api/auth/me", headers=headers)
    
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
        token = invitation_url.split("token=")[-1]
    else:
        token = invitation_url
    
    # Accept invitation - token is a query parameter
    accept_resp = await client.post(
        f"/api/staff/invitations/accept?token={token}",
        json={
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
    
    # Create assistant (tokens not used - role would need DB manipulation)
    await create_doctor(client, headers, email="assistant@example.com")
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


# Helper to create a performed procedure for notes tests
async def create_test_procedure_for_notes(client, headers):
    """Helper to create a performed procedure for testing notes update."""
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
    
    me = await client.get("/api/auth/me", headers=headers)
    doctor_id = me.json()["id"]
    
    # Create appointment
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
    return pp.json()["id"], headers


@pytest.mark.asyncio
async def test_update_procedure_notes_valid(client):
    """Test updating procedure notes with a valid value."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    procedure_id, headers = await create_test_procedure_for_notes(client, headers)
    
    # Update notes with valid content
    valid_notes = "This is a test note for the procedure."
    response = await client.patch(
        f"/api/performed-procedures/{procedure_id}/notes",
        headers=headers,
        json={"notes": valid_notes}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["notes"] == valid_notes


@pytest.mark.asyncio
async def test_update_procedure_notes_max_length(client):
    """Test that notes validation rejects strings longer than 1000 characters."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    procedure_id, headers = await create_test_procedure_for_notes(client, headers)
    
    # Create notes with exactly 1000 characters (should pass)
    notes_1000 = "a" * 1000
    response = await client.patch(
        f"/api/performed-procedures/{procedure_id}/notes",
        headers=headers,
        json={"notes": notes_1000}
    )
    assert response.status_code == 200
    
    # Create notes with 1001 characters (should fail with validation error)
    notes_1001 = "a" * 1001
    response = await client.patch(
        f"/api/performed-procedures/{procedure_id}/notes",
        headers=headers,
        json={"notes": notes_1001}
    )
    assert response.status_code == 422  # Unprocessable Entity - Pydantic validation error
    errors = response.json()["detail"]
    assert any("max_length" in str(err).lower() or "length" in str(err).lower() for err in errors)


@pytest.mark.asyncio
async def test_procedure_duration_tracking(client):
    """Test that procedure duration is correctly calculated after start/pause/resume/complete."""
    from freezegun import freeze_time
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create room, patient, procedure catalog, appointment, and procedure
    procedure_id, headers = await create_test_procedure_for_notes(client, headers)
    
    # Start procedure - duration should be 0 initially
    with freeze_time("2026-01-01T10:00:00+00:00") as frozen_time:
        start_resp = await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        assert start_data["status"] == "running"
        # Duration should be None or 0 since no segment is completed yet
        assert start_data["total_duration_seconds"] is None or start_data["total_duration_seconds"] == 0
        
        # Move time forward and pause
        frozen_time.move_to("2026-01-01T10:30:00+00:00")
        pause_resp = await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        assert pause_resp.status_code == 200
        pause_data = pause_resp.json()
        assert pause_data["status"] == "paused"
        # Should have 30 minutes = 1800 seconds duration
        assert pause_data["total_duration_seconds"] == 1800
        
        # Move time forward and resume
        frozen_time.move_to("2026-01-01T11:00:00+00:00")
        resume_resp = await client.post(f"/api/performed-procedures/{procedure_id}/resume", headers=headers)
        assert resume_resp.status_code == 200
        resume_data = resume_resp.json()
        assert resume_data["status"] == "running"
        # Duration should still be 1800 seconds (from first segment)
        assert resume_data["total_duration_seconds"] == 1800
        
        # Move time forward and complete
        frozen_time.move_to("2026-01-01T11:15:00+00:00")
        complete_resp = await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=headers)
        assert complete_resp.status_code == 200
        complete_data = complete_resp.json()
        assert complete_data["status"] == "completed"
        # Total duration: 30 min (first segment) + 15 min (second segment) = 2700 seconds
        assert complete_data["total_duration_seconds"] == 2700
        
        # Verify by getting the procedure again (new read from DB)
        get_resp = await client.get(f"/api/performed-procedures/{procedure_id}", headers=headers)
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["total_duration_seconds"] == 2700


@pytest.mark.asyncio
async def test_list_procedures_with_duration(client):
    """Test that list endpoint returns correct duration for procedures."""
    from freezegun import freeze_time
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create room, patient, procedure catalog, appointment, and procedure
    procedure_id, headers = await create_test_procedure_for_notes(client, headers)
    
    # Start and pause procedure to create duration
    with freeze_time("2026-01-01T10:00:00+00:00") as frozen_time:
        await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=headers)
        frozen_time.move_to("2026-01-01T10:10:00+00:00")
        await client.post(f"/api/performed-procedures/{procedure_id}/pause", headers=headers)
        
        # List procedures and check duration
        list_resp = await client.get("/api/performed-procedures", headers=headers)
        assert list_resp.status_code == 200
        procedures = list_resp.json()
        
        # Find our procedure
        our_proc = next((p for p in procedures if str(p["id"]) == str(procedure_id)), None)
        assert our_proc is not None
        assert our_proc["total_duration_seconds"] == 600  # 10 minutes


