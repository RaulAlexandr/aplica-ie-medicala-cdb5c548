"""T03 gap: each catalog service declares its allowed locations.

T04 (and both T07 entry paths, which create the same T04 record through POST /api/performed-procedures)
and T08 plan items refuse a forbidden location at creation with 422. Existing services default to
"any" and behave exactly as before; stored records are never re-validated.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    Base,
    PerformedProcedure,
    ProcedureCatalog,
    TreatmentPlan,
    TreatmentPlanItem,
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


async def setup_clinic(client, email: str) -> dict:
    registered = await client.post("/api/auth/register", json={"clinic_name": "Clinica Fictivă T03", "full_name": "Manager T03", "email": email, "password": PASSWORD})
    assert registered.status_code == 201
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T03"})).json()
    patient = (await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Fictiv"})).json()
    visit = await client.post("/api/appointments", headers=auth, json={"patient_id": patient["id"], "doctor_id": me["id"], "room_id": room["id"], "starts_at": "2030-03-01T09:00:00+00:00", "duration_minutes": 60, "appointment_type": "Tratament"})
    assert visit.status_code == 201
    return {"auth": auth, "patient_id": patient["id"], "appointment_id": visit.json()["id"]}


async def service(client, auth, code: str, allowed_location: str | None = None) -> dict:
    body = {"code": code, "name": f"Serviciu {code}", "base_price": "100.00", "default_duration_minutes": 30}
    if allowed_location is not None:
        body["allowed_location"] = allowed_location
    response = await client.post("/api/procedures", headers=auth, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def perform(client, ctx, catalog_id: str, tooth=None, surface=None):
    return await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": ctx["appointment_id"], "procedure_catalog_id": catalog_id, "tooth_fdi": tooth, "tooth_surface": surface})


async def count(sessions, model) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(model))


# (tooth, surface) combinations and whether each rule accepts them.
CASES = [(None, None), ("16", None), ("16", "occlusal"), (None, "occlusal")]
EXPECTED_T04 = {
    "any": [True, True, True, True],  # unchanged: T04 accepted a surface without a tooth before T03
    "no_tooth": [True, False, False, False],
    "tooth": [False, True, True, False],
    "tooth_surface": [False, False, True, False],
}


async def test_catalog_declares_allowed_location_with_permissive_default(env):
    client, sessions = env
    ctx = await setup_clinic(client, "t03-catalog@example.com")
    legacy = await service(client, ctx["auth"], "LEGACY")
    assert legacy["allowed_location"] == "any"
    strict = await service(client, ctx["auth"], "STRICT", "tooth_surface")
    assert strict["allowed_location"] == "tooth_surface"
    assert (await client.get(f"/api/procedures/{strict['id']}", headers=ctx["auth"])).json()["allowed_location"] == "tooth_surface"
    bad = await client.post("/api/procedures", headers=ctx["auth"], json={"code": "BAD", "name": "Bad", "base_price": "1.00", "default_duration_minutes": 5, "allowed_location": "quadrant"})
    assert bad.status_code == 422
    patched = await client.patch(f"/api/procedures/{legacy['id']}", headers=ctx["auth"], json={"allowed_location": "tooth"})
    assert patched.status_code == 200 and patched.json()["allowed_location"] == "tooth"
    assert (await client.patch(f"/api/procedures/{legacy['id']}", headers=ctx["auth"], json={"allowed_location": "x"})).status_code == 422
    unchanged = await client.patch(f"/api/procedures/{legacy['id']}", headers=ctx["auth"], json={"name": "Renamed"})
    assert unchanged.json()["allowed_location"] == "tooth"
    listed = {row["code"]: row["allowed_location"] for row in (await client.get("/api/procedures", headers=ctx["auth"])).json()}
    assert listed == {"LEGACY": "tooth", "STRICT": "tooth_surface"}
    async with sessions() as session:
        with pytest.raises(IntegrityError):  # CHECK constraint also guards direct writes
            row = await session.get(ProcedureCatalog, strict["id"])
            row.allowed_location = "quadrant"
            await session.commit()


@pytest.mark.parametrize("rule", list(EXPECTED_T04))
async def test_t04_t07_creation_enforces_the_service_rule(env, rule):
    # Both T07 paths (VisitPage and PatientProcedureEntry -> ProcedureForm) submit this same T04 create
    # request, so the server rule applies to both; the UI difference is covered by Vitest.
    client, sessions = env
    ctx = await setup_clinic(client, f"t03-{rule}@example.com")
    catalog = await service(client, ctx["auth"], f"S-{rule}", None if rule == "any" else rule)
    for (tooth, surface), allowed in zip(CASES, EXPECTED_T04[rule], strict=True):
        before = await count(sessions, PerformedProcedure)
        response = await perform(client, ctx, catalog["id"], tooth, surface)
        if allowed:
            assert response.status_code == 201, (rule, tooth, surface, response.text)
            assert response.json()["tooth_fdi"] == tooth
        else:
            assert response.status_code == 422, (rule, tooth, surface, response.text)
            assert response.json()["detail"].startswith(f"Service S-{rule} ")
            assert await count(sessions, PerformedProcedure) == before


async def test_t04_messages_are_explicit(env):
    client, _ = env
    ctx = await setup_clinic(client, "t03-messages@example.com")
    none = await service(client, ctx["auth"], "CONS", "no_tooth")
    tooth = await service(client, ctx["auth"], "EXT", "tooth")
    surface = await service(client, ctx["auth"], "OBT", "tooth_surface")
    assert (await perform(client, ctx, none["id"], "16")).json()["detail"] == "Service CONS is recorded without a tooth; remove the tooth and surfaces"
    assert (await perform(client, ctx, tooth["id"])).json()["detail"] == "Service EXT requires an FDI tooth"
    assert (await perform(client, ctx, surface["id"], "16")).json()["detail"] == "Service OBT requires an FDI tooth and at least one surface"
    # Invalid FDI codes keep the existing validation (422 from the schema), independent of the rule.
    assert (await perform(client, ctx, surface["id"], "99", "occlusal")).status_code == 422


EXPECTED_T08 = {
    "any": [True, True, True, False],  # unchanged: T08 already required a tooth for surfaces
    "no_tooth": [True, False, False, False],
    "tooth": [False, True, True, False],
    "tooth_surface": [False, False, True, False],
}


@pytest.mark.parametrize("rule", list(EXPECTED_T08))
async def test_t08_plan_creation_and_added_items_enforce_the_service_rule(env, rule):
    client, sessions = env
    ctx = await setup_clinic(client, f"t03-plan-{rule}@example.com")
    catalog = await service(client, ctx["auth"], f"P-{rule}", None if rule == "any" else rule)
    holder = await service(client, ctx["auth"], "HOLD")
    base = await client.post(f"/api/treatment-plans/patients/{ctx['patient_id']}", headers=ctx["auth"], json={"title": "Plan", "stages": [{"title": "E1", "items": [{"procedure_catalog_id": holder["id"]}]}]})
    assert base.status_code == 201, base.text
    plan = base.json()
    stage_id = plan["stages"][0]["id"]
    for (tooth, surface), allowed in zip(CASES, EXPECTED_T08[rule], strict=True):
        item = {"procedure_catalog_id": catalog["id"], "tooth_fdi": tooth, "tooth_surface": surface}
        plans_before, items_before = await count(sessions, TreatmentPlan), await count(sessions, TreatmentPlanItem)
        created = await client.post(f"/api/treatment-plans/patients/{ctx['patient_id']}", headers=ctx["auth"], json={"title": "Plan nou", "stages": [{"title": "E1", "items": [item]}]})
        added = await client.post(f"/api/treatment-plans/{plan['id']}/stages/{stage_id}/items", headers=ctx["auth"], json=item)
        if allowed:
            assert created.status_code == 201 and added.status_code == 201, (rule, tooth, surface, created.text, added.text)
        else:
            assert created.status_code == 422 and added.status_code == 422, (rule, tooth, surface, created.text, added.text)
            assert await count(sessions, TreatmentPlan) == plans_before
            assert await count(sessions, TreatmentPlanItem) == items_before


async def test_t08_location_change_respects_rule_and_existing_records_stay_valid(env):
    client, sessions = env
    ctx = await setup_clinic(client, "t03-retro@example.com")
    catalog = await service(client, ctx["auth"], "RETRO")
    # Records created while the service allowed any location.
    performed = await perform(client, ctx, catalog["id"], "16", "occlusal")
    assert performed.status_code == 201
    plan = (await client.post(f"/api/treatment-plans/patients/{ctx['patient_id']}", headers=ctx["auth"], json={"title": "Plan", "stages": [{"title": "E1", "items": [
        {"procedure_catalog_id": catalog["id"], "tooth_fdi": "16", "tooth_surface": "occlusal"}]}]})).json()
    item_id = plan["stages"][0]["items"][0]["id"]

    assert (await client.patch(f"/api/procedures/{catalog['id']}", headers=ctx["auth"], json={"allowed_location": "no_tooth"})).status_code == 200

    # The stored performed procedure keeps working (no retroactive invalidation).
    procedure_id = performed.json()["id"]
    assert (await client.get(f"/api/performed-procedures/{procedure_id}", headers=ctx["auth"])).json()["tooth_fdi"] == "16"
    assert (await client.post(f"/api/performed-procedures/{procedure_id}/start", headers=ctx["auth"])).status_code == 200
    assert (await client.post(f"/api/performed-procedures/{procedure_id}/complete", headers=ctx["auth"])).status_code == 200
    # Editing non-location fields of the stored plan item is still possible.
    assert (await client.patch(f"/api/treatment-plans/items/{item_id}", headers=ctx["auth"], json={"priority": "high"})).status_code == 200
    # A new location must follow the current rule.
    moved = await client.patch(f"/api/treatment-plans/items/{item_id}", headers=ctx["auth"], json={"tooth_fdi": "17", "tooth_surface": "occlusal"})
    assert moved.status_code == 422 and "without a tooth" in moved.json()["detail"]
    cleared = await client.patch(f"/api/treatment-plans/items/{item_id}", headers=ctx["auth"], json={"tooth_fdi": None, "tooth_surface": None})
    assert cleared.status_code == 200
    async with sessions() as session:
        stored = await session.get(PerformedProcedure, procedure_id)
        assert (stored.tooth_fdi, stored.tooth_surface, stored.status) == ("16", "occlusal", "completed")


async def test_t08_patch_resending_unchanged_location_is_not_revalidated(env):
    # Sol review point 5: a PATCH that re-sends the stored location must not apply a rule tightened later.
    client, _ = env
    ctx = await setup_clinic(client, "t03-resend@example.com")
    catalog = await service(client, ctx["auth"], "RESEND")
    plan = (await client.post(f"/api/treatment-plans/patients/{ctx['patient_id']}", headers=ctx["auth"], json={"title": "Plan", "stages": [{"title": "E1", "items": [
        {"procedure_catalog_id": catalog["id"], "tooth_fdi": "16"},
        {"procedure_catalog_id": catalog["id"], "tooth_fdi": "21", "tooth_surface": "mesial,distal"}]}]})).json()
    first, second = (item["id"] for item in plan["stages"][0]["items"])
    assert (await client.patch(f"/api/procedures/{catalog['id']}", headers=ctx["auth"], json={"allowed_location": "tooth_surface"})).status_code == 200

    unchanged = await client.patch(f"/api/treatment-plans/items/{first}", headers=ctx["auth"], json={"notes": "Notă nouă", "tooth_fdi": "16"})
    assert unchanged.status_code == 200, unchanged.text
    same_surfaces = await client.patch(f"/api/treatment-plans/items/{second}", headers=ctx["auth"], json={"tooth_fdi": "21", "tooth_surface": "distal,mesial"})
    assert same_surfaces.status_code == 200, same_surfaces.text
    explicit_null = await client.patch(f"/api/treatment-plans/items/{first}", headers=ctx["auth"], json={"tooth_surface": None})
    assert explicit_null.status_code == 200, explicit_null.text

    moved = await client.patch(f"/api/treatment-plans/items/{first}", headers=ctx["auth"], json={"tooth_fdi": "17"})
    assert moved.status_code == 422 and moved.json()["detail"] == "Service RESEND requires an FDI tooth and at least one surface"
    dropped = await client.patch(f"/api/treatment-plans/items/{second}", headers=ctx["auth"], json={"tooth_surface": None})
    assert dropped.status_code == 422
    allowed = await client.patch(f"/api/treatment-plans/items/{first}", headers=ctx["auth"], json={"tooth_surface": "occlusal"})
    assert allowed.status_code == 200
    items = {item["id"]: item for item in allowed.json()["stages"][0]["items"]}
    assert (items[first]["tooth_fdi"], items[first]["tooth_surface"], items[first]["notes"]) == ("16", "occlusal", "Notă nouă")
    assert (items[second]["tooth_fdi"], items[second]["tooth_surface"]) == ("21", "distal,mesial")
