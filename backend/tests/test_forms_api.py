"""HTTP proof for the staff-entered, version-pinned T12 form lifecycle."""

from uuid import uuid4

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
    assert old_read.json()["answers"] == {"has_allergy": False}
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


@pytest.mark.asyncio
async def test_every_forms_endpoint_enforces_contract_role_matrix_and_authentication(form_client):
    client, sessions = form_client
    headers = await register(client, "Role matrix", "role-matrix@example.com")
    manager_id = (await client.get("/api/auth/me", headers=headers)).json()["id"]
    missing = str(uuid4())
    cases = [
        ("GET", "/api/forms/templates", None, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 200),
        ("POST", "/api/forms/templates", {"name": "Matrix template"}, {"clinic_manager", "administrator"}, 201),
        ("POST", f"/api/forms/templates/{missing}/versions", {"definition": definition()}, {"clinic_manager", "administrator"}, 404),
        ("PUT", f"/api/forms/versions/{missing}", {"definition": definition()}, {"clinic_manager", "administrator"}, 404),
        ("POST", f"/api/forms/versions/{missing}/publish", None, {"clinic_manager", "administrator"}, 404),
        ("GET", f"/api/forms/patients/{missing}", None, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 404),
        ("POST", f"/api/forms/patients/{missing}/{missing}", None, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 404),
        ("GET", f"/api/forms/{missing}", None, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 404),
        ("PUT", f"/api/forms/{missing}/answers", {"answers": {}}, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 404),
        ("POST", f"/api/forms/{missing}/submit", None, {"clinic_manager", "doctor", "assistant", "reception", "administrator"}, 404),
        ("POST", f"/api/forms/{missing}/review", {"decision": "validated"}, {"clinic_manager", "doctor", "administrator"}, 404),
        ("POST", f"/api/forms/{missing}/corrections", {"answers": {}, "reason": "reason"}, {"clinic_manager", "doctor", "administrator"}, 404),
        ("GET", f"/api/forms/{missing}/revisions", None, {"clinic_manager", "doctor", "administrator"}, 404),
    ]

    async def set_role(role: str):
        async with sessions() as session:
            user = await session.get(User, manager_id)
            user.role = role
            await session.commit()

    for method, path, body, allowed, authorized_status in cases:
        unauthenticated = await client.request(method, path, json=body) if body is not None else await client.request(method, path)
        assert unauthenticated.status_code == 401, (method, path, unauthenticated.text)
        for role in ("clinic_manager", "doctor", "assistant", "reception", "administrator"):
            await set_role(role)
            response = await client.request(method, path, headers=headers, json=body) if body is not None else await client.request(method, path, headers=headers)
            expected = authorized_status if role in allowed else 403
            assert response.status_code == expected, (role, method, path, response.text)


@pytest.mark.asyncio
async def test_forms_endpoints_hide_foreign_clinic_records(form_client):
    client, _sessions = form_client
    local = await register(client, "Local clinic", "local-boundary@example.com")
    foreign = await register(client, "Foreign clinic", "foreign-boundary@example.com")
    patient = await client.post("/api/patients", headers=local, json={"first_name": "Local", "last_name": "Patient"})
    template = await client.post("/api/forms/templates", headers=local, json={"name": "Local form"})
    version_id = template.json()["version_id"]
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=local, json={"definition": definition()})).status_code == 200
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=local)).status_code == 200
    patient_id = patient.json()["id"]
    form = await client.post(f"/api/forms/patients/{patient_id}/{version_id}", headers=local)
    form_id = form.json()["id"]

    listed = await client.get("/api/forms/templates", headers=foreign)
    assert listed.status_code == 200 and all(item["id"] != template.json()["id"] for item in listed.json())
    assert (await client.post(f"/api/forms/templates/{template.json()['id']}/versions", headers=foreign, json={"definition": definition()})).status_code == 404
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=foreign, json={"definition": definition()})).status_code == 404
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=foreign)).status_code == 404
    assert (await client.get(f"/api/forms/patients/{patient_id}", headers=foreign)).status_code == 404
    assert (await client.post(f"/api/forms/patients/{patient_id}/{version_id}", headers=foreign)).status_code == 404
    assert (await client.get(f"/api/forms/{form_id}", headers=foreign)).status_code == 404
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=foreign, json={"answers": {}})).status_code == 404
    assert (await client.post(f"/api/forms/{form_id}/submit", headers=foreign)).status_code == 404
    assert (await client.post(f"/api/forms/{form_id}/review", headers=foreign, json={"decision": "validated"})).status_code == 404
    assert (await client.post(f"/api/forms/{form_id}/corrections", headers=foreign, json={"answers": {}, "reason": "reason"})).status_code == 404
    assert (await client.get(f"/api/forms/{form_id}/revisions", headers=foreign)).status_code == 404


@pytest.mark.asyncio
async def test_hidden_required_question_is_optional_and_definition_rejects_chained_conditions(form_client):
    client, _ = form_client
    headers = await register(client, "Conditional clinic", "conditional@example.com")
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Conditional", "last_name": "Patient"})
    chained = {"sections": [{"id": "s", "title": "Section", "questions": [
        {"id": "first", "label": "First", "type": "yes_no", "required": False},
        {"id": "second", "label": "Second", "type": "yes_no", "required": False,
         "condition": {"question_id": "first", "operator": "equals", "value": "yes"}},
        {"id": "third", "label": "Third", "type": "short_text", "required": False,
         "condition": {"question_id": "second", "operator": "equals", "value": "yes"}},
    ]}]}
    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Conditional form"})
    rejected_chain = await client.put(f"/api/forms/versions/{template.json()['version_id']}", headers=headers, json={"definition": chained})
    assert rejected_chain.status_code == 422

    conditional = {"sections": [{"id": "s", "title": "Section", "questions": [
        {"id": "has", "label": "Has details?", "type": "yes_no", "required": True},
        {"id": "yes_detail", "label": "Yes detail", "type": "short_text", "required": True,
         "condition": {"question_id": "has", "operator": "equals", "value": "yes"}},
        {"id": "no_detail", "label": "No detail", "type": "short_text", "required": True,
         "condition": {"question_id": "has", "operator": "equals", "value": "no"}},
    ]}]}
    assert (await client.put(f"/api/forms/versions/{template.json()['version_id']}", headers=headers, json={"definition": conditional})).status_code == 200
    version_id = template.json()["version_id"]
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)).status_code == 200

    first = await client.post(f"/api/forms/patients/{patient.json()['id']}/{version_id}", headers=headers)
    form_id = first.json()["id"]
    hidden = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has": False, "yes_detail": "hidden"}})
    assert hidden.status_code == 422
    visible_required = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has": False}})
    assert visible_required.status_code == 200
    must_answer_visible = await client.post(f"/api/forms/{form_id}/submit", headers=headers)
    assert must_answer_visible.status_code == 422 and "no_detail" in must_answer_visible.text
    completed_hidden = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has": False, "no_detail": "visible"}})
    assert completed_hidden.status_code == 200
    assert (await client.post(f"/api/forms/{form_id}/submit", headers=headers)).status_code == 200

    second = await client.post(f"/api/forms/patients/{patient.json()['id']}/{version_id}", headers=headers)
    second_id = second.json()["id"]
    visible_required_missing = await client.post(f"/api/forms/{second_id}/submit", headers=headers)
    assert visible_required_missing.status_code == 422 and "has" in visible_required_missing.text
    only_controller = await client.put(f"/api/forms/{second_id}/answers", headers=headers, json={"answers": {"has": True}})
    assert only_controller.status_code == 200
    visible_conditional_missing = await client.post(f"/api/forms/{second_id}/submit", headers=headers)
    assert visible_conditional_missing.status_code == 422 and "yes_detail" in visible_conditional_missing.text


@pytest.mark.asyncio
async def test_all_question_types_reject_wrong_shapes_and_invalid_values(form_client):
    client, _ = form_client
    headers = await register(client, "Question types", "question-types@example.com")
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Typed", "last_name": "Patient"})
    typed_definition = {"sections": [{"id": "s", "title": "Types", "questions": [
        {"id": "short", "label": "Short", "type": "short_text", "required": False},
        {"id": "long", "label": "Long", "type": "long_text", "required": False},
        {"id": "yesno", "label": "Yes no", "type": "yes_no", "required": False},
        {"id": "single", "label": "Single", "type": "single_choice", "required": False,
         "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        {"id": "multiple", "label": "Multiple", "type": "multiple_choice", "required": False,
         "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        {"id": "date", "label": "Date", "type": "date", "required": False},
        {"id": "number", "label": "Number", "type": "number", "required": False},
    ]}]}
    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Typed"})
    version_id = template.json()["version_id"]
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": typed_definition})).status_code == 200
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)).status_code == 200
    started = await client.post(f"/api/forms/patients/{patient.json()['id']}/{version_id}", headers=headers)
    form_id = started.json()["id"]

    wrong_shapes = {
        "short": True, "long": 7, "yesno": "yes", "single": True,
        "multiple": "a", "date": 20261120, "number": "7",
    }
    for question_id, value in wrong_shapes.items():
        response = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {question_id: value}})
        assert response.status_code == 422, (question_id, response.text)
    for question_id, value in (("single", "missing"), ("multiple", ["a", "missing"])):
        response = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {question_id: value}})
        assert response.status_code == 422, (question_id, response.text)
    invalid_date = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"date": "2026-02-30"}})
    assert invalid_date.status_code == 422
    invalid_number = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"number": "not-a-number"}})
    assert invalid_number.status_code == 422
    valid = await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {
        "short": "Text", "long": "Text lung", "yesno": False, "single": "a",
        "multiple": ["a", "b"], "date": "2026-11-20", "number": 12.5,
    }})
    assert valid.status_code == 200, valid.text


@pytest.mark.asyncio
async def test_clarification_transitions_correction_history_and_pinned_answers(form_client):
    client, sessions = form_client
    headers = await register(client, "Lifecycle clinic", "lifecycle@example.com")
    identity = (await client.get("/api/auth/me", headers=headers)).json()
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Lifecycle", "last_name": "Patient"})
    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Lifecycle"})
    version_id = template.json()["version_id"]
    original_definition = definition("Original question label")
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": original_definition})).status_code == 200
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)).status_code == 200
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": definition("Changed after publish")})).status_code == 409
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)).status_code == 409

    started = await client.post(f"/api/forms/patients/{patient.json()['id']}/{version_id}", headers=headers)
    form_id = started.json()["id"]
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})).status_code == 409
    assert (await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {}, "reason": "too early"})).status_code == 409
    assert (await client.post(f"/api/forms/{form_id}/submit", headers=headers)).status_code == 422
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "Original answer"}})).status_code == 200
    submitted = await client.post(f"/api/forms/{form_id}/submit", headers=headers)
    assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
    assert (await client.post(f"/api/forms/{form_id}/submit", headers=headers)).status_code == 409
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "No overwrite"}})).status_code == 409
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "needs_clarification", "reason": "   "})).status_code == 422
    clarification = await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "needs_clarification", "reason": "Please clarify"})
    assert clarification.status_code == 200 and clarification.json()["status"] == "needs_clarification"
    assert clarification.json()["clarification_reason"] == "Please clarify"
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})).status_code == 409
    assert (await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {"has_allergy": False}, "reason": "too early"})).status_code == 409
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "Original answer"}})).status_code == 200
    resubmitted = await client.post(f"/api/forms/{form_id}/submit", headers=headers)
    assert resubmitted.status_code == 200 and resubmitted.json()["status"] == "submitted"
    assert resubmitted.json()["clarification_reason"] is None
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})).status_code == 200
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})).status_code == 409
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True}})).status_code == 409
    assert (await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {"has_allergy": False}})).status_code == 422
    corrected = await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "Corrected answer"}, "reason": "Corrected after validation"})
    assert corrected.status_code == 200 and corrected.json()["status"] == "submitted"
    assert corrected.json()["version_number"] == 1
    assert corrected.json()["answers"]["allergy_detail"] == "Corrected answer"
    revisions = await client.get(f"/api/forms/{form_id}/revisions", headers=headers)
    assert revisions.status_code == 200 and len(revisions.json()) == 1
    assert revisions.json()[0]["status"] == "validated"
    assert revisions.json()[0]["answers"]["allergy_detail"] == "Original answer"
    assert revisions.json()[0]["validated_by"] == identity["id"]
    assert revisions.json()[0]["validated_at"]
    async with sessions() as session:
        reception = await session.get(User, identity["id"])
        reception.role = "reception"
        await session.commit()
    for path in (f"/api/forms/{form_id}/revisions", f"/api/forms/{form_id}/corrections"):
        response = await (client.get(path, headers=headers) if path.endswith("revisions") else client.post(path, headers=headers, json={"answers": {}, "reason": "Reception cannot correct"}))
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_reception_gets_only_metadata_from_list_detail_and_revisions_are_forbidden(form_client):
    client, sessions = form_client
    headers = await register(client, "Reception visibility", "reception-visibility@example.com")
    identity = (await client.get("/api/auth/me", headers=headers)).json()
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Visibility", "last_name": "Patient"})
    template = await client.post("/api/forms/templates", headers=headers, json={"name": "Visible label"})
    version_id = template.json()["version_id"]
    assert (await client.put(f"/api/forms/versions/{version_id}", headers=headers, json={"definition": definition("Private answer label")})).status_code == 200
    assert (await client.post(f"/api/forms/versions/{version_id}/publish", headers=headers)).status_code == 200
    form = await client.post(f"/api/forms/patients/{patient.json()['id']}/{version_id}", headers=headers)
    form_id = form.json()["id"]
    assert (await client.put(f"/api/forms/{form_id}/answers", headers=headers, json={"answers": {"has_allergy": True, "allergy_detail": "SECRET_RESPONSE_VALUE"}})).status_code == 200
    assert (await client.post(f"/api/forms/{form_id}/submit", headers=headers)).status_code == 200
    assert (await client.post(f"/api/forms/{form_id}/review", headers=headers, json={"decision": "validated"})).status_code == 200
    assert (await client.post(f"/api/forms/{form_id}/corrections", headers=headers, json={"answers": {"has_allergy": False}, "reason": "Privacy fixture correction"})).status_code == 200
    async with sessions() as session:
        user = await session.get(User, identity["id"])
        user.role = "reception"
        await session.commit()

    listing = await client.get(f"/api/forms/patients/{patient.json()['id']}", headers=headers)
    detail = await client.get(f"/api/forms/{form_id}", headers=headers)
    revisions = await client.get(f"/api/forms/{form_id}/revisions", headers=headers)
    assert listing.status_code == 200 and len(listing.json()) == 1
    assert "answers" not in listing.json()[0]
    assert detail.status_code == 200 and "answers" not in detail.json()
    assert "SECRET_RESPONSE_VALUE" not in listing.text + detail.text
    assert revisions.status_code == 403
