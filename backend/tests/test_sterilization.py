"""T23 sterilization and traceability: sets, packs, cycles, approval, use, recall, reverse trace."""
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import Base, User, get_db, utcnow
from app.main import app
from app.sterilization.models import SterilizationPackEvent

PASSWORD = "correct horse battery staple"
API = "/api/sterilization"


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
            yield client, sessions, engine
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def register(client, email=None):
    email = email or f"ster-{uuid4().hex[:8]}@example.com"
    response = await client.post("/api/auth/register", json={
        "clinic_name": "Clinică fictivă sterilizare", "full_name": "Manager Sterilizare", "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": f"Cabinet {uuid4().hex[:6]}"})
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Fictivă"})
    return {"auth": auth, "me": me, "room_id": room.json()["id"], "patient_id": patient.json()["id"]}


async def add_user(sessions, clinic_id, role):
    user = User(clinic_id=clinic_id, email=f"{role}-{uuid4().hex}@example.com", password_hash="unused",
                full_name=f"Utilizator {role}", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
        return {"Authorization": f"Bearer {create_access_token(user)}"}, str(user.id)


async def add_sterilizer(client, ctx, name="Autoclav fictiv", equipment_type="sterilizator"):
    response = await client.post("/api/equipment", headers=ctx["auth"], json={
        "name": name, "equipment_type": equipment_type, "responsible_id": ctx["me"]["id"]})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_set_type(client, auth, name="Set consultație", expiry_days=None):
    body = {"name": name, "description": "oglindă, sondă, pensă"}
    if expiry_days is not None:
        body["expiry_days"] = expiry_days
    response = await client.post(f"{API}/set-types", headers=auth, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def add_pack(client, auth, set_type_id, code=None):
    response = await client.post(f"{API}/packs", headers=auth, json={"set_type_id": set_type_id, "code": code or f"P-{uuid4().hex[:6]}"})
    assert response.status_code == 201, response.text
    return response.json()


async def start_cycle(client, auth, equipment_id, pack_ids, number=None, operator_id=None):
    body = {"equipment_id": equipment_id, "program": "134°C / 18 min", "pack_ids": pack_ids}
    if number is not None:
        body["cycle_number"] = number
    if operator_id:
        body["operator_id"] = operator_id
    return await client.post(f"{API}/cycles", headers=auth, json=body)


FINISH_OK = {"temperature_c": "134.0", "pressure_kpa": "210", "hold_minutes": 18, "chemical_result": "pass", "biological_result": "pending"}


async def finish_cycle(client, auth, cycle_id, **override):
    return await client.post(f"{API}/cycles/{cycle_id}/finish", headers=auth, json={**FINISH_OK, **override})


async def decide(client, auth, cycle_id, decision="approved", note=None):
    return await client.post(f"{API}/cycles/{cycle_id}/decision", headers=auth, json={"decision": decision, "note": note})


async def approved_cycle(client, ctx, assistant_auth, equipment_id, pack_ids, number=None):
    """Manager runs the cycle, an assistant approves it."""
    cycle = await start_cycle(client, ctx["auth"], equipment_id, pack_ids, number)
    assert cycle.status_code == 201, cycle.text
    cycle_id = cycle.json()["id"]
    assert (await finish_cycle(client, ctx["auth"], cycle_id)).status_code == 200
    approval = await decide(client, assistant_auth, cycle_id)
    assert approval.status_code == 200, approval.text
    return cycle_id


async def add_appointment(client, ctx, starts_at="2026-12-02T09:00:00+00:00"):
    response = await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": ctx["patient_id"], "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"],
        "starts_at": starts_at, "duration_minutes": 30, "appointment_type": "Control"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def setup_world(client, sessions):
    ctx = await register(client)
    assistant, assistant_id = await add_user(sessions, ctx["me"]["clinic_id"], "assistant")
    equipment_id = await add_sterilizer(client, ctx)
    set_type = await add_set_type(client, ctx["auth"])
    return ctx, assistant, assistant_id, equipment_id, set_type


@pytest.mark.asyncio
async def test_pack_state_flows_from_history(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    assert pack["state"] == "unsterile"
    cycle = (await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])).json()
    assert cycle["status"] == "in_progress"
    listing = (await client.get(f"{API}/packs", headers=ctx["auth"])).json()
    assert listing[0]["state"] == "in_cycle"
    await finish_cycle(client, ctx["auth"], cycle["id"])
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "in_cycle"
    approved = await decide(client, assistant, cycle["id"])
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    sterile = (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]
    assert sterile["state"] == "sterile"
    assert sterile["expires_at"]
    days = (__import__("datetime").datetime.fromisoformat(sterile["expires_at"]) - utcnow()).days
    assert 28 <= days <= 30  # default 30 days
    history = (await client.get(f"{API}/packs/{pack['id']}/history", headers=ctx["auth"])).json()
    assert [row["event_type"] for row in history] == ["entered_cycle", "sterilized"]


@pytest.mark.asyncio
async def test_set_type_expiry_is_configurable_and_duplicate_pack_code_refused(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, _ = await setup_world(client, sessions)
    short = await add_set_type(client, ctx["auth"], "Set scurt", expiry_days=7)
    pack = await add_pack(client, ctx["auth"], short["id"], "COD-1")
    duplicate = await client.post(f"{API}/packs", headers=ctx["auth"], json={"set_type_id": short["id"], "code": "COD-1"})
    assert duplicate.status_code == 409
    await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    row = (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]
    days = (__import__("datetime").datetime.fromisoformat(row["expires_at"]) - utcnow()).days
    assert 5 <= days <= 7
    assert (await client.post(f"{API}/set-types", headers=ctx["auth"], json={"name": "Set scurt"})).status_code == 409
    assert (await client.post(f"{API}/set-types", headers=ctx["auth"], json={"name": "Rău", "expiry_days": 0})).status_code == 422


@pytest.mark.asyncio
async def test_duplicate_cycle_number_on_same_device_refused_other_device_allowed(env):
    client, sessions, _ = env
    ctx, _, _, equipment_id, set_type = await setup_world(client, sessions)
    second_device = await add_sterilizer(client, ctx, "Autoclav 2")
    packs = [(await add_pack(client, ctx["auth"], set_type["id"]))["id"] for _ in range(3)]
    assert (await start_cycle(client, ctx["auth"], equipment_id, [packs[0]], number=7)).status_code == 201
    duplicate = await start_cycle(client, ctx["auth"], equipment_id, [packs[1]], number=7)
    assert duplicate.status_code == 409
    assert "număr" in duplicate.json()["detail"].lower()
    assert (await start_cycle(client, ctx["auth"], second_device, [packs[1]], number=7)).status_code == 201
    auto = await start_cycle(client, ctx["auth"], equipment_id, [packs[2]])
    assert auto.json()["cycle_number"] == 8


@pytest.mark.asyncio
async def test_operator_cannot_approve_own_cycle_and_author_is_recorded(env):
    client, sessions, _ = env
    ctx, assistant, assistant_id, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle = (await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])).json()
    assert cycle["operator_id"] == ctx["me"]["id"]
    await finish_cycle(client, ctx["auth"], cycle["id"])
    own = await decide(client, ctx["auth"], cycle["id"])
    assert own.status_code == 403
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "in_cycle"
    approved = await decide(client, assistant, cycle["id"], note="Indicatori conformi")
    assert approved.status_code == 200
    body = approved.json()
    assert body["decided_by_id"] == assistant_id and body["decided_at"] and body["decision_note"] == "Indicatori conformi"
    again = await decide(client, assistant, cycle["id"], "rejected")
    assert again.status_code == 409
    # a doctor cannot approve either
    doctor, _ = await add_user(sessions, ctx["me"]["clinic_id"], "doctor")
    assert (await decide(client, doctor, cycle["id"])).status_code == 403


@pytest.mark.asyncio
async def test_approval_needs_finished_cycle_and_conforming_indicators(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle = (await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])).json()
    early = await decide(client, assistant, cycle["id"])
    assert early.status_code == 409
    await finish_cycle(client, ctx["auth"], cycle["id"], chemical_result="fail")
    refused = await decide(client, assistant, cycle["id"])
    assert refused.status_code == 409 and "chimic" in refused.json()["detail"].lower()
    rejected = await decide(client, assistant, cycle["id"], "rejected", "Indicator chimic neconform")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_rejected_cycle_sterilizes_nothing_and_pack_can_be_reprocessed(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle = (await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])).json()
    await finish_cycle(client, ctx["auth"], cycle["id"])
    assert (await decide(client, assistant, cycle["id"], "rejected", "Ciclu întrerupt")).status_code == 200
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "unsterile"
    appointment_id = await add_appointment(client, ctx)
    refused = await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_id})
    assert refused.status_code == 409 and "steril" in refused.json()["detail"].lower()
    # reprocessing = a new cycle
    new_cycle = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "sterile"
    assert new_cycle != cycle["id"]


@pytest.mark.asyncio
async def test_unsterile_pack_and_in_cycle_pack_refused_for_use(env):
    client, sessions, _ = env
    ctx, _, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    appointment_id = await add_appointment(client, ctx)
    plain = await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_id})
    assert plain.status_code == 409 and "nesteril" in plain.json()["detail"].lower()
    await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])
    in_cycle = await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_id})
    assert in_cycle.status_code == 409 and "ciclu" in in_cycle.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pack_cannot_join_two_cycles_or_be_listed_twice(env):
    client, sessions, _ = env
    ctx, _, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    assert (await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])).status_code == 201
    second = await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])
    assert second.status_code == 409
    other = await add_pack(client, ctx["auth"], set_type["id"])
    twice = await start_cycle(client, ctx["auth"], equipment_id, [other["id"], other["id"]])
    assert twice.status_code == 422
    empty = await start_cycle(client, ctx["auth"], equipment_id, [])
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_use_links_once_double_click_is_idempotent_and_second_visit_refused(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    first_appointment = await add_appointment(client, ctx)
    second_appointment = await add_appointment(client, ctx, "2026-12-03T09:00:00+00:00")
    body = {"appointment_id": first_appointment}
    first = await client.post(f"{API}/packs/{pack['id']}/use", headers=assistant, json=body)
    assert first.status_code == 201, first.text
    again = await client.post(f"{API}/packs/{pack['id']}/use", headers=assistant, json=body)
    assert again.status_code == 200 and again.json()["event_id"] == first.json()["event_id"]
    other = await client.post(f"{API}/packs/{pack['id']}/use", headers=assistant, json={"appointment_id": second_appointment})
    assert other.status_code == 409 and "folosit" in other.json()["detail"].lower()
    async with sessions() as session:
        used = await session.scalar(select(func.count()).select_from(SterilizationPackEvent).where(SterilizationPackEvent.event_type == "used"))
    assert used == 1
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "used"


@pytest.mark.asyncio
async def test_expired_pack_refused_with_reason(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    async with sessions() as session:
        event_row = await session.scalar(select(SterilizationPackEvent).where(SterilizationPackEvent.event_type == "sterilized"))
        event_row.expires_at = utcnow() - timedelta(days=1)
        await session.commit()
    assert (await client.get(f"{API}/packs", headers=ctx["auth"])).json()[0]["state"] == "expired"
    appointment_id = await add_appointment(client, ctx)
    refused = await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_id})
    assert refused.status_code == 409 and "expirat" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unavailable_or_wrong_device_refused_with_reason(env):
    client, sessions, _ = env
    ctx, _, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    chair = await add_sterilizer(client, ctx, "Unit dentar", equipment_type="unit dentar")
    wrong = await start_cycle(client, ctx["auth"], chair, [pack["id"]])
    assert wrong.status_code == 409 and "sterilizator" in wrong.json()["detail"].lower()
    start = utcnow() - timedelta(minutes=5)
    outage = await client.post(f"/api/equipment/{equipment_id}/unavailabilities", headers=ctx["auth"], json={
        "reason": "Pompă defectă", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=3)).isoformat()})
    assert outage.status_code == 201
    blocked = await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])
    assert blocked.status_code == 409 and "Pompă defectă" in blocked.json()["detail"]
    await client.post(f"/api/equipment/{equipment_id}/unavailabilities/{outage.json()['id']}/close", headers=ctx["auth"])
    broken = await client.patch(f"/api/equipment/{equipment_id}", headers=ctx["auth"], json={
        "name": "Autoclav fictiv", "equipment_type": "sterilizator", "responsible_id": ctx["me"]["id"], "state": "broken"})
    assert broken.status_code == 200
    state_blocked = await start_cycle(client, ctx["auth"], equipment_id, [pack["id"]])
    assert state_blocked.status_code == 409 and "indisponibil" in state_blocked.json()["detail"].lower()


@pytest.mark.asyncio
async def test_recall_lists_exactly_patients_of_compromised_cycle_and_exports_csv(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    bad_pack = await add_pack(client, ctx["auth"], set_type["id"], "BAD-1")
    unused_pack = await add_pack(client, ctx["auth"], set_type["id"], "BAD-2")
    good_pack = await add_pack(client, ctx["auth"], set_type["id"], "GOOD-1")
    bad_cycle = await approved_cycle(client, ctx, assistant, equipment_id, [bad_pack["id"], unused_pack["id"]])
    good_cycle = await approved_cycle(client, ctx, assistant, equipment_id, [good_pack["id"]])
    other_patient = (await client.post("/api/patients", headers=ctx["auth"], json={"first_name": "Dan", "last_name": "Altul"})).json()
    appointment_bad = await add_appointment(client, ctx)
    other_appointment = (await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": other_patient["id"], "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"],
        "starts_at": "2026-12-04T09:00:00+00:00", "duration_minutes": 30, "appointment_type": "Control"})).json()["id"]
    assert (await client.post(f"{API}/packs/{bad_pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_bad})).status_code == 201
    assert (await client.post(f"{API}/packs/{good_pack['id']}/use", headers=ctx["auth"], json={"appointment_id": other_appointment})).status_code == 201
    result = await client.post(f"{API}/cycles/{bad_cycle}/indicators", headers=assistant, json={"kind": "biological", "result": "fail", "note": "Spori viabili"})
    assert result.status_code == 201, result.text
    cycle = (await client.get(f"{API}/cycles/{bad_cycle}", headers=ctx["auth"])).json()
    assert cycle["compromised"] is True and cycle["status"] == "approved"
    recall = (await client.get(f"{API}/cycles/{bad_cycle}/recall", headers=ctx["auth"])).json()
    assert [item["patient_id"] for item in recall["items"]] == [ctx["patient_id"]]
    assert recall["items"][0]["pack_code"] == "BAD-1" and recall["items"][0]["reason"]
    assert recall["unused_packs"] == ["BAD-2"]
    assert (await client.get(f"{API}/cycles/{good_cycle}/recall", headers=ctx["auth"])).json()["items"] == []
    states = {row["code"]: row["state"] for row in (await client.get(f"{API}/packs", headers=ctx["auth"])).json()}
    assert states == {"BAD-1": "used", "BAD-2": "compromised", "GOOD-1": "used"}
    appointment_refused = await client.post(f"{API}/packs/{unused_pack['id']}/use", headers=ctx["auth"], json={"appointment_id": other_appointment})
    assert appointment_refused.status_code == 409 and "compromis" in appointment_refused.json()["detail"].lower()
    csv = await client.get(f"{API}/cycles/{bad_cycle}/recall.csv", headers=ctx["auth"])
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert len(lines) == 2 and "BAD-1" in lines[1] and "Altul" not in csv.text
    # recording is append-only: the earlier approval stays, a second recall record is not created
    again = await client.post(f"{API}/cycles/{bad_cycle}/indicators", headers=assistant, json={"kind": "biological", "result": "fail"})
    assert again.status_code in (200, 201)
    recall_again = (await client.get(f"{API}/cycles/{bad_cycle}/recall", headers=ctx["auth"])).json()
    assert len(recall_again["items"]) == 1


@pytest.mark.asyncio
async def test_good_biological_result_later_does_not_compromise(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle_id = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    ok = await client.post(f"{API}/cycles/{cycle_id}/indicators", headers=assistant, json={"kind": "biological", "result": "pass"})
    assert ok.status_code == 201
    cycle = (await client.get(f"{API}/cycles/{cycle_id}", headers=ctx["auth"])).json()
    assert cycle["compromised"] is False and cycle["biological_result"] == "pass"


@pytest.mark.asyncio
async def test_reverse_traceability_from_appointment_and_patient(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    pack = await add_pack(client, ctx["auth"], set_type["id"], "TRACE-1")
    cycle_id = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    appointment_id = await add_appointment(client, ctx)
    procedure = (await client.post("/api/procedures", headers=ctx["auth"], json={
        "code": f"ST-{uuid4().hex[:6]}", "name": "Detartraj", "base_price": "10.00", "default_duration_minutes": 30})).json()
    performed = (await client.post("/api/performed-procedures", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "procedure_catalog_id": procedure["id"]})).json()
    used = await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={
        "appointment_id": appointment_id, "performed_procedure_id": performed["id"]})
    assert used.status_code == 201, used.text
    by_appointment = (await client.get(f"{API}/trace/appointments/{appointment_id}", headers=ctx["auth"])).json()
    assert len(by_appointment) == 1
    row = by_appointment[0]
    assert row["pack_code"] == "TRACE-1" and row["cycle_id"] == cycle_id and row["equipment_name"] == "Autoclav fictiv"
    assert row["cycle_number"] == 1 and row["procedure_name"] == "Detartraj" and row["cycle_status"] == "approved"
    by_patient = (await client.get(f"{API}/trace/patients/{ctx['patient_id']}", headers=ctx["auth"])).json()
    assert [item["pack_code"] for item in by_patient] == ["TRACE-1"]
    # a procedure of another appointment cannot be linked
    second_appointment = await add_appointment(client, ctx, "2026-12-09T09:00:00+00:00")
    other_pack = await add_pack(client, ctx["auth"], set_type["id"])
    await approved_cycle(client, ctx, assistant, equipment_id, [other_pack["id"]])
    mismatch = await client.post(f"{API}/packs/{other_pack['id']}/use", headers=ctx["auth"], json={
        "appointment_id": second_appointment, "performed_procedure_id": performed["id"]})
    assert mismatch.status_code == 404


@pytest.mark.asyncio
async def test_roles_reception_blocked_doctor_reads_and_uses_only_own(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    reception, _ = await add_user(sessions, ctx["me"]["clinic_id"], "reception")
    doctor, doctor_id = await add_user(sessions, ctx["me"]["clinic_id"], "doctor")
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle_id = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    appointment_id = await add_appointment(client, ctx)  # the manager is the doctor of the visit, not `doctor`
    for path in ("/set-types", "/packs", "/cycles", f"/cycles/{cycle_id}", f"/cycles/{cycle_id}/recall",
                 f"/cycles/{cycle_id}/recall.csv", f"/trace/appointments/{appointment_id}", f"/trace/patients/{ctx['patient_id']}"):
        assert (await client.get(f"{API}{path}", headers=reception)).status_code == 403, path
    assert (await client.post(f"{API}/packs/{pack['id']}/use", headers=reception, json={"appointment_id": appointment_id})).status_code == 403
    for path in ("/set-types", "/packs", "/cycles", f"/cycles/{cycle_id}", f"/cycles/{cycle_id}/recall"):
        assert (await client.get(f"{API}{path}", headers=doctor)).status_code == 200, path
    # doctor writes: no set types, packs, cycles, decisions, indicators
    assert (await client.post(f"{API}/set-types", headers=doctor, json={"name": "X"})).status_code == 403
    assert (await client.post(f"{API}/packs", headers=doctor, json={"set_type_id": set_type["id"], "code": "Z"})).status_code == 403
    assert (await start_cycle(client, doctor, equipment_id, [pack["id"]])).status_code == 403
    assert (await client.post(f"{API}/cycles/{cycle_id}/indicators", headers=doctor, json={"kind": "biological", "result": "pass"})).status_code == 403
    # doctor cannot link to a visit that is not theirs, can for their own
    foreign_use = await client.post(f"{API}/packs/{pack['id']}/use", headers=doctor, json={"appointment_id": appointment_id})
    assert foreign_use.status_code == 403
    own_appointment = (await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": ctx["patient_id"], "doctor_id": doctor_id, "room_id": ctx["room_id"],
        "starts_at": "2026-12-05T09:00:00+00:00", "duration_minutes": 30, "appointment_type": "Control"})).json()["id"]
    own = await client.post(f"{API}/packs/{pack['id']}/use", headers=doctor, json={"appointment_id": own_appointment})
    assert own.status_code == 201, own.text


@pytest.mark.asyncio
async def test_other_clinic_gets_404_everywhere(env):
    client, sessions, _ = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    foreign = await register(client)
    pack = await add_pack(client, ctx["auth"], set_type["id"])
    cycle_id = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
    appointment_id = await add_appointment(client, ctx)
    for path in (f"/cycles/{cycle_id}", f"/cycles/{cycle_id}/recall", f"/cycles/{cycle_id}/recall.csv", f"/packs/{pack['id']}/history",
                 f"/trace/appointments/{appointment_id}", f"/trace/patients/{ctx['patient_id']}"):
        assert (await client.get(f"{API}{path}", headers=foreign["auth"])).status_code == 404, path
    assert (await client.post(f"{API}/packs/{pack['id']}/use", headers=foreign["auth"], json={"appointment_id": appointment_id})).status_code == 404
    assert (await decide(client, foreign["auth"], cycle_id)).status_code == 404
    assert (await finish_cycle(client, foreign["auth"], cycle_id)).status_code == 404
    assert (await client.post(f"{API}/cycles/{cycle_id}/indicators", headers=foreign["auth"], json={"kind": "biological", "result": "fail"})).status_code == 404
    foreign_set = await client.post(f"{API}/packs", headers=foreign["auth"], json={"set_type_id": set_type["id"], "code": "X"})
    assert foreign_set.status_code == 404
    foreign_cycle = await start_cycle(client, foreign["auth"], equipment_id, [pack["id"]])
    assert foreign_cycle.status_code == 404
    assert (await client.get(f"{API}/cycles", headers=foreign["auth"])).json() == []
    assert (await client.get(f"{API}/packs", headers=foreign["auth"])).json() == []


@pytest.mark.asyncio
async def test_list_endpoints_use_constant_query_count(env):
    client, sessions, engine = env
    ctx, assistant, _, equipment_id, set_type = await setup_world(client, sessions)
    appointment_id = await add_appointment(client, ctx)

    async def seed(count):
        for _ in range(count):
            pack = await add_pack(client, ctx["auth"], set_type["id"])
            cycle_id = await approved_cycle(client, ctx, assistant, equipment_id, [pack["id"]])
            await client.post(f"{API}/packs/{pack['id']}/use", headers=ctx["auth"], json={"appointment_id": appointment_id})
        return cycle_id

    async def count_queries(path):
        statements = []

        def before(conn, cursor, statement, *_):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", before)
        try:
            response = await client.get(f"{API}{path}", headers=ctx["auth"])
            assert response.status_code == 200, path
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before)
        return len(statements)

    cycle_id = await seed(1)
    paths = ["/packs", "/cycles", f"/cycles/{cycle_id}/recall", f"/trace/appointments/{appointment_id}", f"/trace/patients/{ctx['patient_id']}"]
    small = {path: await count_queries(path) for path in paths}
    await seed(5)
    large = {path: await count_queries(path) for path in paths}
    assert large == small, (small, large)
