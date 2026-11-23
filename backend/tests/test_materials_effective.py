"""T18E: material demand follows the effective procedures of each appointment."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event

from app.database import PerformedProcedure, TreatmentPlan, TreatmentPlanItem
from tests.test_materials import (
    add_appointment,
    add_plan_item,
    add_procedure,
    add_stock,
    env,  # noqa: F401
    register,
    save_recipe,
)

WINDOW = {"from_date": "2026-12-01", "to_date": "2026-12-14"}
DAY_1 = "2026-12-02T09:00:00+00:00"
DAY_2 = "2026-12-03T09:00:00+00:00"


async def with_recipe(client, ctx, *, recipe="3", stock="50"):
    procedure_id = await add_procedure(client, ctx["auth"], f"Procedură {uuid4().hex[:6]}")
    material = await add_stock(client, ctx["auth"], quantity=stock)
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0,
                              [{"item_id": material["item_id"], "estimated_quantity": recipe}])
    assert saved.status_code == 201, saved.text
    return procedure_id, material


async def link_manual(client, ctx, appointment_id, procedure_id, quantity):
    current = await client.get(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"])
    assert current.status_code == 200, current.text
    response = await client.put(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"], json={
        "expected_revision": current.json()["revision"],
        "procedures": [{"procedure_catalog_id": procedure_id, "quantity": quantity}],
    })
    assert response.status_code == 200, response.text


async def plan_item(sessions, ctx, procedure_id, appointment_id, *, plan_status="accepted", performed_id=None):
    item_id = await add_plan_item(sessions, ctx, procedure_id, appointment_id, performed_id=performed_id)
    async with sessions() as session:
        item = await session.get(TreatmentPlanItem, UUID(item_id))
        (await session.get(TreatmentPlan, item.plan_id)).status = plan_status
        await session.commit()
    return item_id


async def performed(client, sessions, ctx, appointment_id, procedure_id, *, status=None):
    response = await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "procedure_catalog_id": procedure_id,
    })
    assert response.status_code == 201, response.text
    performed_id = response.json()["id"]
    if status:
        async with sessions() as session:
            (await session.get(PerformedProcedure, UUID(performed_id))).status = status
            await session.commit()
    return UUID(performed_id)


async def requirements(client, ctx):
    response = await client.get("/api/materials/requirements", headers=ctx["auth"], params=WINDOW)
    assert response.status_code == 200, response.text
    return response.json()


def appointment(data, appointment_id):
    return next(row for row in data["appointments"] if row["id"] == appointment_id)


def needs(data, appointment_id):
    return [line["estimated_quantity"] for procedure in appointment(data, appointment_id)["procedures"]
            for line in procedure["lines"]]


def without_procedure(data):
    return {row["id"] for row in data["unknown"]["appointments_without_procedure"]["appointments"]}


async def test_manual_only_procedure_contributes_recipe_times_quantity(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, appointment_id, procedure_id, 2)
    data = await requirements(client, ctx)
    assert needs(data, appointment_id) == ["6.000"]
    assert appointment(data, appointment_id)["procedures"][0]["quantity"] == 2
    assert data["needed_by_item"][material["item_id"]] == "6.000"
    assert data["uncovered_by_item"][material["item_id"]] == "6.000"
    assert appointment_id not in without_procedure(data)


async def test_plan_only_procedure_contributes_once(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, _material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await plan_item(sessions, ctx, procedure_id, appointment_id)
    data = await requirements(client, ctx)
    assert needs(data, appointment_id) == ["3.000"]
    assert appointment(data, appointment_id)["procedures"][0]["quantity"] == 1


@pytest.mark.parametrize(("manual", "plan_items", "expected"), [(2, 1, "6.000"), (2, 3, "9.000")])
async def test_manual_and_plan_use_the_maximum_quantity(env, manual, plan_items, expected):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, appointment_id, procedure_id, manual)
    for _ in range(plan_items):
        await plan_item(sessions, ctx, procedure_id, appointment_id)
    data = await requirements(client, ctx)
    assert needs(data, appointment_id) == [expected]
    assert data["needed_by_item"][material["item_id"]] == expected


@pytest.mark.parametrize("plan_status", ["draft", "cancelled"])
async def test_draft_and_cancelled_plans_do_not_contribute(env, plan_status):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await plan_item(sessions, ctx, procedure_id, appointment_id, plan_status=plan_status)
    data = await requirements(client, ctx)
    assert appointment(data, appointment_id)["procedures"] == []
    assert material["item_id"] not in data["needed_by_item"]
    assert appointment_id in without_procedure(data)


@pytest.mark.parametrize("plan_status", ["accepted", "in_progress"])
async def test_accepted_and_in_progress_plans_contribute(env, plan_status):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, _material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await plan_item(sessions, ctx, procedure_id, appointment_id, plan_status=plan_status)
    assert needs(await requirements(client, ctx), appointment_id) == ["3.000"]


async def test_item_linked_to_open_performed_procedure_stays_in_demand(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, _material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    performed_id = await performed(client, sessions, ctx, appointment_id, procedure_id)
    await plan_item(sessions, ctx, procedure_id, appointment_id, performed_id=performed_id)
    data = await requirements(client, ctx)
    assert needs(data, appointment_id) == ["3.000"]
    assert appointment(data, appointment_id)["procedures"][0]["performed_procedure_id"] == str(performed_id)
    assert appointment_id not in without_procedure(data)


@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_item_linked_to_closed_performed_procedure_leaves_demand(env, status):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    performed_id = await performed(client, sessions, ctx, appointment_id, procedure_id, status=status)
    await plan_item(sessions, ctx, procedure_id, appointment_id, performed_id=performed_id)
    data = await requirements(client, ctx)
    assert appointment(data, appointment_id)["procedures"] == []
    assert material["item_id"] not in data["needed_by_item"]
    assert appointment_id in without_procedure(data)


async def test_open_performed_item_on_draft_plan_does_not_contribute(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, _material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    performed_id = await performed(client, sessions, ctx, appointment_id, procedure_id)
    await plan_item(sessions, ctx, procedure_id, appointment_id, plan_status="draft", performed_id=performed_id)
    data = await requirements(client, ctx)
    assert appointment(data, appointment_id)["procedures"] == []
    assert appointment_id in without_procedure(data)


@pytest.mark.parametrize(("manual", "unperformed", "expected"), [(1, 0, "3.000"), (3, 0, "9.000"), (None, 1, "6.000"), (2, 1, "6.000")])
async def test_open_performed_item_is_not_counted_twice(env, manual, unperformed, expected):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    if manual:
        await link_manual(client, ctx, appointment_id, procedure_id, manual)
    for _ in range(unperformed):
        await plan_item(sessions, ctx, procedure_id, appointment_id)
    performed_id = await performed(client, sessions, ctx, appointment_id, procedure_id)
    await plan_item(sessions, ctx, procedure_id, appointment_id, performed_id=performed_id)
    data = await requirements(client, ctx)
    assert len(appointment(data, appointment_id)["procedures"]) == 1
    assert needs(data, appointment_id) == [expected]
    assert data["needed_by_item"][material["item_id"]] == expected


async def test_procedure_without_recipe_is_unknown_once_per_procedure_and_appointment(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"], "Fără rețetă")
    first = await add_appointment(client, ctx, starts_at=DAY_1)
    second = await add_appointment(client, ctx, starts_at=DAY_2)
    await link_manual(client, ctx, first, procedure_id, 2)
    await plan_item(sessions, ctx, procedure_id, first)
    await plan_item(sessions, ctx, procedure_id, first)
    await link_manual(client, ctx, second, procedure_id, 1)
    data = await requirements(client, ctx)
    unknown = data["unknown"]["procedures_without_recipe"]
    assert unknown["count"] == 2
    assert sorted(row["appointment_id"] for row in unknown["procedures"]) == sorted([first, second])
    assert not without_procedure(data) & {first, second}
    assert appointment(data, first)["procedures"][0]["recipe_missing"] is True


async def test_two_appointments_compete_for_same_stock_with_effective_quantities(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx, stock="10")
    first = await add_appointment(client, ctx, starts_at=DAY_1)
    second = await add_appointment(client, ctx, starts_at=DAY_2)
    await link_manual(client, ctx, first, procedure_id, 2)
    await link_manual(client, ctx, second, procedure_id, 2)
    data = await requirements(client, ctx)
    assert data["needed_by_item"][material["item_id"]] == "12.000"
    shortage = next(row for row in data["shortages"] if row["item_id"] == material["item_id"])
    assert shortage["quantity"] == "2.000"
    assert [(row["appointment_id"], row["shortage"]) for row in shortage["appointments"]] == [(second, "2.000")]


async def test_reservation_of_effective_quantity_covers_the_need(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx, stock="10")
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, appointment_id, procedure_id, 2)
    before = await requirements(client, ctx)
    quantity = needs(before, appointment_id)[0]
    assert quantity == "6.000"
    reserved = await client.post("/api/materials/reservations", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "item_id": material["item_id"], "quantity": quantity,
        "client_request_id": str(uuid4()),
    })
    assert reserved.status_code == 201, reserved.text
    assert reserved.json()["quantity"] == "6.000"
    after = await requirements(client, ctx)
    line = appointment(after, appointment_id)["procedures"][0]["lines"][0]
    assert (line["estimated_quantity"], line["reserved"], line["shortage"]) == ("6.000", "6.000", "0.000")
    assert after["uncovered_by_item"][material["item_id"]] == "0.000"


async def test_replenishment_uses_effective_quantity(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id, material = await with_recipe(client, ctx, stock="5")
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, appointment_id, procedure_id, 2)
    response = await client.get("/api/materials/replenishment", headers=ctx["auth"], params=WINDOW)
    assert response.status_code == 200, response.text
    line = next(row for row in response.json()["items"] if row["item_id"] == material["item_id"])
    assert (line["needed"], line["uncovered_needed"], line["available"]) == ("6.000", "6.000", "5.000")
    assert line["suggested_quantity"] == "3.000"  # minimum 2 + uncovered 6 - available 5


async def test_foreign_clinic_sees_no_demand_and_cannot_reserve(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    foreign = await register(client, "materials-foreign@example.com")
    procedure_id, material = await with_recipe(client, ctx)
    appointment_id = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, appointment_id, procedure_id, 2)
    data = await requirements(client, foreign)
    assert data["appointments"] == [] and data["needed_by_item"] == {}
    denied = await client.post("/api/materials/reservations", headers=foreign["auth"], json={
        "appointment_id": appointment_id, "item_id": material["item_id"], "quantity": "6",
        "client_request_id": str(uuid4()),
    })
    assert denied.status_code == 404


async def test_requirements_query_count_does_not_grow_with_appointments(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id, _material = await with_recipe(client, ctx)
    first = await add_appointment(client, ctx, starts_at=DAY_1)
    await link_manual(client, ctx, first, procedure_id, 2)
    await plan_item(sessions, ctx, procedure_id, first)
    statements = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        await requirements(client, ctx)
        baseline = len(statements)
        event.remove(engine, "before_cursor_execute", count)
        for day in range(3, 7):
            appointment_id = await add_appointment(client, ctx, starts_at=f"2026-12-0{day}T09:00:00+00:00")
            await link_manual(client, ctx, appointment_id, procedure_id, 2)
            await plan_item(sessions, ctx, procedure_id, appointment_id)
        statements.clear()
        event.listen(engine, "before_cursor_execute", count)
        await requirements(client, ctx)
        assert len(statements) == baseline
    finally:
        if event.contains(engine, "before_cursor_execute", count):
            event.remove(engine, "before_cursor_execute", count)
