from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.appointment_procedures.models import AppointmentProcedure
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
from app.equipment.models import EquipmentDependency, EquipmentUnavailability
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
        "check_type": "inspection", "due_at": "2030-04-01T08:00:00Z",
        "completed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "result": "Conform fictiv"})
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
async def test_service_dependency_uses_effective_procedures_for_overlapping_appointments(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-service-match@example.com")
    foreign = await register(client, "equipment-service-foreign@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    foreign_identity = (await client.get("/api/auth/me", headers=foreign)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet detartraj"})).json()
    combined_room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet combinat"})).json()
    foreign_room = (await client.post("/api/rooms", headers=foreign, json={"name": "Cabinet străin"})).json()
    patient = (await client.post("/api/patients", headers=manager, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    foreign_patient = (await client.post("/api/patients", headers=foreign, json={"first_name": "Pacient", "last_name": "Străin"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit detartraj", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    now = datetime.now(UTC).replace(microsecond=0)
    outage_start = now + timedelta(days=3)
    async with sessions() as session:
        service = ProcedureCatalog(clinic_id=identity["clinic_id"], code="DET", code_lower="det", name="Detartraj",
            default_duration_minutes=30, base_price=Decimal("100.00"), currency="RON", is_active=True)
        other_service = ProcedureCatalog(clinic_id=identity["clinic_id"], code="CONS", code_lower="cons", name="Consultație",
            default_duration_minutes=30, base_price=Decimal("80.00"), currency="RON", is_active=True)
        session.add_all([service, other_service])
        await session.flush()
        cases = {
            "manual": ("Control fără serviciu în titlu", room["id"]),
            "accepted": ("Plan acceptat fără serviciu în titlu", room["id"]),
            "in_progress": ("Plan în curs fără serviciu în titlu", room["id"]),
            "draft": ("Detartraj planificat", room["id"]),
            "cancelled_plan": ("Detartraj din plan anulat", room["id"]),
            "performed": ("Manoperă efectuată", room["id"]),
            "cancelled_performed": ("Detartraj cu manoperă anulată", room["id"]),
            "text_only": ("Detartraj sugerat", room["id"]),
            "other_procedure": ("Detartraj și altă procedură", room["id"]),
            "combined": ("Vizită în cabinetul echipamentului", combined_room["id"]),
            "outside": ("Detartraj în afara intervalului", room["id"]),
        }
        appointments = {}
        for index, (key, (label, room_id)) in enumerate(cases.items()):
            starts_at = outage_start + (timedelta(hours=3) if key == "outside" else timedelta(minutes=10 + index))
            appointment = Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"],
                room_id=room_id, starts_at=starts_at, ends_at=starts_at + timedelta(minutes=30),
                duration_minutes=30, status="scheduled", appointment_type=label)
            appointments[key] = appointment
            session.add(appointment)
        foreign_appointment = Appointment(clinic_id=foreign_identity["clinic_id"], patient_id=foreign_patient["id"],
            doctor_id=foreign_identity["id"], room_id=foreign_room["id"], starts_at=outage_start + timedelta(minutes=20),
            ends_at=outage_start + timedelta(minutes=50), duration_minutes=30, status="scheduled",
            appointment_type="Detartraj în altă clinică")
        session.add(foreign_appointment)
        await session.flush()
        for key in ("manual", "combined"):
            session.add(AppointmentProcedure(clinic_id=identity["clinic_id"], appointment_id=appointments[key].id,
                procedure_catalog_id=service.id, quantity=1, added_by_id=identity["id"]))
        session.add(AppointmentProcedure(clinic_id=identity["clinic_id"], appointment_id=appointments["other_procedure"].id,
            procedure_catalog_id=other_service.id, quantity=1, added_by_id=identity["id"]))
        for key, status in (("performed", "pending"), ("cancelled_performed", "cancelled")):
            session.add(PerformedProcedure(clinic_id=identity["clinic_id"], appointment_id=appointments[key].id,
                patient_id=patient["id"], procedure_catalog_id=service.id, executed_by_id=identity["id"],
                procedure_code=service.code, procedure_name=service.name, procedure_base_price=service.base_price,
                procedure_currency="RON", procedure_default_duration=30, status=status))
        for key, status in (("accepted", "accepted"), ("in_progress", "in_progress"),
                            ("draft", "draft"), ("cancelled_plan", "cancelled")):
            plan = TreatmentPlan(clinic_id=identity["clinic_id"], patient_id=patient["id"], title=f"Plan {key}",
                status=status, version=1, accepted_version=1 if status in {"accepted", "in_progress"} else None,
                created_by_id=identity["id"])
            session.add(plan)
            await session.flush()
            stage = TreatmentPlanStage(clinic_id=identity["clinic_id"], plan_id=plan.id, position=0, title=f"Etapă {key}")
            session.add(stage)
            await session.flush()
            session.add(TreatmentPlanItem(clinic_id=identity["clinic_id"], plan_id=plan.id, stage_id=stage.id,
                position=0, procedure_catalog_id=service.id, procedure_code=service.code, procedure_name=service.name,
                currency="RON", estimated_price=Decimal("100.00"), estimated_duration_minutes=30,
                status="active", appointment_id=appointments[key].id))
        await session.commit()
        procedure_id = str(service.id)
    dependency = await client.post(f"/api/equipment/{equipment['id']}/dependencies", headers=manager, json={"procedure_id": procedure_id})
    assert dependency.status_code == 201
    room_dependency = await client.post(f"/api/equipment/{equipment['id']}/dependencies", headers=manager, json={"room_id": combined_room["id"]})
    assert room_dependency.status_code == 201
    statements = []
    engine = sessions.kw["bind"].sync_engine
    def record_select(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", record_select)
    try:
        outage = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=manager, json={
            "reason": "Defecțiune detartraj", "starts_at": outage_start.isoformat(),
            "ends_at": (outage_start + timedelta(hours=2)).isoformat()})
    finally:
        event.remove(engine, "before_cursor_execute", record_select)
    assert outage.status_code == 201
    affected_rows = outage.json()["affected_appointments"]
    affected = {row["appointment_id"]: row["match"] for row in affected_rows}
    expected = {
        "manual": "sigur", "accepted": "sigur", "in_progress": "sigur",
        "draft": "posibil", "cancelled_plan": "posibil", "performed": "sigur",
        "cancelled_performed": "posibil", "text_only": "posibil", "combined": "sigur",
    }
    expected_ids = {str(appointments[key].id): match for key, match in expected.items()}
    assert affected == expected_ids
    assert len(affected_rows) == len({row["appointment_id"] for row in affected_rows})
    assert sum("from appointments " in statement for statement in statements) == 1
    assert sum("from appointment_procedures " in statement for statement in statements) == 1
    assert sum("from treatment_plan_items " in statement for statement in statements) == 1
    assert sum("from performed_procedures " in statement for statement in statements) == 1


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


@pytest.mark.asyncio
async def test_dependency_requires_exactly_one_target_and_allows_separate_service_links(ctx):
    client, sessions = ctx
    manager = await register(client, "equipment-one-target@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet țintă unică"})).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit țintă unică", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    async with sessions() as session:
        services = [ProcedureCatalog(clinic_id=identity["clinic_id"], code=code, code_lower=code.lower(), name=name,
            default_duration_minutes=30, base_price=Decimal("100.00"), currency="RON", is_active=True)
            for code, name in (("SVC1", "Serviciu unu"), ("SVC2", "Serviciu doi"))]
        session.add_all(services)
        await session.commit()
        service_ids = [str(service.id) for service in services]
    path = f"/api/equipment/{equipment['id']}/dependencies"
    both_targets = await client.post(path, headers=manager, json={"room_id": room["id"], "procedure_id": service_ids[0]})
    no_target = await client.post(path, headers=manager, json={})
    assert both_targets.status_code == no_target.status_code == 422
    assert both_targets.json()["detail"] == "Alege cabinetul sau serviciul, nu ambele"
    assert no_target.json()["detail"] == "Alege cabinetul sau serviciul, nu ambele"
    assert (await client.post(path, headers=manager, json={"room_id": room["id"]})).status_code == 201
    assert (await client.post(path, headers=manager, json={"procedure_id": service_ids[0]})).status_code == 201
    assert (await client.post(path, headers=manager, json={"procedure_id": service_ids[1]})).status_code == 201
    async with sessions() as session:
        session.add(EquipmentDependency(clinic_id=identity["clinic_id"], equipment_id=equipment["id"],
            room_id=room["id"], procedure_id=service_ids[0], created_by_id=identity["id"]))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_completed_check_rejects_future_time_and_missing_result(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-invalid-completion@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit verificare viitoare", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    path = f"/api/equipment/{equipment['id']}/checks"
    due_at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    future = await client.post(path, headers=manager, json={"check_type": "inspection", "due_at": due_at,
        "completed_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(), "result": "Conform fictiv"})
    missing_result = await client.post(path, headers=manager, json={"check_type": "inspection", "due_at": due_at,
        "completed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    assert future.status_code == 422
    assert missing_result.status_code == 422
    assert missing_result.json()["detail"] == "Rezultatul este obligatoriu pentru o verificare efectuată"


@pytest.mark.asyncio
async def test_check_result_requires_completion_time(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-result-without-completion@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit rezultat fără efectuare", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    response = await client.post(f"/api/equipment/{equipment['id']}/checks", headers=manager, json={
        "check_type": "inspection", "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(), "result": "Conform fictiv"})
    assert response.status_code == 422
    assert response.json()["detail"] == "Rezultatul se completează numai pentru o verificare efectuată"


@pytest.mark.asyncio
async def test_blank_check_result_returns_plain_romanian_validation_detail(ctx):
    client, _sessions = ctx
    manager = await register(client, "equipment-blank-result@example.com")
    identity = (await client.get("/api/auth/me", headers=manager)).json()
    equipment = (await client.post("/api/equipment", headers=manager, json={"name": "Unit rezultat gol", "equipment_type": "unit", "responsible_id": identity["id"]})).json()
    response = await client.post(f"/api/equipment/{equipment['id']}/checks", headers=manager, json={
        "check_type": "inspection", "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "completed_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "result": "   "})
    assert response.status_code == 422
    assert response.json()["detail"] == "Rezultatul nu poate fi gol"
