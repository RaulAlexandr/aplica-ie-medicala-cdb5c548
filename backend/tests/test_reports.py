from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.config import settings
from app.database import (
    Appointment,
    Base,
    Clinic,
    Patient,
    PatientFinancialEntry,
    PerformedProcedure,
    ProcedureCatalog,
    ProcedureExecutionSegment,
    Room,
    TreatmentPlan,
    TreatmentPlanAcceptance,
    TreatmentPlanItem,
    TreatmentPlanStage,
    User,
    WorkingHours,
    get_db,
)
from app.inventory.models import InventoryItem, InventoryLocation, InventoryLot, InventoryMovement
from app.locations.models import ClinicLocation
from app.main import app
from app.materials.models import MaterialConsumptionBatch, MaterialConsumptionLine


def create_test_token(user_id: str, clinic_id: str, role: str) -> str:
    return jwt.encode({"sub": user_id, "clinic_id": clinic_id, "role": role,
                       "exp": datetime.now(UTC).timestamp() + 3600}, settings.jwt_secret, algorithm="HS256")


def auth_header(user_id, clinic_id, role):
    return {"Authorization": f"Bearer {create_test_token(str(user_id), str(clinic_id), role)}"}


@pytest.fixture
async def report_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        clinic = Clinic(name="Report Clinic", timezone="Europe/Bucharest")
        async with sessions() as session:
            session.add(clinic)
            await session.flush()
            principal = ClinicLocation(clinic_id=clinic.id, name="Principal")
            session.add(principal)
            await session.flush()
            manager = User(clinic_id=clinic.id, full_name="Report Manager", email="manager@reports.test",
                           password_hash=hash_password("password-123456"), role="clinic_manager", is_active=True)
            doctor = User(clinic_id=clinic.id, full_name="Report Doctor", email="doctor@reports.test",
                          password_hash=hash_password("password-123456"), role="doctor", is_active=True)
            assistant = User(clinic_id=clinic.id, full_name="Report Assistant", email="assistant@reports.test",
                             password_hash=hash_password("password-123456"), role="assistant", is_active=True)
            reception = User(clinic_id=clinic.id, full_name="Report Reception", email="reception@reports.test",
                             password_hash=hash_password("password-123456"), role="reception", is_active=True)
            administrator = User(clinic_id=clinic.id, full_name="Report Administrator", email="admin@reports.test",
                                 password_hash=hash_password("password-123456"), role="administrator", is_active=True)
            room = Room(clinic_id=clinic.id, location_id=principal.id, name="Room 1", is_active=True)
            patient = Patient(clinic_id=clinic.id, first_name="Ana", last_name="Test",
                              allergies="FICTIONAL allergy", notes="FICTIONAL private note")
            session.add_all([manager, doctor, assistant, reception, administrator, room, patient])
            await session.commit()
            ids = {"clinic": clinic.id, "manager": manager.id, "doctor": doctor.id,
                   "assistant": assistant.id, "reception": reception.id, "administrator": administrator.id,
                   "room": room.id, "location": principal.id, "patient": patient.id}
        headers = {"Authorization": f"Bearer {create_test_token(str(ids['manager']), str(ids['clinic']), 'clinic_manager')}"}
        yield client, sessions, ids, headers
    app.dependency_overrides.clear()
    await engine.dispose()


def at_local(iso: str) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso).replace(tzinfo=ZoneInfo("Europe/Bucharest")).astimezone(UTC)


@pytest.mark.asyncio
async def test_reports_count_records_and_local_day_boundaries(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        appointments = [
            Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                        room_id=ids["room"], starts_at=at_local("2026-10-25T23:59:59"), duration_minutes=30,
                        status="completed", appointment_type="Checkup"),
            Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                        room_id=ids["room"], starts_at=at_local("2026-10-25T23:00:00"), duration_minutes=30,
                        status="cancelled", appointment_type="Checkup"),
            Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                        room_id=ids["room"], starts_at=at_local("2026-10-25T10:00:00"), duration_minutes=30,
                        status="scheduled", appointment_type="Checkup"),
            Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                        room_id=ids["room"], starts_at=at_local("2026-10-25T11:00:00"), duration_minutes=30,
                        status="no_show", appointment_type="Checkup"),
            Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                        room_id=ids["room"], starts_at=at_local("2026-10-26T00:00:00"), duration_minutes=30,
                        status="no_show", appointment_type="Checkup"),
        ]
        session.add_all(appointments)
        room = await session.get(Room, ids["room"])
        room.is_active = False
        session.add(WorkingHours(clinic_id=ids["clinic"], day_of_week=6, start_time=time(3, 30), end_time=time(4), is_active=True))
        session.add(WorkingHours(clinic_id=ids["clinic"], day_of_week=6, start_time=time(9), end_time=time(11), is_active=True))
        await session.commit()

    res = await client.get("/api/reports/appointments_total?from=2026-10-25&to=2026-10-25", headers=headers)
    assert res.status_code == 200
    assert res.json()["value"] == 4
    assert res.json()["period"]["timezone"] == "Europe/Bucharest"
    records = await client.get("/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25", headers=headers)
    assert records.status_code == 200
    assert records.json()["total"] == 4
    assert {row["id"] for row in records.json()["records"]} == {str(a.id) for a in appointments[:4]}
    assert any(row["date"].startswith("2026-10-25T23:59:59") for row in records.json()["records"])
    assert all(set(row) <= {"id", "entity", "date", "status", "amount", "patient_name"} for row in records.json()["records"])
    first_page = await client.get("/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25&limit=2&offset=0", headers=headers)
    second_page = await client.get("/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25&limit=2&offset=2", headers=headers)
    assert first_page.json()["total"] == second_page.json()["total"] == res.json()["value"]
    assert len(first_page.json()["records"]) == len(second_page.json()["records"]) == 2
    assert {row["id"] for row in first_page.json()["records"] + second_page.json()["records"]} == {str(a.id) for a in appointments[:4]}
    canceled = await client.get("/api/reports/appointments_cancelled?from=2026-10-25&to=2026-10-25", headers=headers)
    assert canceled.json()["value"] == 1
    canceled_rows = await client.get("/api/reports/appointments_cancelled/records?from=2026-10-25&to=2026-10-25", headers=headers)
    assert [row["id"] for row in canceled_rows.json()["records"]] == [str(appointments[1].id)]
    assert canceled_rows.json()["records"][0]["status"] == "cancelled"
    no_show = await client.get("/api/reports/appointments_no_show?from=2026-10-25&to=2026-10-25", headers=headers)
    assert no_show.json()["value"] == 1
    no_show_rows = await client.get("/api/reports/appointments_no_show/records?from=2026-10-25&to=2026-10-25", headers=headers)
    assert [row["id"] for row in no_show_rows.json()["records"]] == [str(appointments[3].id)]
    completed = await client.get("/api/reports/appointments_completed?from=2026-10-25&to=2026-10-25", headers=headers)
    assert completed.json()["value"] == 1
    completed_rows = await client.get("/api/reports/appointments_completed/records?from=2026-10-25&to=2026-10-25", headers=headers)
    assert [row["id"] for row in completed_rows.json()["records"]] == [str(appointments[0].id)]
    cancellation_rate = await client.get("/api/reports/cancellation_rate?from=2026-10-25&to=2026-10-25", headers=headers)
    assert cancellation_rate.json()["value"] == 25.0
    assert cancellation_rate.json()["value"] == round(sum(row["status"] == "cancelled" for row in records.json()["records"]) * 100 / records.json()["total"], 2)
    no_show_rate = await client.get("/api/reports/no_show_rate?from=2026-10-25&to=2026-10-25", headers=headers)
    assert no_show_rate.json()["value"] == 25.0
    assert no_show_rate.json()["value"] == round(sum(row["status"] == "no_show" for row in records.json()["records"]) * 100 / records.json()["total"], 2)
    occupancy = await client.get(f"/api/reports/room_occupancy?from=2026-10-25&to=2026-10-25&room_id={ids['room']}", headers=headers)
    assert occupancy.json()["value"] == {"scheduled_minutes": 30, "available_minutes": 210, "occupancy_percent": 14.29}
    occupancy_rows = await client.get(f"/api/reports/room_occupancy/records?from=2026-10-25&to=2026-10-25&room_id={ids['room']}", headers=headers)
    assert occupancy_rows.json()["total"] == 3
    scheduled_rows = [row for row in occupancy_rows.json()["records"] if row["entity"] == "appointment"]
    schedule_rows = [row for row in occupancy_rows.json()["records"] if row["entity"] == "working_hours"]
    assert [row["id"] for row in scheduled_rows] == [str(appointments[2].id)]
    assert scheduled_rows[0]["status"] == "scheduled" and Decimal(scheduled_rows[0]["amount"]) == 30
    assert sum(int(row["amount"]) for row in schedule_rows) == occupancy.json()["value"]["available_minutes"]
    assert occupancy.json()["value"]["scheduled_minutes"] == sum(int(row["amount"]) for row in scheduled_rows)
    day_span = (at_local("2026-10-26T00:00:00") - at_local("2026-10-25T00:00:00")).total_seconds() / 3600
    assert day_span == 25
    next_day = await client.get("/api/reports/appointments_total?from=2026-10-26&to=2026-10-26", headers=headers)
    next_day_rows = await client.get("/api/reports/appointments_total/records?from=2026-10-26&to=2026-10-26", headers=headers)
    assert next_day.json()["value"] == next_day_rows.json()["total"] == 1
    assert next_day_rows.json()["records"][0]["id"] == str(appointments[4].id)


@pytest.mark.asyncio
async def test_report_auth_foreign_filters_invalid_period_and_unconfigured_room(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        foreign_clinic = Clinic(name="Foreign", timezone="Europe/Bucharest")
        session.add(foreign_clinic)
        await session.flush()
        foreign_principal = ClinicLocation(clinic_id=foreign_clinic.id, name="Principal")
        session.add(foreign_principal)
        await session.flush()
        foreign_room = Room(clinic_id=foreign_clinic.id, location_id=foreign_principal.id, name="Foreign room")
        foreign_doctor = User(clinic_id=foreign_clinic.id, full_name="Foreign Doctor", email="foreign@reports.test",
                              password_hash=hash_password("password-123456"), role="doctor", is_active=True)
        session.add_all([foreign_room, foreign_doctor])
        await session.commit()
        foreign_id = foreign_room.id
    wrong_filter = await client.get(f"/api/reports/appointments_total?from=2026-10-25&to=2026-10-25&room_id={foreign_id}", headers=headers)
    assert wrong_filter.status_code == 404
    wrong_room_records = await client.get(f"/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25&room_id={foreign_id}", headers=headers)
    assert wrong_room_records.status_code == 404
    wrong_doctor = await client.get(f"/api/reports/appointments_total?from=2026-10-25&to=2026-10-25&doctor_id={foreign_doctor.id}", headers=headers)
    assert wrong_doctor.status_code == 404
    wrong_doctor_records = await client.get(f"/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25&doctor_id={foreign_doctor.id}", headers=headers)
    assert wrong_doctor_records.status_code == 404
    invalid = await client.get("/api/reports/appointments_total?from=2025-01-01&to=2026-01-02", headers=headers)
    assert invalid.status_code == 422
    backwards = await client.get("/api/reports/appointments_total?from=2026-10-26&to=2026-10-25", headers=headers)
    assert backwards.status_code == 422
    one_day = await client.get("/api/reports/appointments_total?from=2026-10-25&to=2026-10-25", headers=headers)
    assert one_day.status_code == 200
    three_hundred_sixty_six = await client.get("/api/reports/appointments_total?from=2025-01-01&to=2026-01-01", headers=headers)
    assert three_hundred_sixty_six.status_code == 200
    three_hundred_sixty_seven_records = await client.get("/api/reports/appointments_total/records?from=2025-01-01&to=2026-01-02", headers=headers)
    assert three_hundred_sixty_seven_records.status_code == 422
    three_hundred_sixty_seven = await client.get("/api/reports/appointments_total?from=2025-01-01&to=2026-01-02", headers=headers)
    assert three_hundred_sixty_seven.status_code == 422
    backwards_records = await client.get("/api/reports/appointments_total/records?from=2026-10-26&to=2026-10-25", headers=headers)
    assert backwards_records.status_code == 422
    zero_rate = await client.get("/api/reports/cancellation_rate?from=2026-09-01&to=2026-09-01", headers=headers)
    assert zero_rate.status_code == 200
    assert zero_rate.json()["value"]["status"] == "undefined"
    assert zero_rate.json()["undefined_reason"] == "Nu există programări în perioadă."
    missing_program = await client.get(f"/api/reports/room_occupancy?from=2026-10-25&to=2026-10-25&room_id={ids['room']}", headers=headers)
    assert missing_program.status_code == 200
    assert missing_program.json()["value"]["status"] == "undefined"
    assert missing_program.json()["undefined_reason"]
    doctor_headers = {"Authorization": f"Bearer {create_test_token(str(ids['doctor']), str(ids['clinic']), 'doctor')}"}
    forbidden = await client.get("/api/reports/appointments_total?from=2026-10-25&to=2026-10-25", headers=doctor_headers)
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_every_report_endpoint_checks_all_roles_for_all_indicators(report_env):
    client, _sessions, ids, _manager_headers = report_env
    indicator_keys = (
        "appointments_total", "appointments_cancelled", "appointments_no_show", "appointments_completed",
        "cancellation_rate", "no_show_rate", "room_occupancy", "procedure_estimated_duration",
        "procedure_measured_duration", "procedure_manual_duration", "treatment_items_uncontinued",
        "services_performed", "receipts_net_by_method", "financial_reversals", "consumptions",
    )
    query = "from=2026-10-25&to=2026-10-25&room_id=" + str(ids["room"])
    for indicator in indicator_keys:
        for endpoint in (f"/api/reports/{indicator}?{query}", f"/api/reports/{indicator}/records?{query}"):
            assert (await client.get(endpoint)).status_code == 401, endpoint
            for role in ("clinic_manager", "administrator"):
                response = await client.get(endpoint, headers=auth_header(ids[role] if role == "administrator" else ids["manager"], ids["clinic"], role))
                assert response.status_code == 200, (endpoint, role, response.text)
            for role in ("doctor", "assistant", "reception"):
                response = await client.get(endpoint, headers=auth_header(ids[role], ids["clinic"], role))
                assert response.status_code == 403, (endpoint, role, response.text)


@pytest.mark.asyncio
async def test_reports_financial_service_duration_and_consumptions(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        appt = Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
                           room_id=ids["room"], starts_at=at_local("2026-10-25T10:00:00"),
                           duration_minutes=45, status="completed", appointment_type="Checkup")
        catalog = ProcedureCatalog(clinic_id=ids["clinic"], code="X", code_lower="x", name="Exam",
                                   default_duration_minutes=30, base_price=Decimal("90.00"), currency="RON")
        session.add_all([appt, catalog])
        await session.flush()
        procedure = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=appt.id,
                                       patient_id=ids["patient"], procedure_catalog_id=catalog.id,
                                       executed_by_id=ids["doctor"], procedure_code="X", procedure_name="Exam",
                                       procedure_base_price=Decimal("90.00"), procedure_currency="RON",
                                       procedure_default_duration=30, status="completed", current_price=Decimal("100.00"))
        manual_procedure = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=appt.id,
                                       patient_id=ids["patient"], procedure_catalog_id=catalog.id,
                                       executed_by_id=ids["doctor"], procedure_code="X2", procedure_name="Manual exam",
                                       procedure_base_price=Decimal("200.00"), procedure_currency="RON",
                                       procedure_default_duration=20, status="completed", current_price=None,
                                       notes="Manual duration override: 300s - test")
        cancelled_procedure = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=appt.id,
                                       patient_id=ids["patient"], procedure_catalog_id=catalog.id,
                                       executed_by_id=ids["doctor"], procedure_code="X3", procedure_name="Cancelled",
                                       procedure_base_price=Decimal("50.00"), procedure_currency="RON",
                                       procedure_default_duration=10, status="cancelled", current_price=Decimal("50.00"))
        pending_procedure = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=appt.id,
                                       patient_id=ids["patient"], procedure_catalog_id=catalog.id,
                                       executed_by_id=ids["doctor"], procedure_code="X4", procedure_name="Pending",
                                       procedure_base_price=Decimal("60.00"), procedure_currency="RON",
                                       procedure_default_duration=15, status="pending", current_price=Decimal("60.00"))
        session.add_all([procedure, manual_procedure, cancelled_procedure, pending_procedure])
        session.add(ProcedureExecutionSegment(performed_procedure=procedure, segment_type="run",
                                              started_at=at_local("2026-10-25T10:00:00"),
                                              ended_at=at_local("2026-10-25T10:02:00"), duration_seconds=120))
        session.add(ProcedureExecutionSegment(performed_procedure=procedure, segment_type="pause",
                                              started_at=at_local("2026-10-25T10:02:00"),
                                              ended_at=at_local("2026-10-25T10:12:00"), duration_seconds=600))
        session.add(ProcedureExecutionSegment(performed_procedure=manual_procedure, segment_type="run",
                                              started_at=at_local("2026-10-25T10:03:00"),
                                              ended_at=at_local("2026-10-25T10:08:00"), duration_seconds=300))
        financial_entries = {}
        for key, kind, amount, method in (
            ("cash_payment", "payment", Decimal("70.00"), "cash"),
            ("cash_refund", "refund", Decimal("30.00"), "cash"),
            ("card_payment", "payment", Decimal("50.00"), "card"),
            ("card_refund", "refund", Decimal("10.00"), "card"),
            ("adjustment", "adjustment_credit", Decimal("20.00"), None),
        ):
            entry = PatientFinancialEntry(
                clinic_id=ids["clinic"], patient_id=ids["patient"], kind=kind, amount=amount,
                currency="RON", method=method, reason="test" if kind != "payment" else None,
                client_request_id=key, created_by_id=ids["manager"], created_at=at_local("2026-10-25T11:00:00"),
            )
            financial_entries[key] = entry
            session.add(entry)
        await session.flush()
        catalog.default_duration_minutes = 999  # Completed rows must use the captured procedure snapshot.
        reversal_entries = []
        for key, target_key in (("reverse_cash_payment", "cash_payment"),
                                ("reverse_cash_refund", "cash_refund"),
                                ("reverse_adjustment", "adjustment")):
            target = financial_entries[target_key]
            reversal = PatientFinancialEntry(
                clinic_id=ids["clinic"], patient_id=ids["patient"], kind="reversal", amount=target.amount,
                currency="RON", method=None, reason="test reversal", client_request_id=key,
                created_by_id=ids["manager"], created_at=at_local("2026-10-25T11:00:00"),
                reverses_entry_id=target.id,
            )
            reversal_entries.append(reversal)
            session.add(reversal)
        plan = TreatmentPlan(clinic_id=ids["clinic"], patient_id=ids["patient"], title="Accepted plan",
                             status="accepted", version=1, accepted_version=1, created_by_id=ids["doctor"])
        session.add(plan)
        await session.flush()
        stage = TreatmentPlanStage(clinic_id=ids["clinic"], plan_id=plan.id, position=1, title="Stage 1")
        session.add(stage)
        await session.flush()
        item = TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=plan.id, stage_id=stage.id,
                                 position=1, procedure_catalog_id=catalog.id, procedure_code="X",
                                 procedure_name="Exam", currency="RON", estimated_price=Decimal("90.00"),
                                 estimated_duration_minutes=30, priority="normal", status="active",
                                 created_at=at_local("2026-10-24T10:00:00"))
        session.add(item)
        session.add_all([
            TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=plan.id, stage_id=stage.id,
                position=2, procedure_catalog_id=catalog.id, procedure_code="X", procedure_name="Cancelled item",
                currency="RON", estimated_price=Decimal("90.00"), estimated_duration_minutes=30,
                priority="normal", status="cancelled", created_at=at_local("2026-10-23T10:00:00")),
            TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=plan.id, stage_id=stage.id,
                position=3, procedure_catalog_id=catalog.id, procedure_code="X", procedure_name="Performed item",
                currency="RON", estimated_price=Decimal("90.00"), estimated_duration_minutes=30,
                priority="normal", status="active", performed_procedure_id=procedure.id,
                created_at=at_local("2026-10-22T10:00:00")),
        ])
        session.add(TreatmentPlanAcceptance(clinic_id=ids["clinic"], plan_id=plan.id, version=1,
                                            accepted_by_id=ids["manager"], accepted_at=at_local("2026-10-24T10:00:00"),
                                            note="Accepted in test", snapshot={}))
        draft_plan = TreatmentPlan(clinic_id=ids["clinic"], patient_id=ids["patient"], title="Unaccepted plan",
                                   status="draft", version=1, accepted_version=None, created_by_id=ids["doctor"])
        session.add(draft_plan)
        await session.flush()
        draft_stage = TreatmentPlanStage(clinic_id=ids["clinic"], plan_id=draft_plan.id, position=1, title="Draft stage")
        session.add(draft_stage)
        await session.flush()
        session.add(TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=draft_plan.id, stage_id=draft_stage.id,
            position=1, procedure_catalog_id=catalog.id, procedure_code="X", procedure_name="Unaccepted item",
            currency="RON", estimated_price=Decimal("90.00"), estimated_duration_minutes=30,
            priority="normal", status="active", created_at=at_local("2026-10-21T10:00:00")))
        await session.commit()

    params = "from=2026-10-25&to=2026-10-25"
    expected = {
        "services_performed": "300.00",
        "procedure_estimated_duration": 3000,
        "procedure_measured_duration": 120,
        "procedure_manual_duration": 1,
    }
    for indicator, value in expected.items():
        response = await client.get(f"/api/reports/{indicator}?{params}", headers=headers)
        assert response.status_code == 200, (indicator, response.text)
        assert response.json()["value"] == value
        source_rows = await client.get(f"/api/reports/{indicator}/records?{params}", headers=headers)
        source_records = source_rows.json()["records"]
        expected_ids = {
            "services_performed": {str(procedure.id), str(manual_procedure.id)},
            "procedure_estimated_duration": {str(procedure.id), str(manual_procedure.id)},
            "procedure_measured_duration": {str(procedure.id)},
            "procedure_manual_duration": {str(manual_procedure.id)},
        }[indicator]
        assert source_rows.json()["total"] == len(expected_ids)
        assert {row["id"] for row in source_records} == expected_ids
        amount_field = {"services_performed": "amount", "procedure_estimated_duration": "estimated_seconds",
                        "procedure_measured_duration": "measured_seconds"}.get(indicator)
        if amount_field:
            assert sum(Decimal(str(row[amount_field])) for row in source_records) == Decimal(value)
        assert not {str(cancelled_procedure.id), str(pending_procedure.id)} & {row["id"] for row in source_records}
        assert all(not ({"notes", "allergies", "diagnosis"} & set(row)) for row in source_records)
    net = await client.get(f"/api/reports/receipts_net_by_method?{params}", headers=headers)
    assert net.json()["value"] == {"card": "40.00", "cash": "0.00"}
    net_records = await client.get(f"/api/reports/receipts_net_by_method/records?{params}", headers=headers)
    assert net_records.json()["total"] == 6
    expected_net_ids = {str(entry.id) for key, entry in financial_entries.items() if key != "adjustment"}
    expected_net_ids |= {str(entry.id) for entry in reversal_entries[:2]}
    assert {row["id"] for row in net_records.json()["records"]} == expected_net_ids
    signed_by_method = {"cash": Decimal("0.00"), "card": Decimal("0.00")}
    for row in net_records.json()["records"]:
        if row["status"] == "payment": sign = Decimal(1)
        elif row["status"] == "refund" or row["reverses_kind"] == "payment": sign = Decimal(-1)
        else: sign = Decimal(1)
        signed_by_method[row["method"]] += Decimal(row["amount"]) * sign
    assert {method: f"{amount:.2f}" for method, amount in sorted(signed_by_method.items())} == net.json()["value"]
    assert {row["status"] for row in net_records.json()["records"]} == {"payment", "refund", "reversal"}
    cash_payment_row = next(row for row in net_records.json()["records"] if row["id"] == str(financial_entries["cash_payment"].id))
    cash_reversal_row = next(row for row in net_records.json()["records"] if row["id"] == str(reversal_entries[0].id))
    assert cash_payment_row["status"] == "payment" and cash_reversal_row["status"] == "reversal"
    assert all(not ({"notes", "allergies", "diagnosis"} & set(row)) for row in net_records.json()["records"])
    reversal = await client.get(f"/api/reports/financial_reversals?{params}", headers=headers)
    assert reversal.json()["value"] == {"count": 3, "amount": "120.00", "by_reversed_kind": {"adjustment_credit": "20.00", "payment": "70.00", "refund": "30.00"}}
    reversal_records = await client.get(f"/api/reports/financial_reversals/records?{params}", headers=headers)
    assert reversal_records.json()["total"] == reversal.json()["value"]["count"]
    assert sum(Decimal(row["amount"]) for row in reversal_records.json()["records"]) == Decimal(reversal.json()["value"]["amount"])
    grouped_reversals = {}
    for row in reversal_records.json()["records"]:
        grouped_reversals[row["reverses_kind"]] = grouped_reversals.get(row["reverses_kind"], Decimal("0.00")) + Decimal(row["amount"])
    assert {kind: f"{amount:.2f}" for kind, amount in sorted(grouped_reversals.items())} == reversal.json()["value"]["by_reversed_kind"]
    assert {row["id"] for row in reversal_records.json()["records"]} == {str(entry.id) for entry in reversal_entries}
    consumption = await client.get(f"/api/reports/consumptions?{params}", headers=headers)
    assert consumption.json()["value"] == []
    consumption_rows = await client.get(f"/api/reports/consumptions/records?{params}", headers=headers)
    assert consumption_rows.json()["total"] == 0 and consumption_rows.json()["records"] == []
    uncontinued = await client.get(f"/api/reports/treatment_items_uncontinued?{params}", headers=headers)
    assert uncontinued.status_code == 200, uncontinued.text
    assert uncontinued.json()["value"] == {"count": 1, "ages_days": [1]}
    item_records = await client.get(f"/api/reports/treatment_items_uncontinued/records?{params}", headers=headers)
    assert item_records.json()["total"] == 1
    assert item_records.json()["records"][0]["id"] == str(item.id)
    assert item_records.json()["records"][0]["age_days"] == uncontinued.json()["value"]["ages_days"][0] == 1


@pytest.mark.asyncio
async def test_every_indicator_filter_reconciles_to_its_exact_source_rows(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        other_doctor = User(clinic_id=ids["clinic"], full_name="Other Doctor", email="other-doctor@reports.test",
                            password_hash=hash_password("password-123456"), role="doctor", is_active=True)
        other_room = Room(clinic_id=ids["clinic"], location_id=ids["location"], name="Room 2", is_active=True)
        session.add_all([other_doctor, other_room])
        await session.flush()
        appointments = {
            "target_completed": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"], room_id=ids["room"], starts_at=at_local("2026-10-25T10:00:00"), duration_minutes=30, status="completed", appointment_type="Checkup"),
            "target_scheduled": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"], room_id=ids["room"], starts_at=at_local("2026-10-25T09:00:00"), duration_minutes=30, status="scheduled", appointment_type="Checkup"),
            "other_doctor_completed": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=other_doctor.id, room_id=ids["room"], starts_at=at_local("2026-10-25T11:00:00"), duration_minutes=30, status="completed", appointment_type="Checkup"),
            "other_doctor_cancelled": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=other_doctor.id, room_id=ids["room"], starts_at=at_local("2026-10-25T12:00:00"), duration_minutes=30, status="cancelled", appointment_type="Checkup"),
            "other_room_completed": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"], room_id=other_room.id, starts_at=at_local("2026-10-25T13:00:00"), duration_minutes=30, status="completed", appointment_type="Checkup"),
            "other_room_no_show": Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"], room_id=other_room.id, starts_at=at_local("2026-10-25T14:00:00"), duration_minutes=30, status="no_show", appointment_type="Checkup"),
        }
        session.add_all(appointments.values())
        await session.flush()
        catalog = ProcedureCatalog(clinic_id=ids["clinic"], code="F", code_lower="f", name="Filter fixture", default_duration_minutes=10, base_price=Decimal("40.00"), currency="RON")
        session.add(catalog)
        await session.flush()
        procedure_specs = (
            ("target_completed", "P1", Decimal("40.00"), 10, 90, False),
            ("other_doctor_completed", "P2", Decimal("50.00"), 20, 120, False),
            ("other_room_completed", "P3", Decimal("60.00"), 30, 180, True),
        )
        procedures = {}
        for appt_key, code, price, minutes, measured, manual in procedure_specs:
            appointment = appointments[appt_key]
            procedure = PerformedProcedure(
                clinic_id=ids["clinic"], appointment_id=appointment.id, patient_id=ids["patient"],
                procedure_catalog_id=catalog.id, executed_by_id=appointment.doctor_id, procedure_code=code,
                procedure_name=f"Procedure {code}", procedure_base_price=price, procedure_currency="RON",
                procedure_default_duration=minutes, status="completed", current_price=price,
                notes="Manual duration override: fixture" if manual else None,
            )
            procedures[appt_key] = procedure
            session.add(procedure)
            if not manual:
                session.add(ProcedureExecutionSegment(performed_procedure=procedure, segment_type="run",
                    started_at=appointment.starts_at,
                    ended_at=appointment.starts_at + timedelta(seconds=measured), duration_seconds=measured))
        payments = {}
        for appt_key, amount in (("target_completed", Decimal("12.00")),
                                 ("other_doctor_completed", Decimal("20.00")),
                                 ("other_room_completed", Decimal("30.00"))):
            payment = PatientFinancialEntry(clinic_id=ids["clinic"], patient_id=ids["patient"],
                appointment_id=appointments[appt_key].id, kind="payment", amount=amount, currency="RON",
                method="cash", client_request_id=f"payment-{appt_key}", created_by_id=ids["manager"],
                created_at=at_local("2026-10-25T15:00:00"))
            payments[appt_key] = payment
            session.add(payment)
        await session.flush()
        reversals = {}
        for appt_key, payment in payments.items():
            reversal = PatientFinancialEntry(clinic_id=ids["clinic"], patient_id=ids["patient"],
                appointment_id=appointments[appt_key].id, kind="reversal", amount=payment.amount,
                currency="RON", reason="Fixture reversal", client_request_id=f"reversal-{appt_key}",
                created_by_id=ids["manager"], created_at=at_local("2026-10-25T15:01:00"),
                reverses_entry_id=payment.id)
            reversals[appt_key] = reversal
            session.add(reversal)

        items = {}
        for index, appt_key in enumerate(("target_completed", "other_doctor_completed", "other_room_completed"), start=1):
            appointment = appointments[appt_key]
            plan = TreatmentPlan(clinic_id=ids["clinic"], patient_id=ids["patient"], title=f"Plan {index}",
                status="accepted", version=1, accepted_version=1, created_by_id=appointment.doctor_id)
            session.add(plan)
            await session.flush()
            stage = TreatmentPlanStage(clinic_id=ids["clinic"], plan_id=plan.id, position=1, title="Stage")
            session.add(stage)
            await session.flush()
            item = TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=plan.id, stage_id=stage.id, position=1,
                procedure_catalog_id=catalog.id, procedure_code="F", procedure_name="Filter fixture",
                currency="RON", estimated_price=Decimal("40.00"), estimated_duration_minutes=10,
                priority="normal", status="active", appointment_id=appointment.id,
                created_at=at_local(f"2026-10-{25-index:02d}T08:00:00"))
            items[appt_key] = item
            session.add_all([item, TreatmentPlanAcceptance(clinic_id=ids["clinic"], plan_id=plan.id, version=1,
                accepted_by_id=ids["manager"], accepted_at=at_local("2026-10-24T08:00:00"), note="accepted", snapshot={})])
        session.add(WorkingHours(clinic_id=ids["clinic"], day_of_week=6, start_time=time(9), end_time=time(13), is_active=True))
        await session.commit()

    indicator_keys = (
        "appointments_total", "appointments_cancelled", "appointments_no_show", "appointments_completed",
        "cancellation_rate", "no_show_rate", "room_occupancy", "procedure_estimated_duration",
        "procedure_measured_duration", "procedure_manual_duration", "treatment_items_uncontinued",
        "services_performed", "receipts_net_by_method", "financial_reversals", "consumptions",
    )
    groups = {
        "doctor": {"target_completed", "target_scheduled", "other_room_completed", "other_room_no_show"},
        "room": {"target_completed", "target_scheduled", "other_doctor_completed", "other_doctor_cancelled"},
    }
    expected_values = {
        "doctor": {
            "appointments_total": 4, "appointments_cancelled": 0, "appointments_no_show": 1, "appointments_completed": 2,
            "cancellation_rate": 0.0, "no_show_rate": 25.0,
            "procedure_estimated_duration": 2400, "procedure_measured_duration": 90, "procedure_manual_duration": 1,
            "treatment_items_uncontinued": {"count": 2, "ages_days": [1, 3]}, "services_performed": "100.00",
            "receipts_net_by_method": {"cash": "0.00"},
            "financial_reversals": {"count": 2, "amount": "42.00", "by_reversed_kind": {"payment": "42.00"}},
            "consumptions": [],
        },
        "room": {
            "appointments_total": 4, "appointments_cancelled": 1, "appointments_no_show": 0, "appointments_completed": 2,
            "cancellation_rate": 25.0, "no_show_rate": 0.0,
            "procedure_estimated_duration": 1800, "procedure_measured_duration": 210, "procedure_manual_duration": 0,
            "treatment_items_uncontinued": {"count": 2, "ages_days": [1, 2]}, "services_performed": "90.00",
            "receipts_net_by_method": {"cash": "0.00"},
            "financial_reversals": {"count": 2, "amount": "32.00", "by_reversed_kind": {"payment": "32.00"}},
            "consumptions": [],
        },
    }
    for group, keys in groups.items():
        filter_name = "doctor_id" if group == "doctor" else "room_id"
        filter_value = ids["doctor"] if group == "doctor" else ids["room"]
        base_filters = {filter_name: str(filter_value)}
        for indicator in indicator_keys:
            filters = dict(base_filters)
            if group == "doctor" and indicator == "room_occupancy":
                filters["room_id"] = str(ids["room"])
            query = "from=2026-10-25&to=2026-10-25&" + "&".join(f"{key}={value}" for key, value in filters.items())
            report = await client.get(f"/api/reports/{indicator}?{query}", headers=headers)
            records_response = await client.get(f"/api/reports/{indicator}/records?{query}", headers=headers)
            assert report.status_code == records_response.status_code == 200, (group, indicator, report.text, records_response.text)
            value = report.json()["value"]
            records = records_response.json()["records"]
            allowed_record_fields = {"id", "entity", "date", "status", "amount", "patient_name", "method",
                                     "reverses_kind", "estimated_seconds", "manual_duration", "measured_seconds", "age_days"}
            assert all(set(row) <= allowed_record_fields for row in records)
            assert value == expected_values[group].get(indicator, {"scheduled_minutes": 30, "available_minutes": 240, "occupancy_percent": 12.5}), (group, indicator, value)
            assert records_response.json()["total"] == len(records)
            if indicator in {"appointments_total", "cancellation_rate", "no_show_rate"}:
                assert {row["id"] for row in records} == {str(appointments[key].id) for key in keys}
                if indicator == "appointments_total": assert len(records) == value
                if indicator.endswith("rate"):
                    status = "cancelled" if indicator == "cancellation_rate" else "no_show"
                    expected_rate = round(sum(row["status"] == status for row in records) * 100 / len(records), 2)
                    assert value == expected_rate
            elif indicator.startswith("appointments_"):
                wanted = "cancelled" if indicator.endswith("cancelled") else "no_show" if indicator.endswith("no_show") else "completed"
                assert {row["id"] for row in records} == {str(appointments[key].id) for key in keys if appointments[key].status == wanted}
                assert len(records) == value
            elif indicator == "room_occupancy":
                assert len([row for row in records if row["entity"] == "appointment"]) == 1
                assert sum(int(row["amount"]) for row in records if row["entity"] == "appointment") == value["scheduled_minutes"]
                assert sum(int(row["amount"]) for row in records if row["entity"] == "working_hours") == value["available_minutes"]
            elif indicator.startswith("procedure_") or indicator == "services_performed":
                selected_procedures = [procedures[key] for key in keys if key in procedures]
                if indicator == "procedure_measured_duration": selected_procedures = [p for p in selected_procedures if not p.notes]
                if indicator == "procedure_manual_duration": selected_procedures = [p for p in selected_procedures if p.notes]
                assert {row["id"] for row in records} == {str(p.id) for p in selected_procedures}
                if indicator == "procedure_manual_duration": assert len(records) == value
                elif indicator == "procedure_estimated_duration": assert sum(row["estimated_seconds"] for row in records) == value
                elif indicator == "procedure_measured_duration": assert sum(row["measured_seconds"] for row in records) == value
                elif indicator == "services_performed": assert sum(Decimal(row["amount"]) for row in records) == Decimal(value)
            elif indicator == "treatment_items_uncontinued":
                expected_items = [items[key] for key in keys if key in items]
                assert {row["id"] for row in records} == {str(item.id) for item in expected_items}
                assert value["count"] == len(records) == len(value["ages_days"])
                assert sorted(row["age_days"] for row in records) == sorted(value["ages_days"])
            elif indicator == "receipts_net_by_method":
                expected_entries = [entry for key in keys if key in payments for entry in (payments[key], reversals[key])]
                assert {row["id"] for row in records} == {str(entry.id) for entry in expected_entries}
                assert all(row["method"] == "cash" for row in records)
            elif indicator == "financial_reversals":
                expected_entries = [reversals[key] for key in keys if key in reversals]
                assert {row["id"] for row in records} == {str(entry.id) for entry in expected_entries}
                assert value["count"] == len(records)
                assert sum(Decimal(row["amount"]) for row in records) == Decimal(value["amount"])
                assert sum(Decimal(row["amount"]) for row in records if row.get("reverses_kind") == "payment") == Decimal(value["by_reversed_kind"].get("payment", "0"))
            elif indicator == "consumptions":
                assert value == [] and records == []
            assert all(not ({"notes", "allergies", "diagnosis", "tooth_fdi", "clinical_note"} & set(row)) for row in records)


@pytest.mark.asyncio
async def test_consumption_report_reconciles_sources_corrections_filters_and_local_period(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        item = InventoryItem(clinic_id=ids["clinic"], name="Consumabil fictiv", unit="buc", minimum_quantity=Decimal(0))
        liquid = InventoryItem(clinic_id=ids["clinic"], name="Lichid fictiv", unit="ml", minimum_quantity=Decimal(0))
        location = InventoryLocation(clinic_id=ids["clinic"], name="Depozit fictiv")
        session.add_all([item, liquid, location])
        await session.flush()
        lot = InventoryLot(clinic_id=ids["clinic"], item_id=item.id, code="LOT-CONSUM")
        liquid_lot = InventoryLot(clinic_id=ids["clinic"], item_id=liquid.id, code="LOT-LICHID")
        catalog = ProcedureCatalog(clinic_id=ids["clinic"], code="CONSUM", code_lower="consum",
            name="Manoperă fictivă", default_duration_minutes=30, base_price=Decimal("10.00"))
        session.add_all([lot, liquid_lot, catalog])
        await session.flush()
        appointment = Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"], doctor_id=ids["doctor"],
            room_id=ids["room"], starts_at=at_local("2026-10-25T23:59:59"), duration_minutes=30,
            status="completed", appointment_type="Control fictiv")
        session.add(appointment)
        await session.flush()
        performed = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=appointment.id,
            patient_id=ids["patient"], procedure_catalog_id=catalog.id, executed_by_id=ids["doctor"],
            procedure_code=catalog.code, procedure_name=catalog.name, procedure_base_price=Decimal("10.00"),
            procedure_currency="RON", procedure_default_duration=30, status="completed")
        session.add(performed)
        await session.flush()
        other_doctor_appointment = Appointment(clinic_id=ids["clinic"], patient_id=ids["patient"],
            doctor_id=ids["manager"], room_id=ids["room"], starts_at=at_local("2026-10-25T20:00:00"),
            duration_minutes=30, status="completed", appointment_type="Control fictiv")
        session.add(other_doctor_appointment)
        await session.flush()
        other_doctor_procedure = PerformedProcedure(clinic_id=ids["clinic"], appointment_id=other_doctor_appointment.id,
            patient_id=ids["patient"], procedure_catalog_id=catalog.id, executed_by_id=ids["manager"],
            procedure_code=catalog.code, procedure_name=catalog.name, procedure_base_price=Decimal("10.00"),
            procedure_currency="RON", procedure_default_duration=30, status="completed")
        session.add(other_doctor_procedure)
        await session.flush()

        def movement(*, item_id, lot_id, quantity, at, kind="consumption", reverses=None, tag):
            return InventoryMovement(id=uuid4(), clinic_id=ids["clinic"], item_id=item_id, lot_id=lot_id,
                location_id=location.id, kind=kind, quantity=Decimal(quantity), client_request_id=tag,
                signature=(tag.encode().hex() * 32)[:64], reverses_movement_id=reverses,
                reason=f"Motiv {tag}", created_by_id=ids["manager"], created_at=at_local(at))

        t18 = movement(item_id=item.id, lot_id=lot.id, quantity="1.234", at="2026-10-25T23:59:59", tag="t18")
        other_t18 = movement(item_id=item.id, lot_id=lot.id, quantity="0.500", at="2026-10-25T20:00:00", tag="other-t18")
        direct = movement(item_id=item.id, lot_id=lot.id, quantity="2.500", at="2026-10-25T12:00:00", tag="direct")
        other_unit = movement(item_id=liquid.id, lot_id=liquid_lot.id, quantity="3", at="2026-10-25T12:01:00", tag="liquid")
        corrected_same_day = movement(item_id=item.id, lot_id=lot.id, quantity="5", at="2026-10-25T13:00:00", tag="same-original")
        correction_same_day = movement(item_id=item.id, lot_id=lot.id, quantity="5", at="2026-10-25T14:00:00",
            kind="reversal_in", reverses=corrected_same_day.id, tag="same-reversal")
        corrected_later = movement(item_id=item.id, lot_id=lot.id, quantity="4", at="2026-10-25T15:00:00", tag="later-original")
        correction_later = movement(item_id=item.id, lot_id=lot.id, quantity="4", at="2026-10-26T00:10:00",
            kind="reversal_in", reverses=corrected_later.id, tag="later-reversal")
        next_local_day = movement(item_id=item.id, lot_id=lot.id, quantity="7", at="2026-10-26T00:00:00", tag="next-day")
        session.add_all([t18, other_t18, direct, other_unit, corrected_same_day, correction_same_day,
                         corrected_later, correction_later, next_local_day])
        batch = MaterialConsumptionBatch(clinic_id=ids["clinic"], performed_procedure_id=performed.id,
            client_request_id=str(uuid4()), signature="b" * 64, reason="Motiv T18", actor_id=ids["manager"])
        session.add(batch)
        await session.flush()
        session.add(MaterialConsumptionLine(clinic_id=ids["clinic"], batch_id=batch.id,
            inventory_movement_id=t18.id, item_id=item.id, lot_id=lot.id, location_id=location.id,
            estimated_quantity=Decimal("0.750"), actual_quantity=Decimal("1.234")))
        other_batch = MaterialConsumptionBatch(clinic_id=ids["clinic"], performed_procedure_id=other_doctor_procedure.id,
            client_request_id=str(uuid4()), signature="c" * 64, reason="Motiv T18 secundar", actor_id=ids["manager"])
        session.add(other_batch)
        await session.flush()
        session.add(MaterialConsumptionLine(clinic_id=ids["clinic"], batch_id=other_batch.id,
            inventory_movement_id=other_t18.id, item_id=item.id, lot_id=lot.id, location_id=location.id,
            estimated_quantity=Decimal("0.250"), actual_quantity=Decimal("0.500")))

        foreign_clinic = Clinic(name="Foreign Consumption Clinic", timezone="Europe/Bucharest")
        session.add(foreign_clinic)
        await session.flush()
        foreign_user = User(clinic_id=foreign_clinic.id, full_name="Foreign Manager", email="foreign-consumption@reports.test",
            password_hash=hash_password("password-123456"), role="clinic_manager", is_active=True)
        foreign_item = InventoryItem(clinic_id=foreign_clinic.id, name="Foreign item", unit="buc", minimum_quantity=Decimal(0))
        foreign_location = InventoryLocation(clinic_id=foreign_clinic.id, name="Foreign location")
        session.add_all([foreign_user, foreign_item, foreign_location])
        await session.flush()
        foreign_lot = InventoryLot(clinic_id=foreign_clinic.id, item_id=foreign_item.id, code="FOREIGN-LOT")
        session.add(foreign_lot)
        await session.flush()
        session.add(InventoryMovement(clinic_id=foreign_clinic.id, item_id=foreign_item.id, lot_id=foreign_lot.id,
            location_id=foreign_location.id, kind="consumption", quantity=Decimal(99),
            client_request_id=str(uuid4()), signature="f" * 64, reason="foreign", created_by_id=foreign_user.id,
            created_at=at_local("2026-10-25T10:00:00")))
        await session.commit()
        t18_id, other_t18_id, direct_id = str(t18.id), str(other_t18.id), str(direct.id)
        same_id, later_id, next_id = map(str, (corrected_same_day.id, corrected_later.id, next_local_day.id))
        item_id, liquid_id = str(item.id), str(liquid.id)

    params = "from=2026-10-25&to=2026-10-25"
    engine = sessions.kw["bind"]
    query_count = 0
    def count_query(*_args):
        nonlocal query_count
        query_count += 1
    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    summary = await client.get(f"/api/reports/consumptions?{params}", headers=headers)
    summary_query_count = query_count
    query_count = 0
    response = await client.get(f"/api/reports/consumptions/records?{params}", headers=headers)
    event.remove(engine.sync_engine, "before_cursor_execute", count_query)
    assert summary.status_code == response.status_code == 200
    assert summary_query_count <= 3 and query_count <= 3  # Fixed query count, not one query per movement.
    assert summary.json()["period"]["semantics"] == "local calendar days, inclusive"
    assert "indiferent de data corecției" in summary.json()["definition"]
    assert "InventoryMovement" in summary.json()["source"] and "MaterialConsumptionLine" in summary.json()["source"]
    assert response.json()["total"] == 4
    assert {row["id"] for row in response.json()["records"]} == {t18_id, other_t18_id, direct_id, str(other_unit.id)}
    rows = {row["id"]: row for row in response.json()["records"]}
    assert rows[t18_id]["date"] == "2026-10-25T23:59:59+02:00"
    assert rows[t18_id]["source_type"] == "t18" and rows[t18_id]["patient_name"] == "Ana Test"
    assert rows[t18_id]["procedure_name"] == "Manoperă fictivă"
    assert rows[t18_id]["estimated_quantity"] == "0.750" and rows[t18_id]["actual_quantity"] == "1.234"
    assert rows[direct_id]["source_type"] == "direct" and rows[direct_id]["estimated_quantity"] is None
    assert rows[direct_id]["author"] == "Report Manager" and rows[direct_id]["reason"] == "Motiv direct"
    assert all(row["item_name"] and row["lot"] and row["location"] and row["unit"] for row in rows.values())
    groups = {(entry["item_id"], entry["unit"], entry["source_type"]): entry for entry in summary.json()["value"]}
    assert groups[(item_id, "buc", "t18")]["quantity"] == "1.734"
    assert groups[(item_id, "buc", "t18")]["estimated_quantity"] == "1.000"
    assert groups[(item_id, "buc", "t18")]["actual_quantity"] == "1.734"
    assert groups[(item_id, "buc", "direct")]["quantity"] == "2.500"
    assert groups[(liquid_id, "ml", "direct")]["quantity"] == "3.000"
    assert not {same_id, later_id, next_id} & {row["id"] for row in response.json()["records"]}

    filtered = await client.get(f"/api/reports/consumptions?{params}&doctor_id={ids['doctor']}&room_id={ids['room']}", headers=headers)
    filtered_records = await client.get(f"/api/reports/consumptions/records?{params}&doctor_id={ids['doctor']}&room_id={ids['room']}", headers=headers)
    assert filtered.status_code == filtered_records.status_code == 200
    assert "Consumul direct nu poate fi atribuit" in filtered.json()["definition"]
    assert filtered_records.json()["total"] == 1
    assert filtered_records.json()["records"][0]["id"] == t18_id
    room_filtered = await client.get(f"/api/reports/consumptions/records?{params}&room_id={ids['room']}", headers=headers)
    doctor_filtered = await client.get(f"/api/reports/consumptions/records?{params}&doctor_id={ids['doctor']}", headers=headers)
    assert {row["id"] for row in room_filtered.json()["records"]} == {t18_id, other_t18_id}
    assert {row["id"] for row in doctor_filtered.json()["records"]} == {t18_id}
    next_day = await client.get("/api/reports/consumptions/records?from=2026-10-26&to=2026-10-26", headers=headers)
    assert {row["id"] for row in next_day.json()["records"]} == {next_id}
    empty = await client.get("/api/reports/consumptions?from=2026-11-01&to=2026-11-01", headers=headers)
    assert empty.status_code == 200 and empty.json()["value"] == []
