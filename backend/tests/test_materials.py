"""T18 recipes, demand, reservations and confirmed material use."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import (
    Appointment,
    Base,
    PerformedProcedure,
    ProcedureCatalog,
    TreatmentPlan,
    TreatmentPlanItem,
    TreatmentPlanStage,
    User,
    get_db,
)
from app.inventory.models import InventoryMovement
from app.main import app
from app.materials.models import MaterialReservationEvent

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
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def register(client, email="materials-manager@example.com"):
    response = await client.post("/api/auth/register", json={
        "clinic_name": "Clinică fictivă materiale", "full_name": "Manager Materiale",
        "email": email, "password": PASSWORD,
    })
    assert response.status_code == 201, response.text
    auth = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": f"Cabinet {uuid4().hex[:6]}"})
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Fictivă"})
    assert room.status_code == patient.status_code == 201
    return {"auth": auth, "me": me, "room_id": room.json()["id"], "patient_id": patient.json()["id"]}


async def add_user(sessions, clinic_id, role):
    user = User(clinic_id=clinic_id, email=f"{role}-{uuid4().hex}@example.com", password_hash="unused",
                full_name=f"Utilizator {role}", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
        return {"Authorization": f"Bearer {create_access_token(user)}"}


async def add_procedure(client, auth, name="Procedură fictivă"):
    response = await client.post("/api/procedures", headers=auth, json={
        "code": f"MAT-{uuid4().hex[:8]}", "name": name, "base_price": "10.00", "default_duration_minutes": 30,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_appointment(client, ctx, starts_at="2026-12-02T09:00:00+00:00", status="scheduled", appointment_type="Control"):
    response = await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": ctx["patient_id"], "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"],
        "starts_at": starts_at, "duration_minutes": 30, "status": status, "appointment_type": appointment_type,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_plan_item(sessions, ctx, procedure_id, appointment_id, *, status="active", performed_id=None):
    async with sessions() as session:
        plan = TreatmentPlan(clinic_id=ctx["me"]["clinic_id"], patient_id=ctx["patient_id"],
            title="Plan fictiv", status="accepted", version=1, accepted_version=1,
            created_by_id=ctx["me"]["id"])
        session.add(plan)
        await session.flush()
        stage = TreatmentPlanStage(clinic_id=ctx["me"]["clinic_id"], plan_id=plan.id, position=1, title="Etapă")
        session.add(stage)
        await session.flush()
        catalog = await session.get(ProcedureCatalog, procedure_id)
        item = TreatmentPlanItem(clinic_id=ctx["me"]["clinic_id"], plan_id=plan.id, stage_id=stage.id,
            position=1, procedure_catalog_id=catalog.id, procedure_code=catalog.code,
            procedure_name=catalog.name, currency="RON", estimated_price=Decimal("10.00"),
            estimated_duration_minutes=30, status=status, appointment_id=appointment_id,
            performed_procedure_id=performed_id)
        session.add(item)
        await session.commit()
        return str(item.id)


async def add_stock(client, auth, *, quantity="10", expires_on=None):
    tag = uuid4().hex[:8]
    item = await client.post("/api/inventory/items", headers=auth, json={
        "name": f"Material {tag}", "unit": "buc", "minimum_quantity": "2",
    })
    location = await client.post("/api/inventory/locations", headers=auth, json={"name": f"Depozit {tag}"})
    assert item.status_code == location.status_code == 201
    lot = await client.post("/api/inventory/lots", headers=auth, json={
        "item_id": item.json()["id"], "code": f"LOT-{tag}", "expires_on": expires_on,
    })
    assert lot.status_code == 201, lot.text
    received = await client.post("/api/inventory/movements/receipt", headers=auth, json={
        "item_id": item.json()["id"], "lot_id": lot.json()["id"], "location_id": location.json()["id"],
        "quantity": quantity, "client_request_id": str(uuid4()),
    })
    assert received.status_code == 201, received.text
    return {"item_id": item.json()["id"], "lot_id": lot.json()["id"], "location_id": location.json()["id"]}


async def save_recipe(client, auth, procedure_id, expected_version, lines, request_id=None):
    return await client.put(f"/api/materials/recipes/{procedure_id}", headers=auth,
        json={"expected_version": expected_version, "lines": lines, **({"client_request_id": request_id} if request_id else {})})


@pytest.mark.asyncio
async def test_recipe_versions_decimal_duplicate_and_expected_version(env):
    client, sessions = env
    ctx = await register(client)
    doctor = await add_user(sessions, ctx["me"]["clinic_id"], "doctor")
    procedure_id = await add_procedure(client, ctx["auth"])
    stock = await add_stock(client, ctx["auth"])

    denied = await save_recipe(client, doctor, procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "1.250"}])
    assert denied.status_code == 403
    first_request = str(uuid4())
    first = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "1.250"}], first_request)
    assert first.status_code == 201, first.text
    assert first.json()["version"] == 1
    retry = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "1.250"}], first_request)
    assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
    collision = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "1.500"}], first_request)
    assert collision.status_code == 409
    second = await save_recipe(client, ctx["auth"], procedure_id, 1, [{"item_id": stock["item_id"], "estimated_quantity": "2.500"}])
    assert second.status_code == 201 and second.json()["version"] == 2
    stale = await save_recipe(client, ctx["auth"], procedure_id, 1, [{"item_id": stock["item_id"], "estimated_quantity": "3"}])
    assert stale.status_code == 409
    duplicate = await save_recipe(client, ctx["auth"], procedure_id, 2, [
        {"item_id": stock["item_id"], "estimated_quantity": "1"},
        {"item_id": stock["item_id"], "estimated_quantity": "2"},
    ])
    assert duplicate.status_code == 422
    current = await client.get(f"/api/materials/recipes/{procedure_id}", headers=ctx["auth"])
    assert current.status_code == 200 and current.json()["version"] == 2
    assert current.json()["versions"] == [1, 2]


@pytest.mark.asyncio
async def test_requirements_use_linked_plan_items_and_report_unknowns(env):
    client, sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"], "Profilaxie fictivă")
    # Text coincidence must not infer a catalog procedure: no TreatmentPlanItem is linked here.
    no_link = await add_appointment(client, ctx, appointment_type="Profilaxie fictivă")
    no_recipe = await add_appointment(client, ctx, starts_at="2026-12-03T09:00:00+00:00")
    await add_plan_item(sessions, ctx, procedure_id, no_recipe)
    cancelled = await add_appointment(client, ctx, starts_at="2026-12-04T09:00:00+00:00")
    async with sessions() as session:
        cancelled_row = await session.get(Appointment, UUID(cancelled))
        cancelled_row.status = "cancelled"
        await session.commit()
    await add_plan_item(sessions, ctx, procedure_id, cancelled)

    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={
        "from_date": "2026-12-01", "to_date": "2026-12-14",
    })
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["unknown"]["appointments_without_procedure"]["count"] == 1
    assert data["unknown"]["appointments_without_procedure"]["appointments"][0]["id"] == no_link
    assert data["unknown"]["procedures_without_recipe"]["count"] == 1
    assert data["unknown"]["procedures_without_recipe"]["procedures"][0]["appointment_id"] == no_recipe
    assert all(row["id"] != cancelled for row in data["appointments"])


@pytest.mark.asyncio
async def test_reservations_are_idempotent_release_is_audited_and_stock_is_unchanged(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5")
    procedure_id = await add_procedure(client, ctx["auth"])
    recipe = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "3"}])
    assert recipe.status_code == 201, recipe.text
    appointment_id = await add_appointment(client, ctx)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    request_id = str(uuid4())
    body = {"appointment_id": appointment_id, "item_id": stock["item_id"], "quantity": "3", "client_request_id": request_id}
    reserved = await client.post("/api/materials/reservations", headers=ctx["auth"], json=body)
    assert reserved.status_code == 201, reserved.text
    retry = await client.post("/api/materials/reservations", headers=ctx["auth"], json=body)
    assert retry.status_code == 201 and retry.json()["id"] == reserved.json()["id"]
    other_appointment = await add_appointment(client, ctx, starts_at="2026-12-03T09:00:00+00:00")
    shortage = await client.post("/api/materials/reservations", headers=ctx["auth"], json={**body, "appointment_id": other_appointment, "quantity": "6", "client_request_id": str(uuid4())})
    assert shortage.status_code == 409 and shortage.json()["detail"]["shortage"] == "4.000"
    release_id = str(uuid4())
    released = await client.post(f"/api/materials/reservations/{appointment_id}/{stock['item_id']}/release",
        headers=ctx["auth"], json={"client_request_id": release_id})
    assert released.status_code == 201 and released.json()["action"] == "release"
    inventory = await client.get("/api/inventory", headers=ctx["auth"])
    balance = next(row for row in inventory.json()["balances"] if row["item_id"] == stock["item_id"])
    assert balance["quantity"] == "5.000"
    async with sessions() as session:
        events = (await session.scalars(select(MaterialReservationEvent))).all()
    assert [event.action for event in events] == ["reserve", "release"]
    assert all(event.actor_id == UUID(ctx["me"]["id"]) and event.occurred_at for event in events)


@pytest.mark.asyncio
async def test_confirm_consumption_is_atomic_idempotent_and_keeps_estimate(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5")
    recipe_unused = await add_stock(client, ctx["auth"], quantity="5")
    extra_material = await add_stock(client, ctx["auth"], quantity="5")
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [
        {"item_id": stock["item_id"], "estimated_quantity": "2.500"},
        {"item_id": recipe_unused["item_id"], "estimated_quantity": "1.250"},
    ])
    assert saved.status_code == 201, saved.text
    appointment_id = await add_appointment(client, ctx)
    performed = await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "procedure_catalog_id": procedure_id,
    })
    assert performed.status_code == 201, performed.text
    performed_id = performed.json()["id"]
    request_id = str(uuid4())
    body = {"client_request_id": request_id, "lines": [{**stock, "quantity": "2"}]}
    first = await client.post(f"/api/materials/performed-procedures/{performed_id}/confirm", headers=ctx["auth"], json=body)
    assert first.status_code == 201, first.text
    retry = await client.post(f"/api/materials/performed-procedures/{performed_id}/confirm", headers=ctx["auth"], json=body)
    assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
    collision = await client.post(f"/api/materials/performed-procedures/{performed_id}/confirm", headers=ctx["auth"], json={**body, "lines": [{**stock, "quantity": "1"}]})
    assert collision.status_code == 409
    second_without_reason = await client.post(f"/api/materials/performed-procedures/{performed_id}/confirm", headers=ctx["auth"], json={"client_request_id": str(uuid4()), "lines": [{**stock, "quantity": "1"}]})
    assert second_without_reason.status_code == 422
    second = await client.post(f"/api/materials/performed-procedures/{performed_id}/confirm", headers=ctx["auth"], json={
        "client_request_id": str(uuid4()), "reason": "Consum suplimentar fictiv", "lines": [
            {**stock, "quantity": "1"}, {**extra_material, "quantity": "1"},
        ],
    })
    assert second.status_code == 201, second.text
    async with sessions() as session:
        movements = (await session.scalars(select(InventoryMovement).where(InventoryMovement.kind == "consumption"))).all()
        assert len(movements) == 3
    actuals = await client.get("/api/materials/actuals", headers=ctx["auth"], params={"from_date": "2026-12-01", "to_date": "2026-12-14"})
    assert actuals.status_code == 200, actuals.text
    line = next(row for row in actuals.json()["lines"] if row["item_id"] == stock["item_id"])
    assert line["estimated_quantity"] == "2.500" and line["actual_quantity"] == "3.000"
    lines_by_item = {row["item_id"]: row for row in actuals.json()["lines"]}
    assert lines_by_item[recipe_unused["item_id"]]["estimated_quantity"] == "1.250"
    assert lines_by_item[recipe_unused["item_id"]]["actual_quantity"] == "0.000"
    assert lines_by_item[extra_material["item_id"]]["estimated_quantity"] == "0.000"
    assert lines_by_item[extra_material["item_id"]]["actual_quantity"] == "1.000"


@pytest.mark.asyncio
async def test_replenishment_formula_reports_available_reserved_and_need(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="2")
    procedure_id = await add_procedure(client, ctx["auth"])
    assert (await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "4"}])).status_code == 201
    appointment_id = await add_appointment(client, ctx)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    result = await client.get("/api/materials/replenishment", headers=ctx["auth"], params={"from_date": "2026-12-01", "to_date": "2026-12-14"})
    assert result.status_code == 200, result.text
    line = next(row for row in result.json()["items"] if row["item_id"] == stock["item_id"])
    assert line["available"] == "2.000" and line["needed"] == "4.000"
    assert line["minimum_quantity"] == "2.000" and line["suggested_quantity"] == "4.000"

@pytest.mark.asyncio
async def test_requirements_aggregate_same_article_within_appointment(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5")
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "3"}])
    assert saved.status_code == 201, saved.text
    appointment_id = await add_appointment(client, ctx)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={"from_date": "2026-12-01", "to_date": "2026-12-14"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["shortages"][0]["quantity"] == "1.000"
    line_shortages = [line["shortage"] for proc in data["appointments"][0]["procedures"] for line in proc["lines"]]
    assert sum(Decimal(value) for value in line_shortages) == Decimal("1.000")

@pytest.mark.asyncio
async def test_requirement_does_not_count_lot_expired_before_appointment(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5", expires_on="2026-12-01")
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "2"}])
    assert saved.status_code == 201, saved.text
    appointment_id = await add_appointment(client, ctx)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={"from_date": "2026-12-01", "to_date": "2026-12-14"})
    assert result.status_code == 200, result.text
    line = result.json()["appointments"][0]["procedures"][0]["lines"][0]
    assert line["available"] == "0.000" and line["shortage"] == "2.000"

@pytest.mark.asyncio
async def test_shortages_allocate_stock_fifo_across_appointments_and_expiry_dates(env):
    client, _sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="10")
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [
        {"item_id": stock["item_id"], "estimated_quantity": "6"},
    ])
    assert saved.status_code == 201, saved.text
    first = await add_appointment(client, ctx, starts_at="2026-12-02T09:00:00+00:00")
    second = await add_appointment(client, ctx, starts_at="2026-12-04T09:00:00+00:00")
    await add_plan_item(_sessions, ctx, procedure_id, first)
    await add_plan_item(_sessions, ctx, procedure_id, second)

    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={
        "from_date": "2026-12-01", "to_date": "2026-12-14",
    })
    assert result.status_code == 200, result.text
    assert result.json()["shortages"][0]["quantity"] == "2.000"
    assert result.json()["shortages"][0]["appointments"][0]["appointment_id"] == second

    reservation = await client.post("/api/materials/reservations", headers=ctx["auth"], json={
        "appointment_id": first, "item_id": stock["item_id"], "quantity": "2", "client_request_id": str(uuid4()),
    })
    assert reservation.status_code == 201, reservation.text
    reserved_result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={
        "from_date": "2026-12-01", "to_date": "2026-12-14",
    })
    assert reserved_result.status_code == 200, reserved_result.text
    assert reserved_result.json()["shortages"][0]["quantity"] == "2.000"
    assert reserved_result.json()["shortages"][0]["appointments"][0]["appointment_id"] == second


@pytest.mark.asyncio
async def test_fifo_does_not_use_lot_after_its_expiry_for_later_appointment(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="6", expires_on="2026-12-03")
    later_lot = await client.post("/api/inventory/lots", headers=ctx["auth"], json={
        "item_id": stock["item_id"], "code": f"LOT-LATER-{uuid4().hex[:8]}",
    })
    assert later_lot.status_code == 201, later_lot.text
    receipt = await client.post("/api/inventory/movements/receipt", headers=ctx["auth"], json={
        "item_id": stock["item_id"], "lot_id": later_lot.json()["id"], "location_id": stock["location_id"],
        "quantity": "4", "client_request_id": str(uuid4()),
    })
    assert receipt.status_code == 201, receipt.text
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [
        {"item_id": stock["item_id"], "estimated_quantity": "6"},
    ])
    assert saved.status_code == 201, saved.text
    first = await add_appointment(client, ctx, starts_at="2026-12-02T09:00:00+00:00")
    second = await add_appointment(client, ctx, starts_at="2026-12-04T09:00:00+00:00")
    await add_plan_item(sessions, ctx, procedure_id, first)
    await add_plan_item(sessions, ctx, procedure_id, second)
    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={
        "from_date": "2026-12-01", "to_date": "2026-12-14",
    })
    assert result.status_code == 200, result.text
    assert result.json()["shortages"][0]["quantity"] == "2.000"
    assert result.json()["shortages"][0]["appointments"][0]["appointment_id"] == second

@pytest.mark.asyncio
async def test_replenishment_does_not_recount_need_covered_by_reservations(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5")
    procedure_id = await add_procedure(client, ctx["auth"])
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [
        {"item_id": stock["item_id"], "estimated_quantity": "5"},
    ])
    assert saved.status_code == 201, saved.text
    appointment_id = await add_appointment(client, ctx)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    reservation = await client.post("/api/materials/reservations", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "item_id": stock["item_id"], "quantity": "5",
        "client_request_id": str(uuid4()),
    })
    assert reservation.status_code == 201, reservation.text
    result = await client.get("/api/materials/replenishment", headers=ctx["auth"], params={
        "from_date": "2026-12-01", "to_date": "2026-12-14",
    })
    assert result.status_code == 200, result.text
    line = next(row for row in result.json()["items"] if row["item_id"] == stock["item_id"])
    assert line["needed"] == "5.000"
    assert line["suggested_quantity"] == "2.000"
    assert line["uncovered_needed"] == "0.000"
    assert line["available"] == "0.000" and line["reserved"] == "5.000"

@pytest.mark.asyncio
async def test_confirmation_list_includes_recent_completed_and_marks_existing_consumption(env):
    client, sessions = env
    ctx = await register(client)
    stock = await add_stock(client, ctx["auth"], quantity="5")
    procedure_id = await add_procedure(client, ctx["auth"])
    recent_confirmed = await add_appointment(client, ctx, starts_at="2026-12-02T09:00:00+00:00")
    recent_unconfirmed = await add_appointment(client, ctx, starts_at="2026-12-03T09:00:00+00:00")
    cancelled = await add_appointment(client, ctx, starts_at="2026-12-04T09:00:00+00:00")
    no_show = await add_appointment(client, ctx, starts_at="2026-12-05T09:00:00+00:00")
    older = await add_appointment(client, ctx, starts_at="2026-11-30T09:00:00+00:00")
    performed_ids = {}
    for key, appointment_id in (("confirmed", recent_confirmed), ("unconfirmed", recent_unconfirmed),
                                ("cancelled", cancelled), ("no_show", no_show), ("older", older)):
        response = await client.post("/api/performed-procedures", headers=ctx["auth"], json={
            "appointment_id": appointment_id, "procedure_catalog_id": procedure_id,
        })
        assert response.status_code == 201, response.text
        performed_ids[key] = response.json()["id"]
    consumed = await client.post(f"/api/materials/performed-procedures/{performed_ids['confirmed']}/confirm",
        headers=ctx["auth"], json={"client_request_id": str(uuid4()), "lines":[{
            **stock, "quantity":"1",
        }]})
    assert consumed.status_code == 201, consumed.text
    async with sessions() as session:
        for key, appointment_id in (("confirmed", recent_confirmed), ("unconfirmed", recent_unconfirmed)):
            (await session.get(Appointment, UUID(appointment_id))).status = "completed"
            (await session.get(PerformedProcedure, UUID(performed_ids[key]))).status = "completed"
        (await session.get(Appointment, UUID(cancelled))).status = "cancelled"
        (await session.get(Appointment, UUID(no_show))).status = "no_show"
        (await session.get(Appointment, UUID(older))).status = "completed"
        (await session.get(PerformedProcedure, UUID(performed_ids["older"]))).status = "completed"
        await session.commit()

    result = await client.get("/api/materials/requirements", headers=ctx["auth"], params={
        "from_date":"2026-12-08", "to_date":"2026-12-14",
    })
    assert result.status_code == 200, result.text
    rows = {row["id"]: row for row in result.json()["performed_procedures"]}
    assert set(rows) == {performed_ids["confirmed"], performed_ids["unconfirmed"]}
    assert rows[performed_ids["confirmed"]]["consumption_confirmed"] is True
    assert rows[performed_ids["unconfirmed"]]["consumption_confirmed"] is False
