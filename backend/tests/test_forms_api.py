"""HTTP proof for the staff-entered, version-pinned T12 form lifecycle."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, PatientFormRevision, User, get_db
from app.main import app


@pytest.fixture
async def form_client():
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


async def register(client, name: str, email: str):
    response = await client.post("/api/auth/register", json={"clinic_name": name, "full_name": "Manager Fictiv", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def definition(label: str = "Istoric fictiv"):
    return {"sections": [{"id": "medical", "title": "Date fictive", "questions": [
        {"id": "has_allergy", "label": "Alergie fictivă?", "type": "yes_no", "required": True},
        {"id": "allergy_detail", "label": label, "type": "long_text", "required": True,
         "condition": {"question_id": "has_allergy", "operator": "equals", "value": "yes"}},
    ]}]}


@pytest.mark.asyncio
async def test_form_version_condition_validation_correction_and_clinic_isolation(form_client):
    client, sessions = form_client
    headers = await register(client, "Form Clinic", "forms@example.com")
    foreign = await register(client, "Other Clinic", "other-forms@example.com")
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Pacient", "last_name": "Fictiv"})
    assert patient.status_code == 201
    patient_id = patient.json()["id"]

    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Anamneză fictivă"})
    assert template.status_code == 201
    version_id = template.json()["version_id"]
    saved = await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": definition()})
    assert saved.status_code == 200
    published = await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)
    assert published.status_code == 200
    immutable = await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": definition("editat")})
    assert immutable.status_code == 409

    started = await client.post(f"/api/forms/patients/{patient_id}/{version_id}", headers=headers)
    assert started.status_code == 201
    form_id = started.json()["id"]
    hidden_answer = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": False, "allergy_detail": "ascuns"}})
    assert hidden_answer.status_code == 422
    missing_required = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True}})
    assert missing_required.status_code == 200
    failed_submit = await client.post(f"/api/forms/{form_id}/submit", headers=headers)
    assert failed_submit.status_code == 422
    saved_answers = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "Răspuns fictiv"}})
    assert saved_answers.status_code == 200
    submitted = await client.post(f"/api/forms/{form_id}/submit", headers=headers)
    assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
    validated = await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})
    assert validated.status_code == 200 and validated.json()["validated_by"]
    corrected = await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {"has_allergy": False}, "reason": "Corectare fictivă"})
    assert corrected.status_code == 200 and corrected.json()["status"] == "submitted"
    revision_response = await client.get(f"/api/forms/{form_id}/revisions", headers=headers)
    assert revision_response.status_code == 200 and revision_response.json()[0]["reason"] == "Corectare fictivă"
    assert revision_response.json()[0]["validated_by"] and revision_response.json()[0]["validated_at"]
    async with sessions() as session:
        history = (await session.scalars(select(PatientFormRevision))).all()
        assert len(history) == 1 and history[0].answers["allergy_detail"] == "Răspuns fictiv"
        assert history[0].validated_by_id is not None and history[0].validated_at is not None

    # Create and publish a changed version; the old response remains bound to its old definition.
    v2 = await client.post(f"/api/forms/templates/{template.json()['id']}/versions", headers=headers, json={"definition": definition("Întrebare nouă fictivă")})
    assert v2.status_code == 201 and v2.json()["version_number"] == 2
    await client.post(f"/api/forms/versions/{v2.json()['id']}/publish", headers=headers)
    old_read = await client.get(f"/api/forms/{form_id}", headers=headers)
    assert old_read.status_code == 200
    assert old_read.json()["version_number"] == 1
    assert old_read.json()["definition"]["sections"][0]["questions"][1]["label"] == "Istoric fictiv"
    foreign_read = await client.get(f"/api/forms/{form_id}", headers=foreign)
    assert foreign_read.status_code == 404
    foreign_patient_list = await client.get(f"/api/forms/patients/{patient_id}", headers=foreign)
    assert foreign_patient_list.status_code == 404
    manager_identity = await client.get("/api/auth/me", headers=headers)
    async with sessions() as session:
        receptionist = await session.get(User, manager_identity.json()["id"])
        receptionist.role = "reception"
        await session.commit()
    reception_read = await client.get(f"/api/forms/{form_id}", headers=headers)
    assert reception_read.status_code == 200 and "answers" not in reception_read.json() and "definition" in reception_read.json()
    reception_list = await client.get(f"/api/forms/patients/{patient_id}", headers=headers)
    assert reception_list.status_code == 200 and "answers" not in reception_list.json()[0]
    forbidden_authoring = await client.post("/api/forms/templates", headers=headers, json={"name": "Nepermis"})
    assert forbidden_authoring.status_code == 403


@pytest.mark.asyncio
async def test_invalid_question_definition_rejected(form_client):
    client, _ = form_client
    headers = await register(client, "Definition Clinic", "definition@example.com")
    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Formular fictiv"})
    invalid = await client.put(f"/api/forms/versions/{template.json()['version_id']}", headers=headers, json={"definition": {"sections": [{"id": "s", "title": "S", "questions": [{"id": "q", "label": "Q", "type": "unsupported"}]}]}})
    assert invalid.status_code == 422
