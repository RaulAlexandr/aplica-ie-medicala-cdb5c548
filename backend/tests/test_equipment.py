from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.equipment.models import EquipmentUnavailability
from app.main import app


@pytest.fixture
async def ctx():
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
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def register(client, email):
    response = await client.post("/api/auth/register", json={"clinic_name": "Clinică fictivă", "full_name": "Manager Fictiv", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_outage_reports_overlapping_active_appointments_read_only_and_clinic_isolation(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-manager@example.com")
    foreign = await register(client, "equipment-foreign@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet fictiv"})).json()
    patient = (await client.post("/api/patients", headers=manager, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = await client.post("/api/equipment", headers=manager, json={
        "name": "Unit fictiv", "equipment_type": "unit dentar", "room_id": room["id"], "responsible_id": identity["id"],
    })
    assert equipment.status_code == 201
    start = datetime.now(UTC) + timedelta(days=3)
    async with sessions() as session:
        for offset, status in ((5, "scheduled"), (10, "scheduled"), (8, "cancelled")):
            appt_start = start + timedelta(minutes=offset)
            session.add(Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"],
                room_id=room["id"], starts_at=appt_start, ends_at=appt_start + timedelta(minutes=10), duration_minutes=10,
                status=status, appointment_type="Control fictiv"))
        await session.commit()
    result = await client.post(f"/api/equipment/{equipment.json()['id']}/unavailabilities", headers=manager, json={
        "reason": "Defecțiune fictivă", "starts_at": start.isoformat(), "ends_at": (start + timedelta(minutes=20)).isoformat(),
    })
    assert result.status_code == 201
    assert len(result.json()["affected_appointments"]) == 2
    assert all("patient" in appt and "doctor" in appt and "responsible" in appt for appt in result.json()["affected_appointments"])
    assert (await client.get(f"/api/equipment/{equipment.json()['id']}/history", headers=foreign)).status_code == 404
    async with sessions() as session:
        existing = list(await session.scalars(select(Appointment).order_by(Appointment.starts_at)))
        assert len(existing) == 3
        assert len(list(await session.scalars(select(EquipmentUnavailability)))) == 1


@pytest.mark.asyncio
async def test_doctor_and_assistant_can_report_but_cannot_administer(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-manager-roles@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet roluri"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit", "equipment_type": "unit", "room_id": room["id"], "responsible_id": identity["id"]})).json()
    async with sessions() as session:
        member = await session.get(User, identity["id"])
        member.role = "assistant"
        await session.commit()
    assistant = manager
    report = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=assistant, json={
        "reason": "Raport fictiv", "starts_at": "2030-04-01T10:00:00+03:00", "ends_at": "2030-04-01T11:00:00+03:00"})
    assert report.status_code == 201
    assert (await client.post("/api/equipment", headers=assistant, json={"name": "Nu", "equipment_type": "nu", "responsible_id": identity["id"]})).status_code == 403
    assert (await client.get("/api/equipment", headers=assistant)).status_code == 200


@pytest.mark.asyncio
async def test_validation_and_append_only_check_document_dependency_history(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-history@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet istoric"})).json()
    equipment = await client.post("/api/equipment", headers=manager, json={
        "name": "Sterilizator fictiv", "equipment_type": "sterilizator", "room_id": room["id"], "responsible_id": identity["id"]})
    assert equipment.status_code == 201
    assert (await client.post("/api/equipment", headers=manager, json={"name": "Invalid", "equipment_type": "unit", "state": "unknown", "responsible_id": identity["id"]})).status_code == 422
    equipment_id = equipment.json()["id"]
    assert (await client.post(f"/api/equipment/{equipment_id}/unavailabilities", headers=manager, json={
        "reason": "interval invalid", "starts_at": "2030-01-01T10:00:00Z", "ends_at": "2030-01-01T10:00:00Z"})).status_code == 422
    check = await client.post(f"/api/equipment/{equipment_id}/checks", headers=manager, json={
        "check_type": "inspection", "due_at": "2030-04-01T08:00:00Z", "completed_at": "2030-03-31T08:00:00Z", "result": "Conform fictiv"})
    document = await client.post(f"/api/equipment/{equipment_id}/documents", headers=manager, json={
        "title": "Certificat fictiv", "document_type": "certificate", "reference": "urn:fake:certificate:21"})
    dependency = await client.post(f"/api/equipment/{equipment_id}/dependencies", headers=manager, json={"room_id": room["id"]})
    assert check.status_code == document.status_code == dependency.status_code == 201
    history = await client.get(f"/api/equipment/{equipment_id}/history", headers=manager)
    assert history.status_code == 200
    assert history.json()["checks"][0]["author_id"] == identity["id"]
    assert len(history.json()["checks"]) == len(history.json()["documents"]) == len(history.json()["dependencies"]) == 1


@pytest.mark.asyncio
async def test_closed_outage_truncates_affected_appointments_in_history_and_affected_route(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-closed-outage@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet închidere"})).json()
    patient = (await client.post("/api/patients", headers=manager, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit închis", "equipment_type": "unit", "room_id": room["id"], "responsible_id": identity["id"]})).json()
    now = datetime.now(UTC).replace(microsecond=0)
    appt_start = now + timedelta(hours=1)
    async with sessions() as session:
        session.add(Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"],
            room_id=room["id"], starts_at=appt_start, ends_at=appt_start + timedelta(minutes=30), duration_minutes=30,
            status="scheduled", appointment_type="Control fictiv"))
        await session.commit()
    created = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
        "reason": "Test închidere fictivă", "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(hours=8)).isoformat()})
    assert created.status_code == 201 and len(created.json()["affected_appointments"]) == 1
    outage_id = created.json()["id"]
    closed = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities/{outage_id}/close", headers=manager)
    assert closed.status_code == 200
    affected = await client.get(f"/api/equipment/{equipment['id']}/unavailabilities/{outage_id}/affected", headers=manager)
    history = await client.get(f"/api/equipment/{equipment['id']}/history", headers=manager)
    assert affected.status_code == 200 and affected.json() == []
    assert history.status_code == 200 and history.json()["unavailabilities"][0]["affected_appointments"] == []


@pytest.mark.asyncio
async def test_existing_check_can_be_completed_once_and_leaves_due_queue(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-check-complete@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Autoclav completare", "equipment_type": "autoclav", "responsible_id": identity["id"]})).json()
    due_at = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    check = await client.post(f"/api/equipment/{equipment['id']}/checks", headers=manager, json={"check_type": "inspection", "due_at": due_at.isoformat()})
    assert check.status_code == 201
    check_id = check.json()["id"]
    assert any(row["id"] == check_id for row in (await client.get("/api/equipment/checks/due", headers=manager)).json())
    completed = await client.post(f"/api/equipment/{equipment['id']}/checks/{check_id}/complete", headers=manager, json={"result": "Conform fictiv"})
    assert completed.status_code == 200
    assert completed.json()["due_at"] == due_at.isoformat()
    assert completed.json()["completed_by_id"] == identity["id"] and completed.json()["completed_at"]
    assert completed.json()["result"] == "Conform fictiv"
    assert all(row["id"] != check_id for row in (await client.get("/api/equipment/checks/due", headers=manager)).json())
    again = await client.post(f"/api/equipment/{equipment['id']}/checks/{check_id}/complete", headers=manager, json={"result": "Alt rezultat"})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_service_dependency_matches_normalized_text_and_procedure_links(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-service-match@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet detartraj"})).json()
    patient = (await client.post("/api/patients", headers=manager, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit detartraj", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    now = datetime.now(UTC).replace(microsecond=0)
    async with sessions() as session:
        service = ProcedureCatalog(clinic_id=identity["clinic_id"], code="DET", code_lower="det", name="Detartraj",
            default_duration_minutes=30, base_price=Decimal("100.00"), currency="RON", is_active=True)
        session.add(service)
        await session.flush()
        appointment_rows = []
        labels = ("Detartraj", "detartraj", "Detartraj complet", "Detártraj complet", "Intervenție efectuată", "Control planificat")
        for index, label in enumerate(labels):
            appointment = Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"],
                room_id=room["id"], starts_at=now + timedelta(minutes=10 + index), ends_at=now + timedelta(minutes=40 + index),
                duration_minutes=30, status="scheduled", appointment_type=label)
            session.add(appointment)
            appointment_rows.append(appointment)
        await session.flush()
        performed_link = appointment_rows[4]
        session.add(PerformedProcedure(clinic_id=identity["clinic_id"], appointment_id=performed_link.id, patient_id=patient["id"],
            procedure_catalog_id=service.id, executed_by_id=identity["id"], procedure_code=service.code,
            procedure_name=service.name, procedure_base_price=service.base_price, procedure_currency="RON",
            procedure_default_duration=30, status="pending"))
        plan = TreatmentPlan(clinic_id=identity["clinic_id"], patient_id=patient["id"], title="Plan fictiv",
            status="draft", version=1, created_by_id=identity["id"])
        session.add(plan)
        await session.flush()
        stage = TreatmentPlanStage(clinic_id=identity["clinic_id"], plan_id=plan.id, position=0, title="Etapă fictivă")
        session.add(stage)
        await session.flush()
        planned_link = appointment_rows[5]
        session.add(TreatmentPlanItem(clinic_id=identity["clinic_id"], plan_id=plan.id, stage_id=stage.id,
            position=0, procedure_catalog_id=service.id, procedure_code=service.code, procedure_name=service.name,
            currency="RON", estimated_price=Decimal("100.00"), estimated_duration_minutes=30,
            status="active", appointment_id=planned_link.id))
        await session.commit()
        procedure_id = str(service.id)
        expected_ids = {str(row.id) for row in appointment_rows}
    dependency = await client.post(f"/api/equipment/{equipment['id']}/dependencies", headers=manager, json={"procedure_id": procedure_id})
    assert dependency.status_code == 201
    outage = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
        "reason": "Defecțiune detartraj", "starts_at": (now + timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat()})
    assert outage.status_code == 201
    affected = {row["appointment_id"]: row["match"] for row in outage.json()["affected_appointments"]}
    assert set(affected) == expected_ids
    assert all(affected[str(row.id)] == "posibil" for row in appointment_rows[:4])
    assert affected[str(appointment_rows[4].id)] == affected[str(appointment_rows[5].id)] == "sigur"


@pytest.mark.asyncio
async def test_duplicate_equipment_dependency_is_conflict(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-duplicate-dependency@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet dependență unică"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit unic", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    path = f"/api/equipment/{equipment['id']}/dependencies"
    first = await client.post(path, headers=manager, json={"room_id": room["id"]})
    duplicate = await client.post(path, headers=manager, json={"room_id": room["id"]})
    assert first.status_code == 201
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_broken_or_maintenance_state_requires_open_unavailability(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-state-outage@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit stare defectă", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    payload = {key: equipment.get(key) for key in ("name", "equipment_type", "serial_number", "room_id", "responsible_id", "is_active")}
    changed = await client.patch(f"/api/equipment/{equipment['id']}", headers=manager, json={**payload, "state": "broken"})
    assert changed.status_code == 200
    assert changed.json()["unavailability_required"] is True
    assert changed.json()["open_unavailability_id"] is None
    row = next(item for item in (await client.get("/api/equipment", headers=manager)).json() if item["id"] == equipment["id"])
    assert row["missing_unavailability"] is True


@pytest.mark.asyncio
async def test_expired_and_future_open_outages_do_not_satisfy_broken_state(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-outage-current-window@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit fereastră", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    now = datetime.now(UTC).replace(microsecond=0)
    payload = {key: equipment.get(key) for key in ("name", "equipment_type", "serial_number", "room_id", "responsible_id", "is_active")}
    expired = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
        "reason": "Expirată fictiv", "starts_at": (now - timedelta(days=2)).isoformat(), "ends_at": (now - timedelta(days=1)).isoformat()})
    assert expired.status_code == 201
    changed = await client.patch(f"/api/equipment/{equipment['id']}", headers=manager, json={**payload, "state": "broken"})
    assert changed.status_code == 200 and changed.json()["unavailability_required"] is True
    assert changed.json()["open_unavailability_id"] is None
    listed = await client.get("/api/equipment", headers=manager)
    assert next(row for row in listed.json() if row["id"] == equipment["id"])["missing_unavailability"] is True
    future = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
        "reason": "Viitoare fictivă", "starts_at": (now + timedelta(days=20)).isoformat(), "ends_at": (now + timedelta(days=21)).isoformat()})
    assert future.status_code == 201
    changed = await client.patch(f"/api/equipment/{equipment['id']}", headers=manager, json={**payload, "state": "maintenance"})
    assert changed.status_code == 200 and changed.json()["unavailability_required"] is True
    assert changed.json()["open_unavailability_id"] is None
    listed = await client.get("/api/equipment", headers=manager)
    assert next(row for row in listed.json() if row["id"] == equipment["id"])["missing_unavailability"] is True


@pytest.mark.asyncio
async def test_outage_closed_before_start_has_no_affected_appointments(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-outage-closed-before-start@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet anulare viitoare"})).json()
    patient = (await client.post("/api/patients", headers=manager, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit oprit înainte", "equipment_type": "unit", "room_id": room["id"], "responsible_id": identity["id"]})).json()
    now = datetime.now(UTC).replace(microsecond=0)
    appt_start = now - timedelta(minutes=10)
    async with sessions() as session:
        session.add(Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"],
            room_id=room["id"], starts_at=appt_start, ends_at=now + timedelta(minutes=50), duration_minutes=60,
            status="scheduled", appointment_type="Control fictiv"))
        await session.commit()
    starts = now + timedelta(minutes=20)
    ends = now + timedelta(hours=2)
    created = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
        "reason": "Închisă înainte de început", "starts_at": starts.isoformat(), "ends_at": ends.isoformat()})
    assert created.status_code == 201 and len(created.json()["affected_appointments"]) == 1
    outage_id = created.json()["id"]
    closed = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities/{outage_id}/close", headers=manager)
    assert closed.status_code == 200
    affected = await client.get(f"/api/equipment/{equipment['id']}/unavailabilities/{outage_id}/affected", headers=manager)
    history = await client.get(f"/api/equipment/{equipment['id']}/history", headers=manager)
    assert affected.status_code == 200 and affected.json() == []
    assert history.status_code == 200 and history.json()["unavailabilities"][0]["affected_appointments"] == []
