from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Appointment, Base, User, get_db
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
