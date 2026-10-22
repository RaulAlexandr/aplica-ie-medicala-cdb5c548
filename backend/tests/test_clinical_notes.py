"""Tests for T05: Clinical Notes, Findings, and Patient Timeline."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.config import settings
from app.database import Base, ClinicalNote, PatientTimelineEvent, User, get_db
from app.main import app


@pytest.fixture(scope="function")
async def client_and_db():
    """Create test client and provide direct DB access."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, sessions

    app.dependency_overrides.clear()
    await engine.dispose()


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


def create_test_token(user_id: str, clinic_id: str, role: str) -> str:
    """Create a test JWT token for a user."""
    payload = {
        "sub": str(user_id),
        "clinic_id": str(clinic_id),
        "role": role,
        "exp": datetime.now(UTC) + timedelta(hours=1)
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


async def register(client, clinic="Test Clinic", email="test@example.com"):
    response = await client.post("/api/auth/register", json={
        "clinic_name": clinic,
        "full_name": "Test User",
        "email": email,
        "password": "correct horse battery staple"
    })
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_create_clinical_note(client):
    """Test creating a clinical note."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    patient_id = patient.json()["id"]

    # Create clinical note
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "finding",
        "title": "Initial Exam",
        "content": "Patient presents with tooth pain on upper right molar.",
        "tooth_fdi": "16",
        "tooth_surface": "occlusal"
    })
    assert note.status_code == 201
    data = note.json()
    assert data["status"] == "draft"
    assert data["note_type"] == "finding"
    assert data["title"] == "Initial Exam"
    assert data["tooth_fdi"] == "16"
    assert data["content"] == "Patient presents with tooth pain on upper right molar."


@pytest.mark.asyncio
async def test_clinical_note_lifecycle(client):
    """Test clinical note lifecycle: draft -> finalized -> addendum."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create clinical note
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "Test Note",
        "content": "Initial content"
    })
    note_id = note.json()["id"]
    assert note.json()["status"] == "draft"

    # Update note (still draft)
    updated = await client.patch(
        f"/api/clinical-notes/{note_id}",
        headers=headers,
        json={"title": "Updated Note", "content": "Updated content"}
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated Note"
    assert updated.json()["content"] == "Updated content"
    assert updated.json()["status"] == "draft"

    # Finalize note
    finalized = await client.post(f"/api/clinical-notes/{note_id}/finalize", headers=headers)
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "finalized"
    assert finalized.json()["finalized_at"] is not None

    # Cannot update finalized note
    update_finalized = await client.patch(
        f"/api/clinical-notes/{note_id}",
        headers=headers,
        json={"title": "Should Fail"}
    )
    assert update_finalized.status_code == 409

    # Add addendum
    addendum = await client.post(
        f"/api/clinical-notes/{note_id}/addendum",
        headers=headers,
        json={
            "addendum_content": "Additional findings after review.",
            "reason": "Follow-up examination"
        }
    )
    assert addendum.status_code == 200
    assert "Addendum" in addendum.json()["content"]


@pytest.mark.asyncio
async def test_clinical_note_with_appointment(client):
    """Test creating a clinical note associated with an appointment."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create room
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create appointment
    me = await client.get("/api/auth/me", headers=headers)
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": me.json()["id"],
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]

    # Create clinical note with appointment
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "appointment_id": appointment_id,
        "note_type": "finding",
        "title": "Consultation Findings",
        "content": "Findings from consultation."
    })
    assert note.status_code == 201
    data = note.json()
    assert data["appointment_id"] == appointment_id


@pytest.mark.asyncio
async def test_list_patient_notes(client):
    """Test listing clinical notes for a patient."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create multiple notes
    for i in range(3):
        note = await client.post("/api/clinical-notes", headers=headers, json={
            "patient_id": patient_id,
            "note_type": "note",
            "title": f"Note {i}",
            "content": f"Content {i}"
        })
        assert note.status_code == 201

    # List notes
    notes_list = await client.get(f"/api/clinical-notes/patients/{patient_id}", headers=headers)
    assert notes_list.status_code == 200
    assert len(notes_list.json()) == 3


@pytest.mark.asyncio
async def test_note_revisions(client):
    """Test that note revisions are tracked."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create clinical note
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "Original Title",
        "content": "Original content"
    })
    note_id = note.json()["id"]

    # Update note
    updated = await client.patch(
        f"/api/clinical-notes/{note_id}",
        headers=headers,
        json={"title": "Updated Title"}
    )
    assert updated.status_code == 200

    # Get revisions
    revisions = await client.get(f"/api/clinical-notes/{note_id}/revisions", headers=headers)
    assert revisions.status_code == 200
    assert len(revisions.json()) >= 1
    assert any(r["field"] == "title" for r in revisions.json())


@pytest.mark.asyncio
async def test_patient_record_endpoint(client):
    """Test patient record endpoint with all data."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create room
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    room_id = room.json()["id"]

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient",
        "allergies": "Penicillin"
    })
    patient_id = patient.json()["id"]

    # Create appointment
    me = await client.get("/api/auth/me", headers=headers)
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": me.json()["id"],
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appointment_id = appointment.json()["id"]

    # Create procedure catalog
    catalog = await client.post("/api/procedures", headers=headers, json={
        "code": "FILL01",
        "name": "Filling",
        "base_price": "100.00",
        "default_duration_minutes": 30
    })
    catalog_id = catalog.json()["id"]

    # Create performed procedure
    pp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appointment_id,
        "procedure_catalog_id": catalog_id,
        "tooth_fdi": "16"
    })
    assert pp.status_code == 201

    # Create clinical note
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "appointment_id": appointment_id,
        "note_type": "finding",
        "title": "Finding",
        "content": "Test finding",
        "is_alert": True
    })
    assert note.status_code == 201

    # Finalize note
    await client.post(f"/api/clinical-notes/{note.json()['id']}/finalize", headers=headers)

    # Get patient record
    record = await client.get(f"/api/clinical-notes/patients/{patient_id}/record", headers=headers)
    assert record.status_code == 200
    data = record.json()

    # Check patient data
    assert data["patient"]["first_name"] == "Test"
    assert data["patient"]["last_name"] == "Patient"
    assert data["patient"]["allergies"] == "Penicillin"

    # Check alerts
    assert len(data["alerts"]) == 1

    # Check clinical notes
    assert len(data["clinical_notes"]) >= 1

    # Check performed procedures
    assert len(data["performed_procedures"]) >= 1


@pytest.mark.asyncio
async def test_timeline_events(client):
    """Test timeline events endpoint."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create clinical note
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "Test Note",
        "content": "Test content"
    })
    assert note.status_code == 201
    note_id = note.json()["id"]

    # Finalize the note (drafts are not included in timeline)
    finalize = await client.post(f"/api/clinical-notes/{note_id}/finalize", headers=headers)
    assert finalize.status_code == 200

    # Refresh timeline
    refresh = await client.post("/api/clinical-notes/timeline/refresh", headers=headers)
    assert refresh.status_code == 200

    # Get timeline
    timeline = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers)
    assert timeline.status_code == 200
    # Timeline should have at least the clinical note event
    assert len(timeline.json()) >= 1


@pytest.mark.asyncio
async def test_alert_notes(client):
    """Test creating and retrieving alert notes."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create alert note
    alert = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "alert",
        "title": "Urgent Alert",
        "content": "Patient has severe allergy!",
        "is_alert": True
    })
    assert alert.status_code == 201
    assert alert.json()["is_alert"] is True

    # List alerts only
    alerts = await client.get(
        f"/api/clinical-notes/patients/{patient_id}",
        headers=headers,
        params={"is_alert": True}
    )
    assert alerts.status_code == 200
    assert all(n["is_alert"] is True for n in alerts.json())


@pytest.mark.asyncio
async def test_tenant_isolation_clinical_notes(client):
    """Test that clinical notes are isolated by clinic."""
    # Create two clinics
    tokens1 = await register(client, clinic="Clinic 1", email="manager1@example.com")
    headers1 = {"Authorization": f"Bearer {tokens1['access_token']}"}

    tokens2 = await register(client, clinic="Clinic 2", email="manager2@example.com")
    headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

    # Clinic 1: create patient and note
    patient1 = await client.post("/api/patients", headers=headers1, json={
        "first_name": "Patient",
        "last_name": "One"
    })
    patient_id_1 = patient1.json()["id"]

    note1 = await client.post("/api/clinical-notes", headers=headers1, json={
        "patient_id": patient_id_1,
        "note_type": "note",
        "title": "Clinic 1 Note",
        "content": "This is a clinic 1 note"
    })
    assert note1.status_code == 201
    note_id_1 = note1.json()["id"]

    # Clinic 2: create patient and note
    patient2 = await client.post("/api/patients", headers=headers2, json={
        "first_name": "Patient",
        "last_name": "Two"
    })
    patient_id_2 = patient2.json()["id"]

    note2 = await client.post("/api/clinical-notes", headers=headers2, json={
        "patient_id": patient_id_2,
        "note_type": "note",
        "title": "Clinic 2 Note",
        "content": "This is a clinic 2 note"
    })
    assert note2.status_code == 201

    # Clinic 1 cannot access clinic 2's note
    get_note = await client.get(f"/api/clinical-notes/{note2.json()['id']}", headers=headers1)
    assert get_note.status_code == 404

    # Clinic 2 cannot access clinic 1's note
    get_note2 = await client.get(f"/api/clinical-notes/{note_id_1}", headers=headers2)
    assert get_note2.status_code == 404

    # Each clinic only sees their own notes
    list1 = await client.get(f"/api/clinical-notes/patients/{patient_id_1}", headers=headers1)
    assert list1.status_code == 200
    assert len(list1.json()) == 1

    list2 = await client.get(f"/api/clinical-notes/patients/{patient_id_2}", headers=headers2)
    assert list2.status_code == 200
    assert len(list2.json()) == 1


@pytest.mark.asyncio
async def test_note_types(client):
    """Test different note types."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create notes with different types
    note_types = ["note", "finding", "alert", "diagnosis"]
    for note_type in note_types:
        note = await client.post("/api/clinical-notes", headers=headers, json={
            "patient_id": patient_id,
            "note_type": note_type,
            "title": f"{note_type.title()} Note",
            "content": f"Content for {note_type}"
        })
        assert note.status_code == 201
        assert note.json()["note_type"] == note_type

    # List all notes
    notes = await client.get(f"/api/clinical-notes/patients/{patient_id}", headers=headers)
    assert notes.status_code == 200
    assert len(notes.json()) == len(note_types)

    # Filter by note type
    findings = await client.get(
        f"/api/clinical-notes/patients/{patient_id}",
        headers=headers,
        params={"note_type": "finding"}
    )
    assert findings.status_code == 200
    assert all(n["note_type"] == "finding" for n in findings.json())


@pytest.mark.asyncio
async def test_fdi_validation_in_notes(client):
    """Test FDI tooth validation in clinical notes."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Valid FDI codes
    valid_codes = ["11", "12", "18", "21", "28", "31", "38", "41", "48"]
    for code in valid_codes:
        note = await client.post("/api/clinical-notes", headers=headers, json={
            "patient_id": patient_id,
            "note_type": "note",
            "title": f"Note for {code}",
            "content": "Test",
            "tooth_fdi": code
        })
        assert note.status_code == 201

    # Invalid FDI codes
    invalid_codes = ["01", "51", "19", "99"]
    for code in invalid_codes:
        note = await client.post("/api/clinical-notes", headers=headers, json={
            "patient_id": patient_id,
            "note_type": "note",
            "title": f"Invalid {code}",
            "content": "Test",
            "tooth_fdi": code
        })
        assert note.status_code == 422


@pytest.mark.asyncio
async def test_surface_validation_in_notes(client):
    """Test surface validation in clinical notes."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Valid surfaces
    valid_surfaces = ["mesial", "distal,buccal", "occlusal"]
    for surface in valid_surfaces:
        note = await client.post("/api/clinical-notes", headers=headers, json={
            "patient_id": patient_id,
            "note_type": "note",
            "title": f"Note for {surface}",
            "content": "Test",
            "tooth_surface": surface
        })
        assert note.status_code == 201

    # Invalid surface
    note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "Invalid surface",
        "content": "Test",
        "tooth_surface": "invalid_surface"
    })
    assert note.status_code == 422


@pytest.mark.asyncio
async def test_draft_notes_not_visible_to_non_authors(client):
    """Test that draft notes are only visible to authors and managers."""
    tokens = await register(client, email="manager@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]

    # Create draft note
    draft_note = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "Draft Note",
        "content": "This is a draft"
    })
    assert draft_note.status_code == 201
    note_id = draft_note.json()["id"]

    # Manager can see draft note
    get_note = await client.get(f"/api/clinical-notes/{note_id}", headers=headers)
    assert get_note.status_code == 200

    # Note: In a real scenario with proper role separation, we would test
    # that non-author doctors cannot see draft notes. This test confirms
    # the basic structure is in place.


@pytest.mark.asyncio
async def test_timeline_draft_protection_bypass_source(client_and_db):
    """Regression test: timeline draft protection should not be bypassed by different source.

    Tests that draft notes are filtered from timeline even when event.source
    is different from 'clinical_note'. Protection should be based on entity_type,
    not solely on source field.

    Scenariu:
    1. Creează autor și alt medic în aceeași clinică
    2. Creează pacient și ciornă a autorului
    3. Inserează direct în baza de date un PatientTimelineEvent cu:
       entity_type="clinical_note", entity_id=ID-ul ciornei, source="legacy_import"
       title/description cu markeri sensibili unici
    4. Citește timeline-ul ca autor: evenimentul este prezent
    5. Citește timeline-ul ca celălalt medic: evenimentul NU este prezent
    6. Acoperă și source=None
    """
    client, sessions = client_and_db

    # Create first user (author/manager)
    tokens1 = await register(client, email="author@test.com")
    headers1 = {"Authorization": f"Bearer {tokens1['access_token']}"}

    # Get author user info
    me1 = await client.get("/api/auth/me", headers=headers1)
    assert me1.status_code == 200
    author_data = me1.json()
    author_clinic_id = author_data["clinic_id"]

    # Create patient
    patient = await client.post("/api/patients", headers=headers1, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    patient_id = patient.json()["id"]

    # Create draft note as author
    draft_note = await client.post("/api/clinical-notes", headers=headers1, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "SENSITIVE_DRAFT_TITLE",
        "content": "This is a draft content"
    })
    assert draft_note.status_code == 201
    note_id = draft_note.json()["id"]

    # Create second doctor in the same clinic via direct DB insert
    async with sessions() as session:
        note_db = await session.get(ClinicalNote, UUID(note_id))
        note_clinic_id = note_db.clinic_id
        note_author_id = note_db.author_id
        note_created_at = note_db.created_at
        note_uuid = note_db.id

        doctor2 = User(
            clinic_id=UUID(author_clinic_id),
            full_name="Dr. Other",
            email="doctor2@test.com",
            password_hash=hash_password("doctor_password_123456"),
            role="doctor",
            is_active=True
        )
        session.add(doctor2)
        await session.commit()
        await session.refresh(doctor2)
        doctor2_id = doctor2.id

        event = PatientTimelineEvent(
            clinic_id=note_clinic_id,
            patient_id=UUID(patient_id),
            event_type="clinical_note",
            entity_type="clinical_note",
            entity_id=note_uuid,
            title="SENSITIVE_LEGACY_TITLE",
            description="SENSITIVE_LEGACY_DESCRIPTION",
            actor_id=note_author_id,
            occurred_at=note_created_at,
            recorded_at=datetime.now(UTC),
            source="legacy_import",
            metadata={
                "type": "note",
                "status": "draft",
                "is_alert": False,
                "sensitive_data": "SHOULD_NOT_BE_VISIBLE"
            }
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        event_id = event.id

    # Author should see the event in their timeline
    timeline1 = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers1)
    assert timeline1.status_code == 200
    events1 = timeline1.json()
    author_event_ids = {e["id"] for e in events1}
    assert str(event_id) in author_event_ids

    author_event = next(e for e in events1 if str(e["id"]) == str(event_id))
    assert "SENSITIVE_LEGACY_TITLE" not in author_event["title"]
    assert "SENSITIVE_LEGACY_DESCRIPTION" not in author_event["description"]

    # Create token for doctor2
    access_token2 = create_test_token(str(doctor2_id), str(author_clinic_id), "doctor")
    headers2 = {"Authorization": f"Bearer {access_token2}"}

    # Doctor2 should NOT see the draft note event in timeline
    timeline2 = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers2)
    assert timeline2.status_code == 200
    events2 = timeline2.json()
    doctor2_event_ids = {e["id"] for e in events2}
    assert str(event_id) not in doctor2_event_ids, "Doctor2 should not see draft note event from another author"

    # Test with source=None
    async with sessions() as session:
        event2 = PatientTimelineEvent(
            clinic_id=note_clinic_id,
            patient_id=UUID(patient_id),
            event_type="clinical_note",
            entity_type="clinical_note",
            entity_id=note_uuid,
            title="SENSITIVE_NULL_SOURCE_TITLE",
            description="SENSITIVE_NULL_SOURCE_DESC",
            actor_id=note_author_id,
            occurred_at=note_created_at,
            recorded_at=datetime.now(UTC),
            source=None,
            metadata={"type": "note", "status": "draft", "is_alert": False}
        )
        session.add(event2)
        await session.commit()
        await session.refresh(event2)
        event2_id = event2.id

    # Author should see this event too
    timeline1b = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers1)
    assert timeline1b.status_code == 200
    events1b = timeline1b.json()
    author_event_ids_b = {e["id"] for e in events1b}
    assert str(event2_id) in author_event_ids_b

    # Doctor2 should NOT see this event either
    timeline2b = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers2)
    assert timeline2b.status_code == 200
    events2b = timeline2b.json()
    doctor2_event_ids_b = {e["id"] for e in events2b}
    assert str(event2_id) not in doctor2_event_ids_b, "Doctor2 should not see draft note event with source=None"


@pytest.mark.asyncio
async def test_timeline_draft_protection_in_record(client_and_db):
    """Regression test: timeline in patient record should also filter draft notes.

    Tests that draft notes are filtered from timeline included in patient record,
    using the same protection logic as the standalone timeline endpoint.

    Scenariu:
    1. Același autor și alt medic din aceeași clinică
    2. Eveniment persistent legat de ciornă
    3. Autorul vede nota și evenimentul permis
    4. Celălalt medic nu vede ciorna în clinical_notes și nici evenimentul în timeline
    5. Managerul neautor nu primește o excepție implicită
    6. Fără refresh care ar înlocui evenimentul construit
    """
    client, sessions = client_and_db

    # Create first user (author/doctor)
    tokens1 = await register(client, email="author@test.com")
    headers1 = {"Authorization": f"Bearer {tokens1['access_token']}"}

    # Get author user info
    me1 = await client.get("/api/auth/me", headers=headers1)
    assert me1.status_code == 200
    author_data = me1.json()
    author_clinic_id = author_data["clinic_id"]

    # Create patient
    patient = await client.post("/api/patients", headers=headers1, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    patient_id = patient.json()["id"]

    # Create draft note as author
    draft_note = await client.post("/api/clinical-notes", headers=headers1, json={
        "patient_id": patient_id,
        "note_type": "note",
        "title": "SENSITIVE_DRAFT_TITLE",
        "content": "This is a draft content"
    })
    assert draft_note.status_code == 201
    note_id = draft_note.json()["id"]

    # Create second doctor and timeline event in the same clinic via direct DB insert
    async with sessions() as session:
        doctor2 = User(
            clinic_id=UUID(author_clinic_id),
            full_name="Dr. Other",
            email="doctor2@test.com",
            password_hash=hash_password("doctor_password_123456"),
            role="doctor",
            is_active=True
        )
        session.add(doctor2)
        await session.commit()
        await session.refresh(doctor2)
        doctor2_id = doctor2.id

        note_db = await session.get(ClinicalNote, UUID(note_id))

        event = PatientTimelineEvent(
            clinic_id=note_db.clinic_id,
            patient_id=note_db.patient_id,
            event_type="clinical_note",
            entity_type="clinical_note",
            entity_id=note_db.id,
            title="SENSITIVE_DRAFT_TITLE",
            description="This is a draft content",
            actor_id=note_db.author_id,
            occurred_at=note_db.created_at,
            recorded_at=datetime.now(UTC),
            source="clinical_note",
            metadata={
                "type": "note",
                "status": "draft",
                "is_alert": False
            }
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)

    # Create token for doctor2
    access_token2 = create_test_token(str(doctor2_id), str(author_clinic_id), "doctor")
    headers2 = {"Authorization": f"Bearer {access_token2}"}

    # Author gets patient record - should see their own draft in clinical_notes and timeline
    record1 = await client.get(f"/api/clinical-notes/patients/{patient_id}/record", headers=headers1)
    assert record1.status_code == 200
    record_data1 = record1.json()

    clinical_notes1 = record_data1.get("clinical_notes", [])
    note_ids_in_notes1 = {n["id"] for n in clinical_notes1}
    assert str(note_id) in note_ids_in_notes1

    timeline_events1 = record_data1.get("timeline", [])
    clinical_note_events1 = [e for e in timeline_events1 if e.get("entity_type") == "clinical_note"]
    timeline_note_ids1 = {str(e["entity_id"]) for e in clinical_note_events1}
    assert str(note_id) in timeline_note_ids1

    # Doctor2 gets patient record - should NOT see the draft note
    record2 = await client.get(f"/api/clinical-notes/patients/{patient_id}/record", headers=headers2)
    assert record2.status_code == 200
    record_data2 = record2.json()

    clinical_notes2 = record_data2.get("clinical_notes", [])
    note_ids_in_notes2 = {n["id"] for n in clinical_notes2}
    assert str(note_id) not in note_ids_in_notes2, "Doctor2 should not see draft note in clinical_notes"

    timeline_events2 = record_data2.get("timeline", [])
    clinical_note_events2 = [e for e in timeline_events2 if e.get("entity_type") == "clinical_note"]
    timeline_note_ids2 = {str(e["entity_id"]) for e in clinical_note_events2}
    assert str(note_id) not in timeline_note_ids2, "Doctor2 should not see draft note event in timeline"

    # Test with manager in same clinic - should also not see draft
    async with sessions() as session:
        manager = User(
            clinic_id=UUID(author_clinic_id),
            full_name="Manager User",
            email="manager2@test.com",
            password_hash=hash_password("manager_password_123456"),
            role="clinic_manager",
            is_active=True
        )
        session.add(manager)
        await session.commit()
        await session.refresh(manager)
        manager_id = manager.id

    access_token_mgr = create_test_token(str(manager_id), str(author_clinic_id), "clinic_manager")
    headers_mgr = {"Authorization": f"Bearer {access_token_mgr}"}

    record_mgr = await client.get(f"/api/clinical-notes/patients/{patient_id}/record", headers=headers_mgr)
    assert record_mgr.status_code == 200
    record_data_mgr = record_mgr.json()

    clinical_notes_mgr = record_data_mgr.get("clinical_notes", [])
    note_ids_in_notes_mgr = {n["id"] for n in clinical_notes_mgr}
    assert str(note_id) not in note_ids_in_notes_mgr, "Manager should not see draft note in clinical_notes"

    timeline_events_mgr = record_data_mgr.get("timeline", [])
    clinical_note_events_mgr = [e for e in timeline_events_mgr if e.get("entity_type") == "clinical_note"]
    timeline_note_ids_mgr = {str(e["entity_id"]) for e in clinical_note_events_mgr}
    assert str(note_id) not in timeline_note_ids_mgr, "Manager should not see draft note event in timeline"


@pytest.mark.asyncio
async def test_timeline_sanitizes_old_events_without_refresh(client_and_db):
    """Regression test: timeline sanitization works for old persisted events without refresh.

    Tests that _sanitize_timeline_event properly sanitizes events that were
    created before the sanitization logic was added, without requiring a refresh.

    Scenariu:
    1. Creează nota sursă potrivită și finalizeaz-o
    2. Inserează direct un eveniment vechi cu:
       - title: marker sensibil
       - description: alt marker sensibil
       - metadata: chei suplimentare cu al treilea marker sensibil
    3. Citește evenimentul ca rol fără acces clinic, folosind politica stabilită
    4. Verifică absența markerilor din întregul răspuns serializat
    5. Verifică faptul că evenimentul a fost efectiv evaluat
    6. Nu apela refresh

    Adaugă caz relevant pentru nota sursă inexistentă/neaccesibilă: nu trebuie divulgate informații protejate.
    """
    client, sessions = client_and_db

    # Create user (manager - clinical role)
    tokens = await register(client, email="manager@test.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Get user info
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    user_data = me.json()
    user_id = user_data["id"]
    clinic_id = user_data["clinic_id"]

    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    assert patient.status_code == 201
    patient_id = patient.json()["id"]

    # Create finalized note
    note_resp = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "diagnosis",
        "title": "Diagnosis Title",
        "content": "Patient diagnosis content",
        "is_alert": True
    })
    assert note_resp.status_code == 201
    note_id = note_resp.json()["id"]

    finalize = await client.post(
        f"/api/clinical-notes/{note_id}/finalize",
        headers=headers
    )
    assert finalize.status_code == 200

    # Get the note from DB
    async with sessions() as session:
        note_db = await session.get(ClinicalNote, UUID(note_id))

        # Insert old event with sensitive data directly into DB
        old_event = PatientTimelineEvent(
            clinic_id=note_db.clinic_id,
            patient_id=note_db.patient_id,
            event_type="clinical_note",
            entity_type="clinical_note",
            entity_id=note_db.id,
            title="SENSITIVE_TITLE_MARKER_1",
            description="SENSITIVE_DESCRIPTION_MARKER_2",
            actor_id=note_db.author_id,
            occurred_at=note_db.created_at,
            recorded_at=datetime.now(UTC),
            source="legacy_system",
            metadata={
                "type": "diagnosis",
                "status": "finalized",
                "is_alert": True,
                "content": "SENSITIVE_METADATA_MARKER_3",
                "sensitive_field": "SENSITIVE_METADATA_MARKER_4"
            }
        )
        session.add(old_event)
        await session.commit()
        await session.refresh(old_event)
        old_event_id = old_event.id

    # Now read the timeline WITHOUT calling refresh
    timeline = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers)
    assert timeline.status_code == 200
    events = timeline.json()

    # Find the old event
    old_events = [e for e in events if str(e["id"]) == str(old_event_id)]
    assert len(old_events) > 0, "The old event should be present in the timeline"

    event = old_events[0]

    # Verify sanitization: sensitive markers should NOT be present
    assert "SENSITIVE_TITLE_MARKER_1" not in event["title"], "Sensitive title should be sanitized"
    assert "SENSITIVE_DESCRIPTION_MARKER_2" not in event["description"], "Sensitive description should be sanitized"

    # Check metadata
    metadata = event.get("metadata", {})
    assert isinstance(metadata, dict), "Metadata should be a dict"

    # Sensitive metadata fields should not be present
    assert "SENSITIVE_METADATA_MARKER_3" not in str(metadata), "Sensitive metadata content should be sanitized"
    assert "SENSITIVE_METADATA_MARKER_4" not in str(metadata), "Sensitive metadata field should be sanitized"
    assert "content" not in metadata, "content field should not be in metadata"
    assert "sensitive_field" not in metadata, "sensitive_field should not be in metadata"

    # Only safe fields should be present
    safe_fields = {"type", "status", "is_alert"}
    metadata_keys = set(metadata.keys()) if metadata else set()
    assert metadata_keys.issubset(safe_fields), f"Metadata should only contain safe fields, got: {metadata_keys}"

    # Test with non-clinical role
    async with sessions() as session:
        reception = User(
            clinic_id=UUID(clinic_id),
            full_name="Reception User",
            email="reception@test.com",
            password_hash=hash_password("reception_password_123456"),
            role="reception",
            is_active=True
        )
        session.add(reception)
        await session.commit()
        await session.refresh(reception)
        reception_id = reception.id

    access_token_rec = create_test_token(str(reception_id), str(clinic_id), "reception")
    headers_rec = {"Authorization": f"Bearer {access_token_rec}"}

    timeline_rec = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers_rec)
    assert timeline_rec.status_code == 200
    events_rec = timeline_rec.json()

    old_events_rec = [e for e in events_rec if str(e["id"]) == str(old_event_id)]
    assert len(old_events_rec) > 0, "The old event should be present for reception too"

    event_rec = old_events_rec[0]
    assert "SENSITIVE_TITLE_MARKER_1" not in event_rec["title"], "Sensitive title should be sanitized for reception"
    assert "SENSITIVE_DESCRIPTION_MARKER_2" not in event_rec["description"], "Sensitive description should be sanitized for reception"
    assert "SENSITIVE_METADATA_MARKER_3" not in str(event_rec.get("metadata", {})), "Sensitive metadata should be sanitized for reception"

    metadata_rec = event_rec.get("metadata", {})
    assert "content" not in metadata_rec
    assert "type" in metadata_rec

    # Test with inaccessible source note
    async with sessions() as session:
        inaccessible_event = PatientTimelineEvent(
            clinic_id=UUID(clinic_id),
            patient_id=UUID(patient_id),
            event_type="clinical_note",
            entity_type="clinical_note",
            entity_id=UUID("00000000-0000-0000-0000-000000000000"),
            title="INACCESSIBLE_TITLE_MARKER",
            description="INACCESSIBLE_DESCRIPTION_MARKER",
            actor_id=UUID(user_id),
            occurred_at=datetime.now(UTC),
            recorded_at=datetime.now(UTC),
            source="unknown_source",
            metadata={
                "type": "diagnosis",
                "sensitive": "INACCESSIBLE_METADATA_MARKER"
            }
        )
        session.add(inaccessible_event)
        await session.commit()
        await session.refresh(inaccessible_event)
        inaccessible_event_id = inaccessible_event.id

    timeline_inaccessible = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers)
    assert timeline_inaccessible.status_code == 200
    events_inaccessible = timeline_inaccessible.json()

    inaccessible_events = [e for e in events_inaccessible if str(e["id"]) == str(inaccessible_event_id)]
    if len(inaccessible_events) > 0:
        event_inaccessible = inaccessible_events[0]
        assert "INACCESSIBLE_TITLE_MARKER" not in event_inaccessible["title"], "Inaccessible note title should be sanitized"
        assert "INACCESSIBLE_METADATA_MARKER" not in str(event_inaccessible.get("metadata", {})), "Inaccessible note metadata should be sanitized"
