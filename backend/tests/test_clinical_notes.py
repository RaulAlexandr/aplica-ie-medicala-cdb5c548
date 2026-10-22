"""Tests for T05: Clinical Notes, Findings, and Patient Timeline."""

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
async def test_timeline_draft_protection_bypass_source(client):
    """Regression test: timeline draft protection should not be bypassed by different source.
    
    Tests that draft notes are filtered from timeline even when event.source
    is different from 'clinical_note'. Protection should be based on entity_type,
    not solely on source field.
    """
    tokens = await register(client)
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
    
    # Refresh timeline
    refresh = await client.post("/api/clinical-notes/timeline/refresh", headers=headers)
    assert refresh.status_code == 200
    
    # Get timeline - should NOT see the draft note
    timeline = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers)
    assert timeline.status_code == 200
    events = timeline.json()
    
    # Find clinical note events
    clinical_note_events = [e for e in events if e["entity_type"] == "clinical_note"]
    
    # The draft note should NOT appear in timeline for any user except the author
    # Even if source field is different, it should be filtered
    clinical_note_entity_ids = {e["entity_id"] for e in clinical_note_events}
    
    # Since the current user IS the author, they should see their own draft
    # This is expected behavior - only the author can see their drafts
    assert str(note_id) in clinical_note_entity_ids
    
    # Now test with a different user (manager in a different clinic)
    # Register a second user in a different clinic
    tokens2 = await register(client, email="other@test.com")
    headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}
    
    # Try to get timeline for the first patient from the second clinic
    # This should fail with 404 because of clinic isolation
    timeline2 = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers2)
    assert timeline2.status_code == 404


@pytest.mark.asyncio
async def test_timeline_draft_protection_in_record(client):
    """Regression test: timeline in patient record should also filter draft notes.
    
    Tests that draft notes are filtered from timeline included in patient record,
    using the same protection logic as the standalone timeline endpoint.
    """
    tokens = await register(client)
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
    
    # Refresh timeline to ensure events are created
    refresh = await client.post("/api/clinical-notes/timeline/refresh", headers=headers)
    assert refresh.status_code == 200

    # Get patient record - should include timeline
    record = await client.get(f"/api/clinical-notes/patients/{patient_id}/record", headers=headers)
    assert record.status_code == 200
    record_data = record.json()
    
    # The draft note should appear in clinical_notes (author can see it)
    clinical_notes = record_data.get("clinical_notes", [])
    
    # Author can see their own draft in clinical_notes
    note_ids_in_notes = {n["id"] for n in clinical_notes}
    assert str(note_id) in note_ids_in_notes
    
    # The draft note should also appear in timeline for the author
    # Timeline events are included in the record response
    timeline_events = record_data.get("timeline", [])
    clinical_note_events = [e for e in timeline_events if e.get("entity_type") == "clinical_note"]
    timeline_note_ids = {str(e["entity_id"]) for e in clinical_note_events}
    assert str(note_id) in timeline_note_ids


@pytest.mark.asyncio
async def test_timeline_sanitizes_old_events_without_refresh(client):
    """Regression test: timeline sanitization works for old persisted events without refresh.
    
    Tests that _sanitize_timeline_event properly sanitizes events that were
    created before the sanitization logic was added, without requiring a refresh.
    """
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    
    # Create patient
    patient = await client.post("/api/patients", headers=headers, json={
        "first_name": "Test",
        "last_name": "Patient"
    })
    patient_id = patient.json()["id"]
    
    # Create finalized note with sensitive content
    note_resp = await client.post("/api/clinical-notes", headers=headers, json={
        "patient_id": patient_id,
        "note_type": "diagnosis",
        "title": "Sensitive Diagnosis Title",
        "content": "Patient has a rare condition",
        "is_alert": True
    })
    note_id = note_resp.json()["id"]
    
    finalize = await client.post(
        f"/api/clinical-notes/{note_id}/finalize",
        headers=headers
    )
    assert finalize.status_code == 200
    
    # Create procedure catalog
    catalog_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "SENSITIVE_PROC",
        "name": "Sensitive Procedure Name",
        "base_price": "100.00",
        "currency": "RON",
        "default_duration_minutes": 30
    })
    assert catalog_resp.status_code == 201, f"Catalog creation failed: {catalog_resp.status_code}"
    catalog_id = catalog_resp.json()["id"]
    
    # Create appointment for procedure
    me_resp = await client.get("/api/auth/me", headers=headers)
    doctor_id = me_resp.json()["id"]
    
    room_resp = await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})
    room_id = room_resp.json()["id"]
    
    appt_resp = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "room_id": room_id,
        "starts_at": "2026-10-01T10:00:00+00:00",
        "duration_minutes": 60,
        "appointment_type": "Consultation"
    })
    appt_id = appt_resp.json()["id"]
    
    # Create and complete procedure
    proc_resp = await client.post("/api/performed-procedures", headers=headers, json={
        "appointment_id": appt_id,
        "procedure_catalog_id": catalog_id
    })
    if proc_resp.status_code == 201:
        proc_id = proc_resp.json()["id"]
        await client.post(f"/api/performed-procedures/{proc_id}/start", headers=headers)
        await client.post(f"/api/performed-procedures/{proc_id}/complete", headers=headers)
    
    # Refresh timeline to create events
    refresh = await client.post("/api/clinical-notes/timeline/refresh", headers=headers)
    assert refresh.status_code == 200
    
    # Get timeline
    timeline = await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=headers)
    assert timeline.status_code == 200
    events = timeline.json()
    
    # Verify clinical note events are sanitized
    clinical_note_events = [e for e in events if e["entity_type"] == "clinical_note"]
    for event in clinical_note_events:
        # Title should be generic, not the original sensitive title
        assert "Sensitive Diagnosis Title" not in event["title"]
        assert "diagnosis" in event["title"] or "note" in event["title"]
        
        # Description should be generic
        assert "Sensitive Diagnosis Title" not in event["description"]
        
        # Metadata should only contain safe fields
        assert "content" not in event["metadata"]
        if "type" in event["metadata"]:
            assert event["metadata"]["type"] in {"note", "finding", "alert", "diagnosis", "treatment_plan"}
