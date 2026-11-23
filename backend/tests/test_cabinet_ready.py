"""T22 Cabinet Ready: rule -> test matrix on SQLite (PostgreSQL concurrency is in test_postgres_cabinet_ready.py)."""
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.cabinet_ready import service
from app.cabinet_ready.models import CabinetConfirmationEvent, CabinetPreparation
from app.database import Appointment, PerformedProcedure, User
from app.equipment.models import Equipment, EquipmentDependency, EquipmentUnavailability
from app.team.models import TeamLeaveRequest, TeamShift
from tests.test_materials import (
    add_appointment,
    add_plan_item,
    add_procedure,
    add_stock,
    env,  # noqa: F401
    register,
    save_recipe,
)

DAY = "2026-12-02"
STARTS = "2026-12-02T09:00:00+00:00"
LINES = [
    {"kind": "instrument", "label": "Trusă de consultație", "required": True, "prep_minutes": 10},
    {"kind": "document", "label": "Consimțământ tipărit", "required": True, "prep_minutes": 5},
    {"kind": "action", "label": "Aerisire cabinet", "required": False, "prep_minutes": 20},
]


def key():
    return {"client_request_id": str(uuid4())}


async def add_person(sessions, ctx, role):
    person = User(clinic_id=ctx["me"]["clinic_id"], email=f"{role}-{uuid4().hex}@example.com", password_hash="unused",
                  full_name=f"Persoană {role}", role=role, is_active=True)
    async with sessions() as session:
        session.add(person)
        await session.commit()
    from app.auth.service import create_access_token
    return {"id": str(person.id), "auth": {"Authorization": f"Bearer {create_access_token(person)}"}}


async def save_checklist(client, auth, procedure_id, expected=0, lines=None, request_id=None):
    return await client.put(f"/api/cabinet-ready/checklists/{procedure_id}", headers=auth, json={
        "expected_version": expected, "lines": lines or LINES, "client_request_id": request_id or str(uuid4()),
    })


async def link(client, ctx, appointment_id, procedure_id, quantity=1):
    current = await client.get(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"])
    response = await client.put(f"/api/appointment-procedures/{appointment_id}", headers=ctx["auth"], json={
        "expected_revision": current.json()["revision"], "procedures": [{"procedure_catalog_id": procedure_id, "quantity": quantity}],
    })
    assert response.status_code == 200, response.text


async def scenario(client, starts_at=STARTS, email="cabinet-manager@example.com"):
    ctx = await register(client, email)
    procedure_id = await add_procedure(client, ctx["auth"], "Obturație fictivă")
    saved = await save_checklist(client, ctx["auth"], procedure_id)
    assert saved.status_code == 201, saved.text
    appointment_id = await add_appointment(client, ctx, starts_at=starts_at)
    await link(client, ctx, appointment_id, procedure_id)
    return ctx, procedure_id, appointment_id


async def board(client, auth, day=DAY, **params):
    response = await client.get("/api/cabinet-ready/board", headers=auth, params={"day": day, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def card(client, auth, appointment_id, day=DAY):
    return next(row for row in (await board(client, auth, day))["cards"] if row["appointment_id"] == appointment_id)


async def start(client, auth, appointment_id):
    response = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/start", headers=auth, json=key())
    assert response.status_code == 201, response.text
    return response.json()


async def confirm(client, auth, item_id, request_id=None):
    return await client.post(f"/api/cabinet-ready/items/{item_id}/confirm", headers=auth,
                             json={"client_request_id": request_id or str(uuid4())})


async def confirm_required(client, auth, appointment_id):
    for line in (await card(client, auth, appointment_id))["items"]:
        if line["required"] and not line["confirmed"]:
            assert (await confirm(client, auth, line["id"])).status_code == 201


async def set_appointment(sessions, appointment_id, **values):
    async with sessions() as session:
        row = await session.get(Appointment, UUID(appointment_id))
        for name, value in values.items():
            setattr(row, name, value)
        await session.commit()


async def add_outage(sessions, ctx, *, room_id=None, procedure_id=None, closed=False):
    clinic_id, me = UUID(ctx["me"]["clinic_id"]), UUID(ctx["me"]["id"])
    async with sessions() as session:
        equipment = Equipment(clinic_id=clinic_id, name="Compresor fictiv", equipment_type="compresor", responsible_id=me,
                              state="maintenance", is_active=True, created_by_id=me)
        session.add(equipment)
        await session.flush()
        session.add(EquipmentDependency(clinic_id=clinic_id, equipment_id=equipment.id, room_id=UUID(room_id) if room_id else None,
                                        procedure_id=UUID(procedure_id) if procedure_id else None, created_by_id=me))
        starts = datetime(2026, 12, 2, 8, tzinfo=UTC)
        session.add(EquipmentUnavailability(clinic_id=clinic_id, equipment_id=equipment.id, reason="Revizie fictivă", starts_at=starts,
                                            ends_at=starts + timedelta(hours=4), reported_by_id=me,
                                            closed_at=starts if closed else None, closed_by_id=me if closed else None))
        await session.commit()


async def add_leave(sessions, ctx, status="approved"):
    clinic_id, me = UUID(ctx["me"]["clinic_id"]), UUID(ctx["me"]["id"])
    async with sessions() as session:
        session.add(TeamLeaveRequest(clinic_id=clinic_id, user_id=me, leave_type="annual", starts_on=date(2026, 12, 2),
                                     ends_on=date(2026, 12, 2), status=status, requested_by_id=me))
        await session.commit()


async def add_shift(sessions, ctx):
    clinic_id, me = UUID(ctx["me"]["clinic_id"]), UUID(ctx["me"]["id"])
    async with sessions() as session:
        session.add(TeamShift(clinic_id=clinic_id, user_id=me, room_id=UUID(ctx["room_id"]), post="doctor",
                              starts_at=datetime(2026, 12, 2, 7, tzinfo=UTC), ends_at=datetime(2026, 12, 2, 15, tzinfo=UTC),
                              status="active", created_by_id=me))
        await session.commit()


async def add_shortage(client, ctx, procedure_id):
    stock = await add_stock(client, ctx["auth"], quantity="1")
    saved = await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "3"}])
    assert saved.status_code == 201, saved.text
    return stock


# 1. Versioned checklist
async def test_checklist_save_creates_versions_and_rejects_stale_edit(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    assert (await save_checklist(client, ctx["auth"], procedure_id)).json()["version"] == 1
    assert (await save_checklist(client, ctx["auth"], procedure_id, 1, LINES[:1])).json()["version"] == 2
    assert (await save_checklist(client, ctx["auth"], procedure_id, 1)).status_code == 409
    current = (await client.get(f"/api/cabinet-ready/checklists/{procedure_id}", headers=ctx["auth"])).json()
    assert current["version"] == 2 and current["versions"] == [1, 2] and len(current["lines"]) == 1


async def test_checklist_retry_does_not_create_a_second_version(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    request_id = str(uuid4())
    first = await save_checklist(client, ctx["auth"], procedure_id, request_id=request_id)
    retry = await save_checklist(client, ctx["auth"], procedure_id, request_id=request_id)
    assert first.json()["id"] == retry.json()["id"] and retry.json()["retry"] is True
    changed = await save_checklist(client, ctx["auth"], procedure_id, lines=LINES[:1], request_id=request_id)
    assert changed.status_code == 409


@pytest.mark.parametrize("role", ["doctor", "assistant", "reception"])
async def test_only_managers_edit_checklists(env, role):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    person = await add_person(sessions, ctx, role)
    procedure_id = await add_procedure(client, ctx["auth"])
    assert (await save_checklist(client, person["auth"], procedure_id)).status_code == 403


@pytest.mark.parametrize("lines", [
    [], [{"kind": "action", "label": "Opțional", "required": False, "prep_minutes": 1}],
    [{"kind": "altceva", "label": "X", "required": True, "prep_minutes": 1}],
    [{"kind": "action", "label": "   ", "required": True, "prep_minutes": 1}],
    [{"kind": "action", "label": "X", "required": True, "prep_minutes": 241}],
])
async def test_checklist_validation(env, lines):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    response = await client.put(f"/api/cabinet-ready/checklists/{procedure_id}", headers=ctx["auth"], json={
        "expected_version": 0, "lines": lines, **key()})
    assert response.status_code == 422


# 2. Snapshot from effective procedures
async def test_template_change_does_not_rewrite_started_preparation(env):  # noqa: F811
    client, _sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    await save_checklist(client, ctx["auth"], procedure_id, 1, [{"kind": "action", "label": "Nou", "required": True, "prep_minutes": 1}])
    row = await card(client, ctx["auth"], appointment_id)
    assert [line["label"] for line in row["items"]] == [line["label"] for line in LINES]
    assert row["explanation"] is None and row["started"] is True


async def test_preparation_starts_from_plan_procedure(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    await save_checklist(client, ctx["auth"], procedure_id)
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS)
    await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await start(client, ctx["auth"], appointment_id)
    assert (await card(client, ctx["auth"], appointment_id))["total_required"] == 2


# 3. Physical confirmation
async def test_confirmation_saves_author_and_moment_and_double_click_is_single(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    request_id = str(uuid4())
    first = await confirm(client, ctx["auth"], item_id, request_id)
    second = await confirm(client, ctx["auth"], item_id, request_id)
    assert first.status_code == second.status_code == 201 and first.json()["id"] == second.json()["id"]
    assert second.json()["retry"] is True
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetConfirmationEvent.id))) == 1
    line = (await card(client, ctx["auth"], appointment_id))["items"][0]
    assert line["confirmed"] and line["confirmed_by"] == "Manager Materiale" and line["confirmed_at"]
    assert (await confirm(client, ctx["auth"], item_id)).status_code == 409


@pytest.mark.parametrize("role", ["doctor", "assistant", "administrator"])
async def test_worker_roles_can_confirm(env, role):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    person = await add_person(sessions, ctx, role)
    await start(client, person["auth"], appointment_id)
    item_id = (await card(client, person["auth"], appointment_id))["items"][0]["id"]
    assert (await confirm(client, person["auth"], item_id)).status_code == 201


async def test_revocation_is_a_new_record_with_reason(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    await confirm(client, ctx["auth"], item_id)
    url = f"/api/cabinet-ready/items/{item_id}/revoke"
    assert (await client.post(url, headers=ctx["auth"], json={**key(), "reason": "  "})).status_code == 422
    assert (await client.post(url, headers=ctx["auth"], json={**key(), "reason": "Trusă greșită"})).status_code == 201
    assert (await client.post(url, headers=ctx["auth"], json={**key(), "reason": "Din nou"})).status_code == 409
    history = (await client.get(f"/api/cabinet-ready/appointments/{appointment_id}/history", headers=ctx["auth"])).json()
    assert [row["action"] for row in history["confirmations"]] == ["confirm", "revoke"]
    assert history["confirmations"][1]["reason"] == "Trusă greșită"
    assert (await card(client, ctx["auth"], appointment_id))["confirmed_required"] == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetConfirmationEvent.id))) == 2


# 4. States
async def test_unknown_without_effective_procedure_never_ready(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "unknown" and row["total_required"] == 0
    response = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/start", headers=ctx["auth"], json=key())
    assert response.status_code == 409


async def test_unknown_with_procedure_without_checklist(env):  # noqa: F811
    client, _sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"], "Fără checklist")
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS)
    await link(client, ctx, appointment_id, procedure_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "unknown" and "Fără checklist" in row["unknown_reasons"][0]


async def test_in_preparation_counter_then_ready(env):  # noqa: F811
    client, _sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    row = await card(client, ctx["auth"], appointment_id)
    assert (row["state"], row["counter"]) == ("in_preparation", "0 din 2")
    await start(client, ctx["auth"], appointment_id)
    first = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    await confirm(client, ctx["auth"], first)
    row = await card(client, ctx["auth"], appointment_id)
    assert (row["state"], row["counter"]) == ("in_preparation", "1 din 2")
    await confirm_required(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert (row["state"], row["counter"]) == ("ready", "2 din 2") and row["ready_at"]


# 5. Blocks
async def test_material_shortage_blocks_with_exact_text(env):  # noqa: F811
    client, _sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    stock = await add_shortage(client, ctx, procedure_id)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "blocked" and row["blockers"][0]["type"] == "material"
    assert "Lipsesc 2.000 buc" in row["blockers"][0]["text"] and row["blockers"][0]["check_with"]
    assert stock["item_id"]


async def test_sure_equipment_blocks_and_possible_only_warns(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await add_outage(sessions, ctx, room_id=ctx["room_id"])
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "blocked" and row["blockers"][0]["type"] == "equipment"
    assert "Compresor fictiv" in row["blockers"][0]["text"] and "Manager Materiale" in row["blockers"][0]["check_with"]


async def test_possible_equipment_match_is_a_warning(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    linked = await add_procedure(client, ctx["auth"], "Obturație fictivă")
    await save_checklist(client, ctx["auth"], linked)
    service_only = await add_procedure(client, ctx["auth"], "Control")
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS, appointment_type="Control")
    await link(client, ctx, appointment_id, linked)
    await add_outage(sessions, ctx, procedure_id=service_only)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "in_preparation" and not row["blockers"]
    assert any(warning["type"] == "equipment_possible" for warning in row["warnings"])


async def test_effective_procedure_dependency_is_a_sure_equipment_match(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await add_outage(sessions, ctx, procedure_id=procedure_id)
    assert (await card(client, ctx["auth"], appointment_id))["blockers"][0]["type"] == "equipment"


async def test_closed_outage_does_not_block(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await add_outage(sessions, ctx, room_id=ctx["room_id"], closed=True)
    assert not (await card(client, ctx["auth"], appointment_id))["blockers"]


async def test_approved_leave_blocks_pending_does_not_and_missing_shift_warns(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    row = await card(client, ctx["auth"], appointment_id)
    assert any(warning["type"] == "no_shift" for warning in row["warnings"]) and not row["blockers"]
    await add_shift(sessions, ctx)
    assert not (await card(client, ctx["auth"], appointment_id))["warnings"]
    await add_leave(sessions, ctx, status="pending")
    assert not (await card(client, ctx["auth"], appointment_id))["blockers"]
    await add_leave(sessions, ctx)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "blocked" and row["blockers"][0]["type"] == "leave" and "concediu aprobat" in row["blockers"][0]["text"]


async def test_manual_block_opens_and_closes_by_new_record(env):  # noqa: F811
    client, _sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    url = f"/api/cabinet-ready/appointments/{appointment_id}/blocks"
    assert (await client.post(url, headers=ctx["auth"], json={**key(), "reason": " ", "check_with": "Ana"})).status_code == 422
    opened = await client.post(url, headers=ctx["auth"], json={**key(), "reason": "Scaun defect", "check_with": "Ana, recepție"})
    assert opened.status_code == 201
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "blocked" and row["blockers"][0]["text"] == "Scaun defect" and row["blockers"][0]["check_with"] == "Ana, recepție"
    close_url = f"/api/cabinet-ready/blocks/{opened.json()['id']}/close"
    assert (await client.post(close_url, headers=ctx["auth"], json={**key(), "reason": "Reparat"})).status_code == 201
    assert (await client.post(close_url, headers=ctx["auth"], json={**key(), "reason": "Iar"})).status_code == 409
    assert (await card(client, ctx["auth"], appointment_id))["state"] == "ready"
    history = (await client.get(f"/api/cabinet-ready/appointments/{appointment_id}/history", headers=ctx["auth"])).json()
    assert history["blocks"][0]["reason"] == "Scaun defect" and history["blocks"][0]["close_reason"] == "Reparat"


# 6. Stock never confirms
async def test_sufficient_stock_confirms_nothing(env):  # noqa: F811
    client, _sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    stock = await add_stock(client, ctx["auth"], quantity="100")
    await save_recipe(client, ctx["auth"], procedure_id, 0, [{"item_id": stock["item_id"], "estimated_quantity": "1"}])
    await start(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "in_preparation" and row["confirmed_required"] == 0
    assert not any(line["confirmed"] for line in row["items"])


# 7. Changes after confirmation
@pytest.mark.parametrize("change", ["time", "room", "procedures"])
async def test_change_after_confirmation_returns_to_preparation_and_keeps_history(env, change):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    assert (await card(client, ctx["auth"], appointment_id))["state"] == "ready"
    expected = {"time": "ora", "room": "cabinetul", "procedures": "procedurile"}[change]
    if change == "time":
        await set_appointment(sessions, appointment_id, starts_at=datetime(2026, 12, 2, 10, tzinfo=UTC),
                              ends_at=datetime(2026, 12, 2, 10, 30, tzinfo=UTC))
    elif change == "room":
        room = await client.post("/api/rooms", headers=ctx["auth"], json={"name": "Cabinet 2 fictiv"})
        await set_appointment(sessions, appointment_id, room_id=UUID(room.json()["id"]))
    else:
        await link(client, ctx, appointment_id, procedure_id, 2)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["state"] == "in_preparation" and row["confirmed_required"] == 0 and expected in row["explanation"]
    assert (await confirm(client, ctx["auth"], item_id)).status_code == 409
    history = (await client.get(f"/api/cabinet-ready/appointments/{appointment_id}/history", headers=ctx["auth"])).json()
    assert len([entry for entry in history["confirmations"] if entry["action"] == "confirm"]) == 2
    await start(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["explanation"] is None and row["confirmed_required"] == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetPreparation.id))) == 2


@pytest.mark.parametrize("status", ["cancelled", "completed", "no_show"])
async def test_closed_appointments_refuse_writes(env, status):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    opened = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/blocks", headers=ctx["auth"],
                               json={**key(), "reason": "Blocaj", "check_with": "Ana"})
    await set_appointment(sessions, appointment_id, status=status)
    base = "/api/cabinet-ready"
    responses = [
        await client.post(f"{base}/appointments/{appointment_id}/start", headers=ctx["auth"], json=key()),
        await confirm(client, ctx["auth"], item_id),
        await client.post(f"{base}/items/{item_id}/revoke", headers=ctx["auth"], json={**key(), "reason": "Motiv"}),
        await client.post(f"{base}/appointments/{appointment_id}/blocks", headers=ctx["auth"], json={**key(), "reason": "B", "check_with": "A"}),
        await client.post(f"{base}/blocks/{opened.json()['id']}/close", headers=ctx["auth"], json={**key(), "reason": "Închis"}),
    ]
    assert [response.status_code for response in responses] == [409] * 5


# 8. Timing
async def test_start_by_uses_required_minutes_in_clinic_time_and_marks_late(env, monkeypatch):  # noqa: F811
    client, _sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    monkeypatch.setattr(service, "utcnow", lambda: datetime(2026, 12, 2, 8, 44, tzinfo=UTC))
    row = await card(client, ctx["auth"], appointment_id)
    assert (row["prep_minutes"], row["start_by_local"], row["late"]) == (15, "10:45", False)
    monkeypatch.setattr(service, "utcnow", lambda: datetime(2026, 12, 2, 8, 46, tzinfo=UTC))
    assert (await card(client, ctx["auth"], appointment_id))["late"] is True
    await start(client, ctx["auth"], appointment_id)
    row = await card(client, ctx["auth"], appointment_id)
    assert row["late"] is False and row["started_at"]


async def test_preparation_does_not_change_the_agenda(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    async with sessions() as session:
        row = await session.get(Appointment, UUID(appointment_id))
        assert row.starts_at == datetime(2026, 12, 2, 9, tzinfo=UTC) and row.status == "scheduled"


async def test_board_day_uses_clinic_timezone(env):  # noqa: F811
    client, _sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client, starts_at="2026-12-02T22:30:00+00:00")
    assert not (await board(client, ctx["auth"], "2026-12-02"))["cards"]
    row = (await board(client, ctx["auth"], "2026-12-03"))["cards"][0]
    assert row["appointment_id"] == appointment_id and row["local_time"] == "00:30" and row["local_date"] == "2026-12-03"


async def test_board_filters_by_room(env):  # noqa: F811
    client, _sessions = env
    ctx, _procedure_id, _appointment_id = await scenario(client)
    other = await client.post("/api/rooms", headers=ctx["auth"], json={"name": "Alt cabinet"})
    assert len((await board(client, ctx["auth"], room_id=ctx["room_id"]))["cards"]) == 1
    assert (await board(client, ctx["auth"], room_id=other.json()["id"]))["cards"] == []


# 9. Particularities
async def test_particularity_requires_doctor_decision_and_only_approved_enters(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    assistant = await add_person(sessions, ctx, "assistant")
    other_doctor = await add_person(sessions, ctx, "doctor")
    url = f"/api/cabinet-ready/appointments/{appointment_id}/particularities"
    body = {"text": "Pacient anxios", "item_kind": "action", "item_label": "Pregătește muzică", "item_required": True, "item_minutes": 5}
    assert (await client.post(url, headers=ctx["auth"], json={**key(), **body})).status_code == 403
    proposed = await client.post(url, headers=assistant["auth"], json={**key(), **body})
    assert proposed.status_code == 201
    labels = [line["label"] for line in (await card(client, ctx["auth"], appointment_id))["items"]]
    assert "Pregătește muzică" not in labels
    decide = f"/api/cabinet-ready/particularities/{proposed.json()['id']}/decision"
    assert (await client.post(decide, headers=assistant["auth"], json={**key(), "decision": "approved"})).status_code == 403
    assert (await client.post(decide, headers=other_doctor["auth"], json={**key(), "decision": "approved"})).status_code == 403
    assert (await client.post(decide, headers=ctx["auth"], json={**key(), "decision": "approved"})).status_code == 201
    assert (await client.post(decide, headers=ctx["auth"], json={**key(), "decision": "rejected"})).status_code == 409
    row = await card(client, ctx["auth"], appointment_id)
    assert "Pregătește muzică" in [line["label"] for line in row["items"]] and row["total_required"] == 3


async def test_rejected_particularity_stays_out(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    assistant = await add_person(sessions, ctx, "assistant")
    proposed = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/particularities", headers=assistant["auth"], json={
        **key(), "text": "Propunere", "item_kind": "material", "item_label": "Extra", "item_required": True, "item_minutes": 1})
    await client.post(f"/api/cabinet-ready/particularities/{proposed.json()['id']}/decision", headers=ctx["auth"],
                      json={**key(), "decision": "rejected", "reason": "Nu e necesar"})
    assert "Extra" not in [line["label"] for line in (await card(client, ctx["auth"], appointment_id))["items"]]


async def test_approved_item_after_ready_returns_to_preparation(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    assistant = await add_person(sessions, ctx, "assistant")
    await start(client, ctx["auth"], appointment_id)
    await confirm_required(client, ctx["auth"], appointment_id)
    proposed = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/particularities", headers=assistant["auth"], json={
        **key(), "text": "Adaos", "item_kind": "action", "item_label": "Extra", "item_required": True, "item_minutes": 2})
    await client.post(f"/api/cabinet-ready/particularities/{proposed.json()['id']}/decision", headers=ctx["auth"], json={**key(), "decision": "approved"})
    row = await card(client, ctx["auth"], appointment_id)
    assert (row["state"], row["counter"]) == ("in_preparation", "2 din 3")


async def test_particularity_item_must_be_complete(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    assistant = await add_person(sessions, ctx, "assistant")
    response = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/particularities", headers=assistant["auth"],
                                 json={**key(), "text": "T", "item_kind": "action"})
    assert response.status_code == 422


# 10. Reception
async def test_reception_sees_state_and_block_type_without_details_or_writes(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    reception = await add_person(sessions, ctx, "reception")
    assistant = await add_person(sessions, ctx, "assistant")
    await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/blocks", headers=ctx["auth"],
                      json={**key(), "reason": "Text secret blocaj", "check_with": "Persoană secretă"})
    await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/particularities", headers=assistant["auth"],
                      json={**key(), "text": "Text clinic secret"})
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    row = await card(client, reception["auth"], appointment_id)
    assert row["state"] == "blocked" and row["counter"] == "0 din 2"
    assert row["blockers"] == [{"type": "manual", "type_label": "Blocaj manual"}]
    encoded = str(row)
    for hidden in ("Text secret", "Persoană secretă", "Text clinic", "Obturație", "Trusă", "items", "particularities", "procedures", "missing"):
        assert hidden not in encoded
    responses = [
        await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/start", headers=reception["auth"], json=key()),
        await confirm(client, reception["auth"], item_id),
        await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/blocks", headers=reception["auth"], json={**key(), "reason": "R", "check_with": "C"}),
        await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/particularities", headers=reception["auth"], json={**key(), "text": "T"}),
        await client.get(f"/api/cabinet-ready/appointments/{appointment_id}/history", headers=reception["auth"]),
    ]
    assert [response.status_code for response in responses] == [403] * 5


# Isolation
async def test_foreign_clinic_gets_404_and_empty_board(env):  # noqa: F811
    client, _sessions = env
    ctx, procedure_id, appointment_id = await scenario(client)
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    foreign = await register(client, "cabinet-foreign@example.com")
    assert (await board(client, foreign["auth"]))["cards"] == []
    base = "/api/cabinet-ready"
    responses = [
        await client.get(f"{base}/checklists/{procedure_id}", headers=foreign["auth"]),
        await save_checklist(client, foreign["auth"], procedure_id),
        await client.post(f"{base}/appointments/{appointment_id}/start", headers=foreign["auth"], json=key()),
        await confirm(client, foreign["auth"], item_id),
        await client.post(f"{base}/appointments/{appointment_id}/blocks", headers=foreign["auth"], json={**key(), "reason": "R", "check_with": "C"}),
        await client.get(f"{base}/appointments/{appointment_id}/history", headers=foreign["auth"]),
    ]
    assert [response.status_code for response in responses] == [404] * 6


async def test_request_key_of_other_actor_is_refused(env):  # noqa: F811
    client, sessions = env
    ctx, _procedure_id, appointment_id = await scenario(client)
    assistant = await add_person(sessions, ctx, "assistant")
    await start(client, ctx["auth"], appointment_id)
    item_id = (await card(client, ctx["auth"], appointment_id))["items"][0]["id"]
    request_id = str(uuid4())
    assert (await confirm(client, ctx["auth"], item_id, request_id)).status_code == 201
    assert (await confirm(client, assistant["auth"], item_id, request_id)).status_code == 409


async def test_open_performed_procedure_does_not_invalidate_preparation(env):  # noqa: F811
    client, sessions = env
    ctx = await register(client)
    procedure_id = await add_procedure(client, ctx["auth"])
    await save_checklist(client, ctx["auth"], procedure_id)
    appointment_id = await add_appointment(client, ctx, starts_at=STARTS)
    item_id = await add_plan_item(sessions, ctx, procedure_id, appointment_id)
    await start(client, ctx["auth"], appointment_id)
    performed = await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "procedure_catalog_id": procedure_id})
    from app.database import TreatmentPlanItem
    async with sessions() as session:
        (await session.get(TreatmentPlanItem, UUID(item_id))).performed_procedure_id = UUID(performed.json()["id"])
        assert (await session.get(PerformedProcedure, UUID(performed.json()["id"]))).status != "completed"
        await session.commit()
    assert (await card(client, ctx["auth"], appointment_id))["explanation"] is None


async def test_board_query_count_does_not_grow_with_appointments(env):  # noqa: F811
    client, sessions = env
    ctx, procedure_id, first = await scenario(client)
    await add_outage(sessions, ctx, room_id=ctx["room_id"])
    await add_shift(sessions, ctx)
    await start(client, ctx["auth"], first)
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
        await link(client, ctx, appointment_id, procedure_id)
        await start(client, ctx["auth"], appointment_id)
    statements.clear()
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert len((await board(client, ctx["auth"]))["cards"]) == 5
        assert len(statements) == baseline
    finally:
        event.remove(engine, "before_cursor_execute", count)
