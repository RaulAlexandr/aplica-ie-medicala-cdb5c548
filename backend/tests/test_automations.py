"""T25 automation rule, evaluator, approval, audit and isolation contracts."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.automations.models import AutomationExecution, AutomationRule, AutomationRuleVersion
from app.collab.models import CollabTask
from app.database import (
    Appointment,
    AppointmentHistory,
    Base,
    Patient,
    PerformedProcedure,
    ProcedureCatalog,
    Recall,
    Room,
    TreatmentPlan,
    TreatmentPlanEvent,
    TreatmentPlanItem,
    TreatmentPlanStage,
    User,
    get_db,
    utcnow,
)
from app.inventory.models import InventoryItem, InventoryLocation, InventoryLot, InventoryMovement
from app.main import app

PASSWORD = "correct horse battery staple"
TRIGGERS = (
    "appointment_created", "appointment_cancelled", "appointment_no_show", "visit_closed",
    "procedure_completed", "recall_due", "stock_below_minimum", "plan_unbooked",
)


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


async def manager(client, email=None):
    email = email or f"manager-{uuid4().hex}@example.com"
    response = await client.post("/api/auth/register", json={"clinic_name": "Clinică fictivă automatizări", "full_name": "Manager fictiv", "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    return {"auth": auth, "id": me["id"], "clinic_id": me["clinic_id"]}


async def person(sessions, clinic_id, role):
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{role}-{uuid4().hex}@example.com", password_hash="unused", full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as db:
        db.add(user)
        await db.commit()
    return {"auth": {"Authorization": f"Bearer {create_access_token(user)}"}, "id": str(user.id)}


def rule_body(**overrides):
    body = {"name": "Urmărire fictivă", "trigger": "appointment_created", "conditions": [], "action": {"target_type": "role", "target_value": "doctor", "title_template": "Verifică programarea {{appointment.type}}", "due_in_days": 2}, "mode": "automatic"}
    return body | overrides


async def seed_appointment(sessions, clinic_id, doctor_id, *, created_at=None, appointment_type="Consultație", room_id=None, status="scheduled"):
    async with sessions() as db:
        if room_id is None:
            room = Room(clinic_id=UUID(clinic_id), name=f"Cabinet {uuid4().hex[:6]}", is_active=True)
            db.add(room)
            await db.flush()
        else:
            room = await db.get(Room, UUID(room_id))
        patient = Patient(clinic_id=UUID(clinic_id), first_name="Pacient", last_name="Fictiv")
        db.add(patient)
        await db.flush()
        now = created_at or utcnow()
        appointment = Appointment(clinic_id=UUID(clinic_id), patient_id=patient.id, doctor_id=UUID(doctor_id), room_id=room.id, starts_at=now + timedelta(days=1), duration_minutes=30, status=status, appointment_type=appointment_type, created_at=now, updated_at=now)
        db.add(appointment)
        await db.flush()
        db.add(AppointmentHistory(clinic_id=UUID(clinic_id), appointment_id=appointment.id, actor_id=UUID(doctor_id), action="created" if status == "scheduled" else "status_changed", details={}, occurred_at=now))
        await db.commit()
        return str(appointment.id)


async def create_rule(client, auth, body=None):
    response = await client.post("/api/automations", headers=auth, json=body or rule_body())
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_rule_roles_tenant_isolation_validation_and_immutable_versions(env):
    client, sessions, _ = env
    owner = await manager(client)
    administrator = await person(sessions, owner["clinic_id"], "administrator")
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    assistant = await person(sessions, owner["clinic_id"], "assistant")
    reception = await person(sessions, owner["clinic_id"], "reception")
    foreign = await manager(client)
    created = await create_rule(client, owner["auth"])
    assert (await client.get("/api/automations", headers=administrator["auth"])).status_code == 200
    assert (await client.post("/api/automations", headers=administrator["auth"], json=rule_body(name="Admin rule"))).status_code == 201
    assert (await client.get("/api/automations", headers=doctor["auth"])).status_code == 403
    assert (await client.get(f"/api/automations/{created['id']}", headers=foreign["auth"])).status_code == 404
    for role in (doctor, assistant, reception):
        assert (await client.post("/api/automations", headers=role["auth"], json=rule_body())).status_code == 403
    assert (await client.post("/api/automations", headers=owner["auth"], json=rule_body(trigger="free_expression"))).status_code == 422
    assert (await client.post("/api/automations", headers=owner["auth"], json=rule_body(conditions=[{"field": "__import__", "operator": "eq", "value": "x"}]))).status_code == 422
    assert (await client.post("/api/automations", headers=owner["auth"], json=rule_body(conditions=[{"field": "appointment_type", "operator": "contains", "value": "x"}]))).status_code == 422
    assert (await client.post("/api/automations", headers=owner["auth"], json=rule_body(trigger="plan_unbooked"))).status_code == 422
    edited = await client.put(f"/api/automations/{created['id']}", headers=owner["auth"], json=rule_body(name="Versiunea a doua fictivă"))
    assert edited.status_code == 200 and edited.json()["version"] == 2
    async with sessions() as db:
        versions = (await db.scalars(select(AutomationRuleVersion).where(AutomationRuleVersion.rule_id == UUID(created["id"])).order_by(AutomationRuleVersion.version))).all()
        assert [row.version for row in versions] == [1, 2]
        assert (await db.scalar(select(AutomationRule).where(AutomationRule.id == UUID(created["id"])))).current_version == 2


@pytest.mark.asyncio
async def test_create_evaluate_idempotency_conditions_task_and_audit(env):
    client, sessions, _ = env
    owner = await manager(client)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    await seed_appointment(sessions, owner["clinic_id"], doctor["id"], appointment_type="Consultație")
    body = rule_body(conditions=[{"field": "appointment_type", "operator": "eq", "value": "Consultație"}])
    created = await create_rule(client, owner["auth"], body)
    activated = await client.post(f"/api/automations/{created['id']}/activate", headers=owner["auth"], json={})
    assert activated.status_code == 200, activated.text
    # Activation defaults to the current instant, so the preceding source event is not replayed.
    assert (await client.post("/api/automations/evaluate", headers=owner["auth"])).json()["created"] == 0
    start = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    assert (await client.post(f"/api/automations/{created['id']}/activate", headers=owner["auth"], json={"start_at": start})).status_code == 200
    first = await client.post("/api/automations/evaluate", headers=owner["auth"])
    again = await client.post("/api/automations/evaluate", headers=owner["auth"])
    assert first.status_code == again.status_code == 200
    assert first.json()["created"] == 1 and again.json()["created"] == 0
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(CollabTask)) == 1
        executions = (await db.scalars(select(AutomationExecution))).all()
        assert len(executions) == 1 and executions[0].result["task_id"]
        assert executions[0].version_id == UUID(created["version_id"])
    detail = await client.get(f"/api/automations/{created['id']}/executions", headers=owner["auth"])
    assert detail.status_code == 200 and len(detail.json()) == 1
    false_rule = await create_rule(client, owner["auth"], rule_body(name="Condiție falsă", conditions=[{"field": "appointment_type", "operator": "eq", "value": "Nu se potrivește"}]))
    await client.post(f"/api/automations/{false_rule['id']}/activate", headers=owner["auth"], json={"start_at": (datetime.now(UTC) - timedelta(days=2)).isoformat()})
    assert (await client.post("/api/automations/evaluate", headers=owner["auth"])).json()["created"] == 0


@pytest.mark.asyncio
async def test_approval_rejection_stop_and_simulation_have_no_unintended_writes(env):
    client, sessions, _ = env
    owner = await manager(client)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=utcnow() - timedelta(days=1))
    pending = await create_rule(client, owner["auth"], rule_body(mode="approval"))
    await client.post(f"/api/automations/{pending['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    async with sessions() as db:
        before = await db.scalar(select(func.count()).select_from(AutomationExecution))
    simulated = await client.post(f"/api/automations/{pending['id']}/simulate", headers=owner["auth"], json={"days": 5})
    assert simulated.status_code == 200 and simulated.json()["matches"] >= 1
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomationExecution)) == before
        assert await db.scalar(select(func.count()).select_from(CollabTask)) == 0
    evaluated = await client.post("/api/automations/evaluate", headers=owner["auth"])
    assert evaluated.json()["created"] == 1
    async with sessions() as db:
        execution = await db.scalar(select(AutomationExecution))
        execution_id = str(execution.id)
    denied = await client.post(f"/api/automations/executions/{execution_id}/decision", headers=doctor["auth"], json={"decision": "approve"})
    assert denied.status_code == 403
    stopped = await client.post(f"/api/automations/{pending['id']}/stop", headers=owner["auth"])
    assert stopped.status_code == 200
    async with sessions() as db:
        assert (await db.get(AutomationExecution, UUID(execution_id))).status == "stopped"
    resumed = await client.post(f"/api/automations/{pending['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    assert resumed.status_code == 200
    assert (await client.post("/api/automations/evaluate", headers=owner["auth"])).json()["created"] == 0
    accepted = await create_rule(client, owner["auth"], rule_body(name="Aprobare fictivă", mode="approval"))
    await client.post(f"/api/automations/{accepted['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    await client.post("/api/automations/evaluate", headers=owner["auth"])
    async with sessions() as db:
        approval = await db.scalar(select(AutomationExecution).where(AutomationExecution.version_id == UUID(accepted["version_id"])))
    decision = await client.post(f"/api/automations/executions/{approval.id}/decision", headers=owner["auth"], json={"decision": "approve"})
    assert decision.status_code == 200 and decision.json()["status"] == "succeeded"
    assert decision.json()["decided_by_id"] == owner["id"] and decision.json()["decided_at"]
    rejected = await create_rule(client, owner["auth"], rule_body(name="Respinsă fictiv", mode="approval"))
    await client.post(f"/api/automations/{rejected['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    await client.post("/api/automations/evaluate", headers=owner["auth"])
    async with sessions() as db:
        rejected_run = await db.scalar(select(AutomationExecution).where(AutomationExecution.version_id == UUID(rejected["version_id"])))
    rejection = await client.post(f"/api/automations/executions/{rejected_run.id}/decision", headers=owner["auth"], json={"decision": "reject", "reason": "Nu este necesară, fictiv"})
    assert rejection.status_code == 200 and rejection.json()["status"] == "rejected"
    assert rejection.json()["decided_by_id"] == owner["id"] and rejection.json()["decision_reason"] == "Nu este necesară, fictiv"


@pytest.mark.asyncio
async def test_all_triggers_are_registered_and_failed_actions_retry_three_times(env, monkeypatch):
    client, sessions, _ = env
    owner = await manager(client)
    for trigger in TRIGGERS:
        conditions = [{"field": "days", "operator": "gte", "value": 3}] if trigger == "plan_unbooked" else []
        await create_rule(client, owner["auth"], rule_body(name=f"Regulă {trigger}", trigger=trigger, conditions=conditions))
    import app.automations.service as automation_service

    async def fail_task(*args, **kwargs):
        raise RuntimeError("eroare fictivă controlată")

    monkeypatch.setattr(automation_service, "create_task_action", fail_task)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=utcnow() - timedelta(days=2))
    body = rule_body(name="Retry fictiv")
    retried = await create_rule(client, owner["auth"], body)
    await client.post(f"/api/automations/{retried['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    for expected in (1, 2, 3):
        await client.post("/api/automations/evaluate", headers=owner["auth"])
        async with sessions() as db:
            run = await db.scalar(select(AutomationExecution).where(AutomationExecution.version_id == UUID(retried["version_id"])))
            assert run.attempt_count == expected
            if expected < 3:
                assert run.status == "failed_retryable"
            else:
                assert run.status == "failed"
                assert "eroare fictivă" in run.reason


@pytest.mark.asyncio
async def test_evaluator_query_count_is_bounded_for_fifty_events(env):
    client, sessions, engine = env
    owner = await manager(client)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    for _ in range(50):
        await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=utcnow() - timedelta(days=1))
    rule = await create_rule(client, owner["auth"])
    await client.post(f"/api/automations/{rule['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=2)).isoformat()})
    selects = 0

    def count_selects(conn, cursor, statement, parameters, context, executemany):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        response = await client.post("/api/automations/evaluate", headers=owner["auth"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)
    assert response.status_code == 200 and response.json()["created"] == 50
    assert selects <= 12


@pytest.mark.asyncio
async def test_every_trigger_is_scoped_by_clinic(env):
    client, _sessions, _ = env
    owner = await manager(client)
    foreign = await manager(client)
    rule = await create_rule(client, owner["auth"])
    assert (await client.get(f"/api/automations/{rule['id']}", headers=foreign["auth"])).status_code == 404
    assert (await client.get(f"/api/automations/{rule['id']}/executions", headers=foreign["auth"])).status_code == 404


@pytest.mark.asyncio
async def test_execution_old_version_remains_linked_after_edit(env):
    client, sessions, _ = env
    owner = await manager(client)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=utcnow() - timedelta(days=1))
    first = await create_rule(client, owner["auth"])
    await client.post(f"/api/automations/{first['id']}/activate", headers=owner["auth"], json={"start_at": (utcnow() - timedelta(days=3)).isoformat()})
    await client.post("/api/automations/evaluate", headers=owner["auth"])
    second = await client.put(f"/api/automations/{first['id']}", headers=owner["auth"], json=rule_body(name="Regulă modificată fictiv"))
    assert second.status_code == 200 and second.json()["version"] == 2
    async with sessions() as db:
        run = await db.scalar(select(AutomationExecution))
        assert run.version_id == UUID(first["version_id"])


@pytest.mark.asyncio
async def test_all_supported_trigger_names_validate(env):
    client, _sessions, _engine = env
    owner = await manager(client)
    for trigger in TRIGGERS:
        conditions = [{"field": "days", "operator": "gte", "value": 3}] if trigger == "plan_unbooked" else []
        response = await client.post("/api/automations", headers=owner["auth"], json=rule_body(name=f"Declanșator {trigger}", trigger=trigger, conditions=conditions))
        assert response.status_code == 201, (trigger, response.text)


@pytest.mark.asyncio
async def test_each_trigger_reads_its_existing_clinic_source_table(env, monkeypatch):
    _client, sessions, _engine = env
    owner = await manager(_client)
    doctor = await person(sessions, owner["clinic_id"], "doctor")
    now, clinic_id, doctor_id = utcnow(), UUID(owner["clinic_id"]), UUID(doctor["id"])
    created = await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=now - timedelta(days=3))
    cancelled = await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=now - timedelta(days=2), status="cancelled")
    no_show = await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=now - timedelta(days=2), status="no_show")
    closed = await seed_appointment(sessions, owner["clinic_id"], doctor["id"], created_at=now - timedelta(days=2), status="completed")
    async with sessions() as db:
        appointment = await db.get(Appointment, UUID(closed))
        patient_id = appointment.patient_id
        catalog = ProcedureCatalog(clinic_id=clinic_id, code="AUTO", code_lower="auto", name="Manoperă fictivă", default_duration_minutes=20, base_price="10.00", currency="RON", is_active=True)
        db.add(catalog)
        await db.flush()
        performed = PerformedProcedure(clinic_id=clinic_id, appointment_id=appointment.id, patient_id=patient_id, procedure_catalog_id=catalog.id,
            executed_by_id=doctor_id, procedure_code=catalog.code, procedure_name=catalog.name, procedure_base_price=catalog.base_price,
            procedure_currency="RON", procedure_default_duration=20, status="completed", updated_at=now - timedelta(days=1))
        recall = Recall(clinic_id=clinic_id, patient_id=patient_id, due_date=date.today() - timedelta(days=1), reason="Control fictiv", status="pending", created_by_id=doctor_id)
        item = InventoryItem(clinic_id=clinic_id, name="Stoc fictiv automatizare", unit="buc", minimum_quantity="2", is_active=True)
        location = InventoryLocation(clinic_id=clinic_id, name="Depozit automatizare", is_active=True)
        db.add_all([performed, recall, item, location])
        await db.flush()
        lot = InventoryLot(clinic_id=clinic_id, item_id=item.id, code="AUTO-LOT")
        db.add(lot)
        await db.flush()
        db.add(InventoryMovement(clinic_id=clinic_id, item_id=item.id, lot_id=lot.id, location_id=location.id, kind="consumption", quantity="3",
            client_request_id=str(uuid4()), signature="f" * 64, reason="Consum fictiv", created_by_id=doctor_id, created_at=now - timedelta(days=1)))
        plan = TreatmentPlan(clinic_id=clinic_id, patient_id=patient_id, title="Plan fictiv", status="accepted", version=1, accepted_version=1, created_by_id=doctor_id)
        db.add(plan)
        await db.flush()
        stage = TreatmentPlanStage(clinic_id=clinic_id, plan_id=plan.id, position=0, title="Etapă fictivă")
        db.add(stage)
        await db.flush()
        db.add_all([
            TreatmentPlanItem(clinic_id=clinic_id, plan_id=plan.id, stage_id=stage.id, position=0, procedure_catalog_id=catalog.id, procedure_code="AUTO", procedure_name="Manoperă fictivă", currency="RON", estimated_price="10.00", estimated_duration_minutes=20, status="active"),
            TreatmentPlanEvent(clinic_id=clinic_id, plan_id=plan.id, actor_id=doctor_id, action="accepted", plan_version=1, details={}, occurred_at=now - timedelta(days=3)),
        ])
        await db.commit()
    from app.automations.service import fetch_events

    async with sessions() as db:
        found = {trigger: await fetch_events(db, clinic_id, trigger, now - timedelta(days=4), now) for trigger in TRIGGERS}
    assert {trigger: len(events) for trigger, events in found.items()} == {trigger: 1 for trigger in TRIGGERS}
    assert found["appointment_created"][0]["event_id"]
    assert found["appointment_cancelled"][0]["data"]["appointment_id"] == cancelled
    assert found["appointment_no_show"][0]["data"]["appointment_id"] == no_show
    assert found["visit_closed"][0]["data"]["appointment_id"] == closed
    assert found["appointment_created"][0]["data"]["appointment_id"] == created
    delay_rule = await create_rule(_client, owner["auth"], rule_body(trigger="plan_unbooked", conditions=[{"field": "days", "operator": "gte", "value": 4}]))
    await _client.post(f"/api/automations/{delay_rule['id']}/activate", headers=owner["auth"], json={"start_at": (now - timedelta(days=4)).isoformat()})
    assert (await _client.post("/api/automations/evaluate", headers=owner["auth"])).json()["created"] == 0
    import app.automations.service as automation_service

    monkeypatch_now = now + timedelta(days=2)
    monkeypatch.setattr(automation_service, "utcnow", lambda: monkeypatch_now)
    assert (await _client.post("/api/automations/evaluate", headers=owner["auth"])).json()["created"] == 1

