from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select
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
from app.main import app


def create_test_token(user_id: str, clinic_id: str, role: str) -> str:
    return jwt.encode({"sub": user_id, "clinic_id": clinic_id, "role": role,
                       "exp": datetime.now(UTC).timestamp() + 3600}, settings.jwt_secret, algorithm="HS256")


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
            manager = User(clinic_id=clinic.id, full_name="Report Manager", email="manager@reports.test",
                           password_hash=hash_password("password-123456"), role="clinic_manager", is_active=True)
            doctor = User(clinic_id=clinic.id, full_name="Report Doctor", email="doctor@reports.test",
                          password_hash=hash_password("password-123456"), role="doctor", is_active=True)
            room = Room(clinic_id=clinic.id, name="Room 1", is_active=True)
            patient = Patient(clinic_id=clinic.id, first_name="Ana", last_name="Test")
            session.add_all([manager, doctor, room, patient])
            await session.commit()
            ids = {"clinic": clinic.id, "manager": manager.id, "doctor": doctor.id,
                   "room": room.id, "patient": patient.id}
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
                        room_id=ids["room"], starts_at=at_local("2026-10-25T23:30:00"), duration_minutes=30,
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
        session.add(WorkingHours(clinic_id=ids["clinic"], day_of_week=6, start_time=time(9), end_time=time(11), is_active=True))
        await session.commit()

    res = await client.get("/api/reports/appointments_total?from=2026-10-25&to=2026-10-25", headers=headers)
    assert res.status_code == 200
    assert res.json()["value"] == 4
    assert res.json()["period"]["timezone"] == "Europe/Bucharest"
    records = await client.get("/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25", headers=headers)
    assert records.status_code == 200
    assert records.json()["total"] == 4
    assert any(row["date"].startswith("2026-10-25T23:30") for row in records.json()["records"])
    canceled = await client.get("/api/reports/appointments_cancelled?from=2026-10-25&to=2026-10-25", headers=headers)
    assert canceled.json()["value"] == 1
    no_show = await client.get("/api/reports/appointments_no_show?from=2026-10-25&to=2026-10-25", headers=headers)
    assert no_show.json()["value"] == 1
    completed = await client.get("/api/reports/appointments_completed?from=2026-10-25&to=2026-10-25", headers=headers)
    assert completed.json()["value"] == 1
    cancellation_rate = await client.get("/api/reports/cancellation_rate?from=2026-10-25&to=2026-10-25", headers=headers)
    assert cancellation_rate.json()["value"] == 25.0
    no_show_rate = await client.get("/api/reports/no_show_rate?from=2026-10-25&to=2026-10-25", headers=headers)
    assert no_show_rate.json()["value"] == 25.0
    occupancy = await client.get(f"/api/reports/room_occupancy?from=2026-10-25&to=2026-10-25&room_id={ids['room']}", headers=headers)
    assert occupancy.json()["value"] == {"scheduled_minutes": 30, "available_minutes": 120, "occupancy_percent": 25.0}
    day_span = (at_local("2026-10-26T00:00:00") - at_local("2026-10-25T00:00:00")).total_seconds() / 3600
    assert day_span == 25


@pytest.mark.asyncio
async def test_report_auth_foreign_filters_invalid_period_and_unconfigured_room(report_env):
    client, sessions, ids, headers = report_env
    async with sessions() as session:
        foreign_clinic = Clinic(name="Foreign", timezone="Europe/Bucharest")
        session.add(foreign_clinic)
        await session.flush()
        foreign_room = Room(clinic_id=foreign_clinic.id, name="Foreign room")
        session.add(foreign_room)
        await session.commit()
        foreign_id = foreign_room.id
    wrong_filter = await client.get(f"/api/reports/appointments_total?from=2026-10-25&to=2026-10-25&room_id={foreign_id}", headers=headers)
    assert wrong_filter.status_code == 404
    invalid = await client.get("/api/reports/appointments_total?from=2025-01-01&to=2026-01-02", headers=headers)
    assert invalid.status_code == 422
    backwards = await client.get("/api/reports/appointments_total?from=2026-10-26&to=2026-10-25", headers=headers)
    assert backwards.status_code == 422
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
                                       procedure_default_duration=20, status="completed", current_price=Decimal("200.00"),
                                       notes="Manual duration override: 300s - test")
        session.add(procedure)
        session.add(manual_procedure)
        session.add(ProcedureExecutionSegment(performed_procedure=procedure, segment_type="run",
                                              started_at=at_local("2026-10-25T10:00:00"),
                                              ended_at=at_local("2026-10-25T10:02:00"), duration_seconds=120))
        session.add(ProcedureExecutionSegment(performed_procedure=manual_procedure, segment_type="run",
                                              started_at=at_local("2026-10-25T10:03:00"),
                                              ended_at=at_local("2026-10-25T10:08:00"), duration_seconds=300))
        for kind, amount, method, request_id in (
            ("payment", Decimal("70.00"), "cash", "r1"),
            ("refund", Decimal("70.00"), "cash", "r2"),
        ):
            session.add(PatientFinancialEntry(clinic_id=ids["clinic"], patient_id=ids["patient"], kind=kind,
                                              amount=amount, currency="RON", method=method, reason="test" if kind != "payment" else None,
                                              client_request_id=request_id, created_by_id=ids["manager"],
                                              created_at=at_local("2026-10-25T11:00:00")))
        await session.flush()
        payment = await session.scalar(select(PatientFinancialEntry).where(PatientFinancialEntry.client_request_id == "r1"))
        session.add(PatientFinancialEntry(clinic_id=ids["clinic"], patient_id=ids["patient"], kind="reversal",
                                          amount=Decimal("70.00"), currency="RON", method=None, reason="test",
                                          client_request_id="r3", created_by_id=ids["manager"],
                                          created_at=at_local("2026-10-25T11:00:00"), reverses_entry_id=payment.id))
        plan = TreatmentPlan(clinic_id=ids["clinic"], patient_id=ids["patient"], title="Accepted plan",
                             status="accepted", version=1, accepted_version=1, created_by_id=ids["doctor"])
        session.add(plan)
        await session.flush()
        stage = TreatmentPlanStage(clinic_id=ids["clinic"], plan_id=plan.id, position=1, title="Stage 1")
        session.add(stage)
        await session.flush()
        session.add(TreatmentPlanItem(clinic_id=ids["clinic"], plan_id=plan.id, stage_id=stage.id,
                                      position=1, procedure_catalog_id=catalog.id, procedure_code="X",
                                      procedure_name="Exam", currency="RON", estimated_price=Decimal("90.00"),
                                      estimated_duration_minutes=30, priority="normal", status="active",
                                      created_at=at_local("2026-10-24T10:00:00")))
        session.add(TreatmentPlanAcceptance(clinic_id=ids["clinic"], plan_id=plan.id, version=1,
                                            accepted_by_id=ids["manager"], accepted_at=at_local("2026-10-24T10:00:00"),
                                            note="Accepted in test", snapshot={}))
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
        assert source_rows.json()["total"] == (1 if indicator in {"procedure_measured_duration", "procedure_manual_duration"} else 2)
    net = await client.get(f"/api/reports/receipts_net_by_method?{params}", headers=headers)
    assert net.json()["value"] == {"cash": "-70.00"}
    assert "Evidență internă" not in net.json()["definition"]  # UI presents the fiscal distinction.
    reversal = await client.get(f"/api/reports/financial_reversals?{params}", headers=headers)
    assert reversal.json()["value"] == {"count": 1, "amount": "70.00", "by_reversed_kind": {"payment": "70.00"}}
    consumption = await client.get(f"/api/reports/consumptions?{params}", headers=headers)
    assert consumption.json()["value"]["status"] == "unavailable"
    uncontinued = await client.get(f"/api/reports/treatment_items_uncontinued?{params}", headers=headers)
    assert uncontinued.status_code == 200, uncontinued.text
    assert uncontinued.json()["value"] == {"count": 1, "ages_days": [1]}
    item_records = await client.get(f"/api/reports/treatment_items_uncontinued/records?{params}", headers=headers)
    assert item_records.json()["total"] == 1
