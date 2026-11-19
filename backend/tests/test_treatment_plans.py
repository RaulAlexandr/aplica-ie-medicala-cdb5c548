"""T08 treatment plans: HTTP flow, authorization, snapshots, versioned acceptance and links."""

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import (
    Base,
    PerformedProcedure,
    ProcedureCatalog,
    ProcedureExecutionSegment,
    TreatmentPlanEvent,
    User,
    get_db,
)
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def clinic(client: AsyncClient, email: str, name: str = "Clinica Fictivă T08") -> dict:
    response = await client.post("/api/auth/register", json={
        "clinic_name": name, "full_name": email.split("@")[0], "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = h(response.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T08"})
    assert room.status_code == 201, room.text
    return {"auth": auth, "me": me, "room_id": room.json()["id"]}


async def add_user(sessions, clinic_id: str, email: str, role: str) -> dict[str, str]:
    from uuid import UUID
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=email, password_hash="unused",
                full_name=email.split("@")[0], role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return h(create_access_token(user))


async def patient(client, auth, first="Ana", last="Fictiv") -> str:
    response = await client.post("/api/patients", headers=auth, json={"first_name": first, "last_name": last})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def catalog(client, auth, code: str, price: str = "250.00", minutes: int = 30) -> str:
    response = await client.post("/api/procedures", headers=auth, json={
        "code": code, "name": f"Serviciu {code}", "base_price": price, "default_duration_minutes": minutes})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def appointment(client, ctx, patient_id: str, day: int = 1) -> str:
    response = await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": patient_id, "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"],
        "starts_at": f"2026-12-{day:02d}T09:00:00+00:00", "duration_minutes": 60, "appointment_type": "Tratament"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def performed(client, auth, appointment_id, catalog_id, tooth=None, surface=None) -> str:
    response = await client.post("/api/performed-procedures", headers=auth, json={
        "appointment_id": appointment_id, "procedure_catalog_id": catalog_id,
        "tooth_fdi": tooth, "tooth_surface": surface})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def complete(client, auth, performed_id):
    assert (await client.post(f"/api/performed-procedures/{performed_id}/start", headers=auth)).status_code == 200
    response = await client.post(f"/api/performed-procedures/{performed_id}/complete", headers=auth)
    assert response.status_code == 200, response.text


def plan_payload(c1: str, c2: str) -> dict:
    return {
        "title": "Reabilitare fictivă", "notes": "Plan demonstrativ",
        "stages": [
            {"title": "Etapa 1 — urgențe", "items": [
                {"procedure_catalog_id": c1, "tooth_fdi": "16", "tooth_surface": "occlusal", "priority": "urgent"}]},
            {"title": "Etapa 2 — restaurări", "items": [
                {"procedure_catalog_id": c2, "tooth_fdi": "21", "tooth_surface": "distal,mesial",
                 "priority": "normal", "estimated_price": "410.50"}]},
        ],
    }


async def counts(sessions) -> tuple[int, int]:
    async with sessions() as session:
        return (await session.scalar(select(func.count()).select_from(PerformedProcedure)),
                await session.scalar(select(func.count()).select_from(ProcedureExecutionSegment)))


def items(plan: dict) -> list[dict]:
    return [item for stage in plan["stages"] for item in stage["items"]]


async def test_full_plan_flow_from_creation_to_completion_and_reload(env):
    client, sessions = env
    ctx = await clinic(client, "manager-t08@example.com")
    auth = ctx["auth"]
    patient_id = await patient(client, auth)
    c1, c2 = await catalog(client, auth, "ENDO1", "300.00", 60), await catalog(client, auth, "OBT2", "250.00", 30)
    before = await counts(sessions)

    created = await client.post(f"/api/treatment-plans/patients/{patient_id}", headers=auth, json=plan_payload(c1, c2))
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["status"] == "draft" and plan["version"] == 1 and plan["accepted_version"] is None
    assert [stage["title"] for stage in plan["stages"]] == ["Etapa 1 — urgențe", "Etapa 2 — restaurări"]
    first, second = items(plan)
    assert (first["procedure_code"], first["estimated_price"], first["estimated_duration_minutes"]) == ("ENDO1", "300.00", 60)
    assert (second["tooth_fdi"], second["tooth_surface"], second["estimated_price"]) == ("21", "distal,mesial", "410.50")
    assert plan["totals"] == [{"currency": "RON", "estimated_total": "710.50", "completed_estimate": "0.00",
                               "remaining_estimate": "710.50", "estimated_minutes": 90}]
    assert "Estimare" in plan["estimate_label"]
    assert plan["progress"]["planned"] == 2 and plan["can_complete"] is False

    accepted = await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth,
                                 json={"note": "Acord verbal consemnat de personal", "expected_version": 1})
    assert accepted.status_code == 200, accepted.text
    plan = accepted.json()
    assert plan["status"] == "accepted" and plan["accepted_version"] == 1 and plan["needs_acceptance"] is False
    assert plan["acceptances"][0]["accepted_by"] == ctx["me"]["full_name"]
    assert plan["acceptances"][0]["snapshot"]["totals"][0]["estimated_total"] == "710.50"
    assert await counts(sessions) == before, "planning/acceptance must not create execution records"

    visit = await appointment(client, ctx, patient_id)
    linked = await client.put(f"/api/treatment-plans/items/{first['id']}/appointment", headers=auth,
                              json={"appointment_id": visit})
    assert linked.status_code == 200, linked.text
    assert items(linked.json())[0]["progress"] == "scheduled"
    assert items(linked.json())[0]["appointment"]["linked_by"] == ctx["me"]["full_name"]

    done_id = await performed(client, auth, visit, c1, "16", "occlusal")
    linked = await client.put(f"/api/treatment-plans/items/{first['id']}/performed-procedure", headers=auth,
                              json={"performed_procedure_id": done_id})
    assert linked.status_code == 200, linked.text
    plan = linked.json()
    assert plan["status"] == "in_progress"
    assert items(plan)[0]["progress"] == "in_progress", "pending performed procedure is not done"

    premature = await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)
    assert premature.status_code == 409

    await complete(client, auth, done_id)
    plan = (await client.get(f"/api/treatment-plans/{plan['id']}", headers=auth)).json()
    assert items(plan)[0]["progress"] == "done"
    assert plan["stages"][0]["progress"]["percent"] == 100 and plan["progress"]["percent"] == 50
    assert plan["totals"][0]["completed_estimate"] == "300.00" and plan["totals"][0]["remaining_estimate"] == "410.50"
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)).status_code == 409

    cancelled = await client.post(f"/api/treatment-plans/items/{second['id']}/cancel", headers=auth,
                                  json={"reason": "Pacientul a ales altă variantă (fictiv)"})
    assert cancelled.status_code == 200
    plan = cancelled.json()
    assert plan["progress"] == {**plan["progress"], "done": 1, "cancelled": 1, "remaining": 0, "percent": 100}
    assert plan["totals"][0]["estimated_total"] == "300.00"
    assert plan["can_complete"] is True

    finished = await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)
    assert finished.status_code == 200 and finished.json()["status"] == "completed"
    assert (await client.patch(f"/api/treatment-plans/{plan['id']}", headers=auth, json={"title": "x"})).status_code == 409

    reloaded = (await client.get(f"/api/treatment-plans/{plan['id']}", headers=auth)).json()
    assert reloaded["status"] == "completed" and reloaded["completed_by"] == ctx["me"]["full_name"]
    assert items(reloaded)[0]["performed_procedure"]["id"] == done_id
    assert items(reloaded)[0]["performed_procedure"]["linked_by"] == ctx["me"]["full_name"]
    assert items(reloaded)[1]["cancel_reason"].startswith("Pacientul")
    assert {event["action"] for event in reloaded["history"]} >= {
        "created", "accepted", "appointment_linked", "performed_linked", "item_cancelled", "completed"}
    listing = (await client.get(f"/api/treatment-plans/patients/{patient_id}", headers=auth)).json()
    assert [row["id"] for row in listing] == [plan["id"]] and listing[0]["status"] == "completed"
    # Exactly the one procedure created through T04; T08 created none.
    assert (await counts(sessions))[0] == before[0] + 1


async def test_roles_and_clinic_isolation_including_foreign_references(env):
    client, sessions = env
    a = await clinic(client, "a-t08@example.com", "Clinica A")
    b = await clinic(client, "b-t08@example.com", "Clinica B")
    pa, pb = await patient(client, a["auth"]), await patient(client, b["auth"], "Bogdan")
    ca, cb = await catalog(client, a["auth"], "A1"), await catalog(client, b["auth"], "B1")
    plan = (await client.post(f"/api/treatment-plans/patients/{pa}", headers=a["auth"],
                              json={"title": "Plan A", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": ca}]}]})).json()
    item_id = items(plan)[0]["id"]

    for role in ("reception", "assistant"):
        other = await add_user(sessions, a["me"]["clinic_id"], f"{role}-t08@example.com", role)
        assert (await client.get(f"/api/treatment-plans/patients/{pa}", headers=other)).status_code == 403
        assert (await client.get(f"/api/treatment-plans/{plan['id']}", headers=other)).status_code == 403
        assert (await client.post(f"/api/treatment-plans/patients/{pa}", headers=other, json={"title": "x", "stages": [{"title": "y"}]})).status_code == 403
    doctor = await add_user(sessions, a["me"]["clinic_id"], "doctor-t08@example.com", "doctor")
    assert (await client.get(f"/api/treatment-plans/{plan['id']}", headers=doctor)).status_code == 200
    assert (await client.patch(f"/api/treatment-plans/{plan['id']}", headers=doctor, json={"notes": "doctor edit"})).status_code == 200

    assert (await client.get(f"/api/treatment-plans/{plan['id']}", headers=b["auth"])).status_code == 404
    assert (await client.get(f"/api/treatment-plans/patients/{pa}", headers=b["auth"])).status_code == 404
    assert (await client.patch(f"/api/treatment-plans/items/{item_id}", headers=b["auth"], json={"priority": "low"})).status_code == 404
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=b["auth"], json={"note": "x"})).status_code == 404
    foreign_catalog = await client.post(f"/api/treatment-plans/{plan['id']}/stages/{plan['stages'][0]['id']}/items",
                                        headers=a["auth"], json={"procedure_catalog_id": cb})
    assert foreign_catalog.status_code == 404
    assert (await client.post(f"/api/treatment-plans/patients/{pb}", headers=a["auth"],
                              json={"title": "x", "stages": [{"title": "y"}]})).status_code == 404

    await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=a["auth"], json={"note": "Acord"})
    visit_b = await appointment(client, b, pb)
    performed_b = await performed(client, b["auth"], visit_b, cb)
    assert (await client.put(f"/api/treatment-plans/items/{item_id}/appointment", headers=a["auth"],
                             json={"appointment_id": visit_b})).status_code == 404
    assert (await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=a["auth"],
                             json={"performed_procedure_id": performed_b})).status_code == 404
    candidates = await client.get(f"/api/treatment-plans/patients/{pa}/link-candidates", headers=a["auth"])
    assert candidates.status_code == 200 and candidates.json()["performed_procedures"] == []


async def test_incompatible_links_and_other_patient_records_are_rejected(env):
    client, _ = env
    ctx = await clinic(client, "links-t08@example.com")
    auth = ctx["auth"]
    p1, p2 = await patient(client, auth), await patient(client, auth, "Altul")
    c1, c2 = await catalog(client, auth, "L1"), await catalog(client, auth, "L2")
    plan = (await client.post(f"/api/treatment-plans/patients/{p1}", headers=auth, json={
        "title": "Plan", "stages": [{"title": "E1", "items": [
            {"procedure_catalog_id": c1, "tooth_fdi": "36", "tooth_surface": "mesial,occlusal"}]}]})).json()
    item_id = items(plan)[0]["id"]
    v1, v2 = await appointment(client, ctx, p1, 2), await appointment(client, ctx, p2, 3)
    match = await performed(client, auth, v1, c1, "36", "occlusal,mesial")
    early = await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth,
                             json={"performed_procedure_id": match})
    assert early.status_code == 409, "draft plans cannot be fulfilled before acceptance"
    await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord"})

    cases = [
        (await performed(client, auth, v1, c2, "36", "mesial,occlusal"), 422),  # other service
        (await performed(client, auth, v1, c1, "37", "mesial,occlusal"), 422),  # other tooth
        (await performed(client, auth, v1, c1, "36", "mesial"), 422),           # other surfaces
        (await performed(client, auth, v1, c1), 422),                           # no location
        (await performed(client, auth, v2, c1, "36", "mesial,occlusal"), 404),  # other patient
        (str(uuid4()), 404),                                                    # unknown id
    ]
    for performed_id, expected in cases:
        response = await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth,
                                    json={"performed_procedure_id": performed_id})
        assert response.status_code == expected, (performed_id, response.text)
    other_patient_visit = await client.put(f"/api/treatment-plans/items/{item_id}/appointment", headers=auth,
                                           json={"appointment_id": v2})
    assert other_patient_visit.status_code == 404

    cancelled = await performed(client, auth, v1, c1, "36", "mesial,occlusal")
    assert (await client.post(f"/api/performed-procedures/{cancelled}/cancel", headers=auth, json={"transition": "cancel"})).status_code == 200
    response = await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth,
                                json={"performed_procedure_id": cancelled})
    assert response.status_code == 409

    ok = await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth,
                          json={"performed_procedure_id": match})
    assert ok.status_code == 200, "surface order does not matter"
    assert (await client.patch(f"/api/treatment-plans/items/{item_id}", headers=auth,
                               json={"tooth_fdi": "46"})).status_code == 409
    assert (await client.post("/api/treatment-plans/patients/" + p1, headers=auth, json={
        "title": "Bad", "stages": [{"title": "E", "items": [{"procedure_catalog_id": c1, "tooth_surface": "mesial"}]}]})).status_code == 422
    assert (await client.post("/api/treatment-plans/patients/" + p1, headers=auth, json={
        "title": "Bad", "stages": [{"title": "E", "items": [{"procedure_catalog_id": c1, "tooth_fdi": "19"}]}]})).status_code == 422
    assert (await client.post("/api/treatment-plans/patients/" + p1, headers=auth, json={
        "title": "Bad", "stages": [{"title": "E", "items": [{"procedure_catalog_id": c1, "estimated_price": 10.5}]}]})).status_code == 422


async def test_catalog_changes_do_not_alter_snapshots_and_inactive_services_cannot_be_added(env):
    client, _ = env
    ctx = await clinic(client, "snapshot-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    c = await catalog(client, auth, "SNAP", "199.99", 45)
    plan = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={
        "title": "Snapshot", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": c, "tooth_fdi": "11"}]}]})).json()
    changed = await client.patch(f"/api/procedures/{c}", headers=auth,
                                 json={"name": "Nume nou", "base_price": "999.00", "default_duration_minutes": 90})
    assert changed.status_code == 200, changed.text
    assert (await client.post(f"/api/procedures/{c}/deactivate", headers=auth)).status_code == 200
    reloaded = (await client.get(f"/api/treatment-plans/{plan['id']}", headers=auth)).json()
    item = items(reloaded)[0]
    assert (item["procedure_name"], item["estimated_price"], item["estimated_duration_minutes"]) == ("Serviciu SNAP", "199.99", 45)
    assert reloaded["totals"][0]["estimated_total"] == "199.99"
    added = await client.post(f"/api/treatment-plans/{plan['id']}/stages/{plan['stages'][0]['id']}/items",
                              headers=auth, json={"procedure_catalog_id": c})
    assert added.status_code == 409
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord"})).status_code == 200


async def test_totals_are_separated_by_currency(env):
    client, sessions = env
    ctx = await clinic(client, "currency-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    ron = await catalog(client, auth, "RON1", "100.10")
    from uuid import UUID
    eur = ProcedureCatalog(clinic_id=UUID(ctx["me"]["clinic_id"]), code="EUR1", code_lower="eur1", name="Implant EUR",
                           default_duration_minutes=90, base_price=Decimal("800.00"), currency="EUR", is_active=True)
    async with sessions() as session:
        session.add(eur)
        await session.commit()
    plan = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={
        "title": "Valute", "stages": [{"title": "E1", "items": [
            {"procedure_catalog_id": ron}, {"procedure_catalog_id": ron, "estimated_price": "0.20"},
            {"procedure_catalog_id": str(eur.id), "tooth_fdi": "46"}]}]})).json()
    assert plan["totals"] == [
        {"currency": "EUR", "estimated_total": "800.00", "completed_estimate": "0.00", "remaining_estimate": "800.00", "estimated_minutes": 90},
        {"currency": "RON", "estimated_total": "100.30", "completed_estimate": "0.00", "remaining_estimate": "100.30", "estimated_minutes": 60},
    ]


async def test_changes_after_acceptance_keep_history_and_require_new_acceptance(env):
    client, _ = env
    ctx = await clinic(client, "version-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    c = await catalog(client, auth, "VER", "100.00")
    plan = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={
        "title": "Versiuni", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": c, "tooth_fdi": "14"}]}]})).json()
    item_id = items(plan)[0]["id"]
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord v1"})).status_code == 200
    again = await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Dublu"})
    assert again.status_code == 409, "repeated acceptance clicks must not create a second record"

    edited = await client.patch(f"/api/treatment-plans/items/{item_id}", headers=auth, json={"estimated_price": "150.00"})
    plan = edited.json()
    assert plan["version"] == 2 and plan["status"] == "draft" and plan["needs_acceptance"] is True
    assert plan["acceptances"][0]["version"] == 1 and plan["acceptances"][0]["is_current"] is False
    assert plan["acceptances"][0]["snapshot"]["stages"][0]["items"][0]["estimated_price"] == "100.00"
    second_edit = await client.patch(f"/api/treatment-plans/items/{item_id}", headers=auth, json={"priority": "high"})
    assert second_edit.json()["version"] == 2, "edits within an unaccepted version do not multiply versions"
    stale = await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "x", "expected_version": 1})
    assert stale.status_code == 409
    accepted = await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth,
                                 json={"note": "Acord v2", "expected_version": 2})
    plan = accepted.json()
    assert plan["status"] == "accepted" and plan["accepted_version"] == 2
    assert [(row["version"], row["note"]) for row in plan["acceptances"]] == [(1, "Acord v1"), (2, "Acord v2")]
    assert plan["acceptances"][1]["snapshot"]["stages"][0]["items"][0]["estimated_price"] == "150.00"

    # Edits while in progress keep status but require re-acceptance before completion.
    visit = await appointment(client, ctx, p)
    pid = await performed(client, auth, visit, c, "14")
    await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth, json={"performed_procedure_id": pid})
    await complete(client, auth, pid)
    renamed = await client.patch(f"/api/treatment-plans/{plan['id']}", headers=auth, json={"title": "Versiuni redenumit"})
    plan = renamed.json()
    assert plan["status"] == "in_progress" and plan["version"] == 3 and plan["needs_acceptance"]
    blocked = await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)
    assert blocked.status_code == 409 and "accepted" in blocked.json()["detail"]
    plan = (await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord v3"})).json()
    assert plan["status"] == "in_progress"
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)).status_code == 200


async def test_performed_procedure_fulfils_only_one_item_and_repeated_link_is_idempotent(env):
    client, sessions = env
    ctx = await clinic(client, "reuse-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    c = await catalog(client, auth, "REUSE")
    body = {"title": "Plan", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": c, "tooth_fdi": "26"},
                                                                   {"procedure_catalog_id": c, "tooth_fdi": "26"}]}]}
    plan_a = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json=body)).json()
    plan_b = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json=body)).json()
    for plan in (plan_a, plan_b):
        await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord"})
    visit = await appointment(client, ctx, p)
    pid = await performed(client, auth, visit, c, "26")
    first, sibling = items(plan_a)
    url = f"/api/treatment-plans/items/{first['id']}/performed-procedure"
    assert (await client.put(url, headers=auth, json={"performed_procedure_id": pid})).status_code == 200
    assert (await client.put(url, headers=auth, json={"performed_procedure_id": pid})).status_code == 200
    assert (await client.put(f"/api/treatment-plans/items/{sibling['id']}/performed-procedure", headers=auth,
                             json={"performed_procedure_id": pid})).status_code == 409
    assert (await client.put(f"/api/treatment-plans/items/{items(plan_b)[0]['id']}/performed-procedure", headers=auth,
                             json={"performed_procedure_id": pid})).status_code == 409
    async with sessions() as session:
        links = await session.scalar(select(func.count()).select_from(TreatmentPlanEvent).where(
            TreatmentPlanEvent.action == "performed_linked"))
    assert links == 1
    candidates = (await client.get(f"/api/treatment-plans/patients/{p}/link-candidates", headers=auth)).json()
    assert candidates["performed_procedures"][0]["linked_item_id"] == first["id"]

    # Unlinking keeps history and frees the procedure for another item.
    unlinked = await client.request("DELETE", url, headers=auth, json={"reason": "Asociere greșită (fictiv)"})
    assert unlinked.status_code == 200
    moved = await client.put(f"/api/treatment-plans/items/{sibling['id']}/performed-procedure", headers=auth,
                             json={"performed_procedure_id": pid})
    assert moved.status_code == 200
    actions = [event["action"] for event in moved.json()["history"]]
    assert actions.count("performed_linked") == 2 and "performed_unlinked" in actions


async def test_cancelled_real_procedure_does_not_count_as_done_and_plan_cancel_keeps_execution(env):
    client, sessions = env
    ctx = await clinic(client, "cancel-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    c = await catalog(client, auth, "CANC")
    plan = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={
        "title": "Plan", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": c}]}]})).json()
    await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord"})
    visit = await appointment(client, ctx, p)
    pid = await performed(client, auth, visit, c)
    item_id = items(plan)[0]["id"]
    await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth, json={"performed_procedure_id": pid})
    assert (await client.post(f"/api/performed-procedures/{pid}/cancel", headers=auth, json={"transition": "cancel"})).status_code == 200
    plan = (await client.get(f"/api/treatment-plans/{plan['id']}", headers=auth)).json()
    assert items(plan)[0]["progress"] == "attention" and plan["progress"]["done"] == 0
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/complete", headers=auth)).status_code == 409
    assert (await client.post(f"/api/treatment-plans/items/{item_id}/cancel", headers=auth, json={"reason": "x"})).status_code == 409

    cancelled = await client.post(f"/api/treatment-plans/{plan['id']}/cancel", headers=auth, json={"reason": "Pacient retras (fictiv)"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert items(cancelled.json())[0]["performed_procedure"]["id"] == pid, "links stay readable"
    visit_state = await client.get(f"/api/appointments/{visit}", headers=auth)
    assert visit_state.json()["status"] == "scheduled"
    async with sessions() as session:
        row = await session.get(PerformedProcedure, __import__("uuid").UUID(pid))
        assert row.status == "cancelled"
    assert (await client.post(f"/api/treatment-plans/{plan['id']}/cancel", headers=auth, json={"reason": "x"})).status_code == 409


async def test_idempotent_plan_creation_and_stage_editing(env):
    client, _ = env
    ctx = await clinic(client, "idem-t08@example.com")
    auth = ctx["auth"]
    p = await patient(client, auth)
    c = await catalog(client, auth, "IDEM")
    key = str(uuid4())
    body = {"title": "Idempotent", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": c}]}, {"title": "E2"}]}
    first = await client.post(f"/api/treatment-plans/patients/{p}", headers={**auth, "Idempotency-Key": key}, json=body)
    retry = await client.post(f"/api/treatment-plans/patients/{p}", headers={**auth, "Idempotency-Key": key}, json=body)
    assert first.status_code == 201 and retry.status_code == 201 and first.json()["id"] == retry.json()["id"]
    conflict = await client.post(f"/api/treatment-plans/patients/{p}", headers={**auth, "Idempotency-Key": key},
                                 json={**body, "title": "Altul"})
    assert conflict.status_code == 409
    assert len((await client.get(f"/api/treatment-plans/patients/{p}", headers=auth)).json()) == 1

    plan = first.json()
    stage2 = plan["stages"][1]["id"]
    item_key = str(uuid4())
    url = f"/api/treatment-plans/{plan['id']}/stages/{stage2}/items"
    for _ in range(2):
        added = await client.post(url, headers={**auth, "Idempotency-Key": item_key}, json={"procedure_catalog_id": c, "tooth_fdi": "45"})
        assert added.status_code == 201
    assert len(added.json()["stages"][1]["items"]) == 1
    moved = await client.patch(f"/api/treatment-plans/{plan['id']}/stages/{stage2}", headers=auth, json={"move": "up", "title": "Etapa urgentă"})
    assert [stage["title"] for stage in moved.json()["stages"]] == ["Etapa urgentă", "E1"]
    third = await client.post(f"/api/treatment-plans/{plan['id']}/stages", headers=auth, json={"title": "E3"})
    assert third.status_code == 201
    removed = await client.delete(f"/api/treatment-plans/{plan['id']}/stages/{third.json()['stages'][2]['id']}", headers=auth)
    assert removed.status_code == 200 and len(removed.json()["stages"]) == 2
    non_empty = await client.delete(f"/api/treatment-plans/{plan['id']}/stages/{stage2}", headers=auth)
    assert non_empty.status_code == 409
    blank = await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={"title": "  ", "stages": [{"title": "E"}]})
    assert blank.status_code == 422
    empty = await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={"title": "Fără etape", "stages": []})
    assert empty.status_code == 422
    no_items = (await client.post(f"/api/treatment-plans/patients/{p}", headers=auth, json={"title": "Gol", "stages": [{"title": "E"}]})).json()
    assert (await client.post(f"/api/treatment-plans/{no_items['id']}/accept", headers=auth, json={"note": "Acord"})).status_code == 422
