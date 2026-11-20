# ruff: noqa: F811  (pytest fixtures are imported, then requested by argument name)
"""HTTP regression tests for the pilot QA fixes (F05, F08, S6)."""

import pytest
from sqlalchemy import select

from app.database import PerformedProcedureRevision
from tests.test_active_catalog_procedure_creation import (  # noqa: F401  (fixtures + helpers)
    api_context,
    create_clinic_context,
    create_performed_procedure,
    register_clinic,
)
from tests.test_periodontal import SITES, create_patient, paro_client, register, tooth  # noqa: F401


async def _revisions(sessions, procedure_id):
    async with sessions() as session:
        rows = await session.scalars(
            select(PerformedProcedureRevision)
            .where(PerformedProcedureRevision.performed_procedure_id == procedure_id)
            .order_by(PerformedProcedureRevision.occurred_at)
        )
        return [(row.field, row.previous_value, row.new_value, row.reason) for row in rows]


async def _open_visit_and_procedure(client, headers):
    context = await create_clinic_context(client, headers)
    for status in ("confirmed", "arrived", "in_progress"):
        moved = await client.patch(
            f"/api/appointments/{context['appointment_id']}/status", headers=headers, json={"status": status}
        )
        assert moved.status_code == 200, moved.text
    created = await create_performed_procedure(client, headers, context["appointment_id"], context["catalog_id"])
    assert created.status_code == 201, created.text
    return context, created.json()["id"]


async def test_f08_cancel_revision_records_the_real_previous_status(api_context):
    client, sessions = api_context
    headers = await register_clinic(client, "F08 Clinic", "f08@example.com")
    _context, procedure_id = await _open_visit_and_procedure(client, headers)
    started = await client.post(
        f"/api/performed-procedures/{procedure_id}/start", headers=headers, json={"transition": "start"}
    )
    assert started.status_code == 200, started.text
    cancelled = await client.post(
        f"/api/performed-procedures/{procedure_id}/cancel",
        headers=headers,
        json={"transition": "cancel", "notes": "Pacientul a plecat"},
    )
    assert cancelled.status_code == 200, cancelled.text
    revisions = await _revisions(sessions, procedure_id)
    assert ("status", "running", "cancelled", "Pacientul a plecat") in revisions
    assert ("status", "cancelled", "cancelled", "Pacientul a plecat") not in revisions


async def test_f08_cancel_without_notes_still_writes_the_status_revision(api_context):
    client, sessions = api_context
    headers = await register_clinic(client, "F08b Clinic", "f08b@example.com")
    _context, procedure_id = await _open_visit_and_procedure(client, headers)
    cancelled = await client.post(
        f"/api/performed-procedures/{procedure_id}/cancel", headers=headers, json={"transition": "cancel"}
    )
    assert cancelled.status_code == 200, cancelled.text
    revisions = await _revisions(sessions, procedure_id)
    assert ("status", "pending", "cancelled", None) in revisions


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "F05-A", "name": "Preț număr", "default_duration_minutes": 10, "base_price": 12.34},
        {"code": 12, "name": "Cod număr", "default_duration_minutes": 10, "base_price": "12.34"},
        {"code": "F05-C", "name": 12, "default_duration_minutes": 10, "base_price": "12.34"},
        {"code": "F05-D", "name": "Monedă număr", "default_duration_minutes": 10, "base_price": "12.34", "currency": 7},
        {"code": ["F05-E"], "name": "Cod listă", "default_duration_minutes": 10, "base_price": "12.34"},
        {"code": "F05-F", "name": "Preț null", "default_duration_minutes": 10, "base_price": None},
    ],
)
async def test_f05_create_catalog_wrong_types_are_422_not_500(api_context, payload):
    client, _sessions = api_context
    headers = await register_clinic(client, "F05 Clinic", "f05@example.com")
    response = await client.post("/api/procedures", headers=headers, json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "patch",
    [{"base_price": 12.34}, {"code": 12}, {"name": 12}, {"currency": 7}],
)
async def test_f05_update_catalog_wrong_types_are_422_not_500(api_context, patch):
    client, _sessions = api_context
    headers = await register_clinic(client, "F05u Clinic", "f05u@example.com")
    context = await create_clinic_context(client, headers)
    response = await client.patch(f"/api/procedures/{context['catalog_id']}", headers=headers, json=patch)
    assert response.status_code == 422, response.text


async def test_f05_appointment_patch_duration_null_is_refused_explicitly(api_context):
    client, _sessions = api_context
    headers = await register_clinic(client, "F05a Clinic", "f05a@example.com")
    context = await create_clinic_context(client, headers)
    response = await client.patch(
        f"/api/appointments/{context['appointment_id']}", headers=headers, json={"duration_minutes": None}
    )
    assert response.status_code == 422, response.text
    unchanged = await client.get(f"/api/appointments/{context['appointment_id']}", headers=headers)
    assert unchanged.json()["duration_minutes"] == 60


async def test_f05_clinical_note_content_null_stays_422(api_context):
    client, _sessions = api_context
    headers = await register_clinic(client, "F05n Clinic", "f05n@example.com")
    context = await create_clinic_context(client, headers)
    note = await client.post(
        "/api/clinical-notes",
        headers=headers,
        json={"patient_id": context["patient_id"], "note_type": "clinical_note", "content": "ok"},
    )
    assert note.status_code == 201, note.text
    response = await client.patch(
        f"/api/clinical-notes/{note.json()['id']}", headers=headers, json={"content": None}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("depth,expected", [(0, 200), (20, 200), (20.01, 422), (21, 422), (40, 422), (100, 422)])
async def test_s6_probing_depth_is_limited_to_0_20_mm(paro_client, depth, expected):
    client, _sessions = paro_client
    auth = await register(client, "s6-doctor@example.com")
    patient = await create_patient(client, auth)
    created = await client.post(
        f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-11-19"}
    )
    assert created.status_code == 201, created.text
    response = await client.put(
        f"/api/periodontal/assessments/{created.json()['id']}/measurements",
        headers=auth,
        json={"teeth": [tooth("16", depth)]},
    )
    assert response.status_code == expected, response.text
    if expected == 422:
        assert "20" in response.text


async def test_s6_rejected_depth_leaves_saved_draft_untouched(paro_client):
    client, _sessions = paro_client
    auth = await register(client, "s6b-doctor@example.com")
    patient = await create_patient(client, auth)
    created = await client.post(
        f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-11-19"}
    )
    aid = created.json()["id"]
    ok = await client.put(f"/api/periodontal/assessments/{aid}/measurements", headers=auth, json={"teeth": [tooth("16", 3)]})
    assert ok.status_code == 200
    bad = await client.put(f"/api/periodontal/assessments/{aid}/measurements", headers=auth, json={"teeth": [tooth("16", 40)]})
    assert bad.status_code == 422
    after = await client.get(f"/api/periodontal/assessments/{aid}", headers=auth)
    assert after.json()["teeth"][0]["sites"][0]["probing_depth_mm"] == 3


@pytest.mark.parametrize("field", ["patient_id", "doctor_id", "room_id", "appointment_type"])
async def test_f05_appointment_patch_other_required_nulls_are_422(api_context, field):
    client, _sessions = api_context
    headers = await register_clinic(client, "F05b Clinic", f"f05b-{field}@example.com")
    context = await create_clinic_context(client, headers)
    response = await client.patch(
        f"/api/appointments/{context['appointment_id']}", headers=headers, json={field: None}
    )
    assert response.status_code == 422, response.text
