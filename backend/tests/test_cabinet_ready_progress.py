"""T22B: visit progress is not a preparation change. Rule -> test matrix is in the T22B handoff."""
from uuid import UUID

from sqlalchemy import event, func, select

from app.cabinet_ready import service
from app.cabinet_ready.models import CabinetPreparation
from app.database import PerformedProcedure, TreatmentPlanItem
from tests.test_cabinet_ready import (
    STARTS,
    add_shift,
    board,
    card,
    confirm,
    confirm_required,
    key,
    link,
    save_checklist,
    scenario,
    start,
)
from tests.test_materials import (
    add_appointment,
    add_plan_item,
    add_procedure,
    add_stock,
    env,  # noqa: F401
    register,
    save_recipe,
)


async def performed(client, sessions, ctx, appointment_id, procedure_id, status="completed"):
    response = await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "procedure_catalog_id": procedure_id})
    assert response.status_code == 201, response.text
    performed_id = UUID(response.json()["id"])
    if status:
        async with sessions() as session:
            (await session.get(PerformedProcedure, performed_id)).status = status
            await session.commit()
    return performed_id


async def link_plan(sessions, item_id, performed_id):
    async with sessions() as session:
        (await session.get(TreatmentPlanItem, UUID(item_id))).performed_procedure_id = performed_id
        await session.commit()


async def set_procedures(client, ctx, appointment_id, procedures):
    current = await client.get(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"])
    response = await client.put(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"], json={
        "expected_revision": current.json()["revision"],
        "procedures": [{"procedure_catalog_id": procedure_id, "quantity": quantity} for procedure_id, quantity in procedures],
    })
    assert response.status_code == 200, response.text


async def ready_scenario(client, quantity=1):
    ctx, procedure_id, appointment_id = await scenario(client)
    if quantity != 1:
        await link(client, ctx, appointment_id, procedure_id, quantity)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "ready" and row["explanation"] is None
    return ctx, procedure_id, appointment_id, row


def assert_kept(before, after):
    assert after["state"] == "ready" and after["explanation"] is None
    assert after["preparation_id"] == before["preparation_id"]
    assert after["confirmed_required"] == before["confirmed_required"] == before["total_required"]
    assert [line["id"] for line in after["items"]] == [line["id"] for line in before["items"]]
    assert all(line["confirmed"] for line in after["items"] if line["required"])


async def assert_reset(client, sessions, ctx, appointment_id, before, *, state="in_preparation"):
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == state and "procedurile" in row["explanation"] and row["confirmed_required"] == 0
    item_id = before["items"][0]["id"]
    assert (await confirm(client, ctx["auth"], item_id)).status_code == 409
    history = (await client.get(f"/api/cabinet-ready/appointments/{appointment_id}/history", headers=ctx["auth"])).json()
    assert len([entry for entry in history["confirmations"] if entry["action"] == "confirm"]) == before["total_required"]
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetPreparation.id))) == 1
    return row


# Rule 3: completion keeps state, confirmations and explanation=None
async def test_manual_one_completed_stays_ready(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 1)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    after = await card(client, ctx["auth"], appointment_id)
    assert_kept(before, after)
    assert after["procedures"][0]["quantity"] == 1
    again = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/start", headers=ctx["auth"], json=key())
    assert again.status_code == 409 and "deja începută" in again.json()["detail"]


async def test_manual_two_one_completed_stays_ready(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 2)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    after = await card(client, ctx["auth"], appointment_id)
    assert_kept(before, after)
    assert after["procedures"][0]["quantity"] == 2


async def test_manual_two_both_completed_stays_ready(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 2)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    assert_kept(before, await card(client, ctx["auth"], appointment_id))


async def test_confirmation_after_completion_still_writes(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "ready" and row["explanation"] is None


async def test_plan_item_linked_to_completed_stays_ready(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    await save_checklist(client, ctx["auth"], procedure_id)
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS)
    item_id = await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    before = await card(client, ctx["auth"], appointment_id)
    assert before["state"] == "ready"
    performed_id = await performed(client, sessions, ctx, appointment_id, procedure_id, status=None)
    await link_plan(sessions, item_id, performed_id)
    assert_kept(before, await card(client, ctx["auth"], appointment_id))
    async with sessions() as session:
        (await session.get(PerformedProcedure, performed_id)).status = "completed"
        await session.commit()
    assert_kept(before, await card(client, ctx["auth"], appointment_id))


async def test_open_performed_procedure_keeps_ready(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 1)
    await performed(client, sessions, ctx, appointment_id, procedure_id, status=None)
    assert_kept(before, await card(client, ctx["auth"], appointment_id))


# Rule 5: a cancelled performed procedure is not progress and does not widen the scope
async def test_cancelled_performed_procedure_neither_progress_nor_scope(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 1)
    await performed(client, sessions, ctx, appointment_id, procedure_id, status="cancelled")
    for _ in range(2):
        item_id = await add_plan_item(sessions, ctx, procedure_id, appointment_id)
        await link_plan(sessions, item_id, await performed(client, sessions, ctx, appointment_id, procedure_id, status="cancelled"))
    after = await card(client, ctx["auth"], appointment_id)
    assert_kept(before, after)
    assert after["procedures"][0]["quantity"] == 1


# Rule 4: a real change is still a change
async def test_quantity_change_after_partial_completion_resets(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 2)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    assert_kept(before, await card(client, ctx["auth"], appointment_id))
    await link(client, ctx, appointment_id, procedure_id, 3)
    row = await assert_reset(client, sessions, ctx, appointment_id, before)
    assert row["procedures"][0]["quantity"] == 3


async def test_added_procedure_resets(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 1)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    other = await add_procedure(client, ctx["auth"], "Detartraj fictiv")
    await save_checklist(client, ctx["auth"], other)
    await set_procedures(client, ctx, appointment_id, [(procedure_id, 1), (other, 1)])
    await assert_reset(client, sessions, ctx, appointment_id, before)


async def test_removed_procedure_resets(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    other = await add_procedure(client, ctx["auth"], "Detartraj fictiv")
    await save_checklist(client, ctx["auth"], other)
    await set_procedures(client, ctx, appointment_id, [(procedure_id, 1), (other, 1)])
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    before = await card(client, ctx["auth"], appointment_id)
    assert before["state"] == "ready"
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    assert_kept(before, await card(client, ctx["auth"], appointment_id))
    await set_procedures(client, ctx, appointment_id, [(procedure_id, 1)])
    await assert_reset(client, sessions, ctx, appointment_id, before)


async def test_plan_change_resets(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, before = await ready_scenario(client, 1)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await assert_reset(client, sessions, ctx, appointment_id, before)


# Rule 6: material block still uses the remaining shortage
async def test_material_shortage_clears_after_completion(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await link(client, ctx, appointment_id, procedure_id, 2)
    stock = await add_stock(client, ctx["auth"], quantity="4")
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "3"}])
    assert saved.status_code == 201, saved.text
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    blocked = await card(client, ctx["auth"], appointment_id)
    assert blocked["state"] == "blocked" and "Lipsesc 2.000 buc" in blocked["blockers"][0]["text"]
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "ready" and not row["blockers"] and row["explanation"] is None
    assert row["procedures"][0]["quantity"] == 2


# Rule 7: clinic isolation and bounded queries
async def test_scope_is_isolated_per_clinic(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id, _before = await ready_scenario(client, 1)
    await performed(client, sessions, ctx, appointment_id, procedure_id)
    foreign = await register(client, "progress-foreign@example.com")
    foreign_procedure = await add_procedure(client, foreign["auth"])
    foreign_appointment = await add_appointment(client, foreign, starts_at=STARTS)
    await link(client, foreign, foreign_appointment, foreign_procedure, 2)
    await performed(client, sessions, foreign, foreign_appointment, foreign_procedure)
    async with sessions() as session:
        scope = await service.preparation_scope(session, UUID(ctx["me"]["clinic_id"]), [UUID(appointment_id), UUID(foreign_appointment)])
        foreign_scope = await service.preparation_scope(session, UUID(foreign["me"]["clinic_id"]), [UUID(appointment_id)])
    assert [row["quantity"] for row in scope[UUID(appointment_id)]] == [1]
    assert scope.get(UUID(foreign_appointment), []) == []
    assert foreign_scope.get(UUID(appointment_id), []) == []
    assert (await board(client, foreign["auth"]))["cards"][0]["appointment_id"] == foreign_appointment


async def test_board_query_count_with_completed_work(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, first = await scenario(client)
    await add_shift(sessions, ctx)
    await link(client, ctx, first, procedure_id, 2)
    await start(client, ctx["auth"], first)
    await performed(client, sessions, ctx, first, procedure_id)
    statements = []

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", count)
    await board(client, ctx["auth"])
    baseline = len(statements)
    event.remove(engine, "before_cursor_execute", count)
    for hour in range(10, 14):
        appointment_id = await add_appointment(client, ctx, starts_at=f"2026-12-02T{hour}:00:00+00:00")
        await link(client, ctx, appointment_id, procedure_id, 2)
        item_id = await add_plan_item(sessions, ctx, procedure_id, appointment_id)
        await start(client, ctx["auth"], appointment_id)
        await link_plan(sessions, item_id, await performed(client, sessions, ctx, appointment_id, procedure_id))
        await performed(client, sessions, ctx, appointment_id, procedure_id, status=None)
    statements.clear()
    event.listen(engine, "before_cursor_execute", count)
    try:
        cards = (await board(client, ctx["auth"]))["cards"]
        assert len(cards) == 5 and all(row["explanation"] is None for row in cards)
        assert len(statements) == baseline
    finally:
        event.remove(engine, "before_cursor_execute", count)
