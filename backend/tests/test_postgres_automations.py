"""PostgreSQL concurrency proof for T25's unique event execution key."""

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.automations.models import AutomationExecution
from app.collab.models import CollabTask
from app.database import Appointment, AppointmentHistory, Patient, Room, User, get_db, utcnow
from app.main import app


@pytest.fixture
async def postgres_automations():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(url, pool_pre_ping=True)
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


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_parallel_evaluation_creates_one_execution_and_task(postgres_automations):
    client, sessions = postgres_automations
    registration = await client.post("/api/auth/register", json={"clinic_name": f"Automation {uuid4().hex[:8]}", "full_name": "Manager fictiv", "email": f"manager-{uuid4().hex}@example.com", "password": "correct horse battery staple"})
    assert registration.status_code == 201, registration.text
    auth = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    manager = (await client.get("/api/auth/me", headers=auth)).json()
    async with sessions() as db:
        doctor = User(clinic_id=UUID(manager["clinic_id"]), email=f"doctor-{uuid4().hex}@example.com", password_hash="unused", full_name="Medic fictiv", role="doctor", is_active=True)
        room = Room(clinic_id=UUID(manager["clinic_id"]), name=f"Cabinet {uuid4().hex[:8]}", is_active=True)
        patient = Patient(clinic_id=UUID(manager["clinic_id"]), first_name="Pacient", last_name="Fictiv")
        db.add_all([doctor, room, patient])
        await db.flush()
        now = utcnow() - timedelta(minutes=1)
        appointment = Appointment(clinic_id=UUID(manager["clinic_id"]), patient_id=patient.id, doctor_id=doctor.id, room_id=room.id, starts_at=now, duration_minutes=30, status="scheduled", appointment_type="Consultație", created_at=now, updated_at=now)
        db.add(appointment)
        await db.flush()
        db.add(AppointmentHistory(clinic_id=UUID(manager["clinic_id"]), appointment_id=appointment.id, actor_id=doctor.id, action="created", details={}, occurred_at=now))
        await db.commit()
    rule = await client.post("/api/automations", headers=auth, json={"name": "Sarcină unică", "trigger": "appointment_created", "conditions": [], "action": {"target_type": "role", "target_value": "doctor", "title_template": "Verifică {{appointment.type}}", "due_in_days": 1}, "mode": "automatic"})
    assert rule.status_code == 201, rule.text
    activated = await client.post(f"/api/automations/{rule.json()['id']}/activate", headers=auth, json={"start_at": (utcnow() - timedelta(days=1)).isoformat()})
    assert activated.status_code == 200, activated.text

    async def evaluate_once():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as parallel_client:
            return await parallel_client.post("/api/automations/evaluate", headers=auth)

    responses = await asyncio.gather(evaluate_once(), evaluate_once())
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AutomationExecution).where(AutomationExecution.version_id == UUID(rule.json()["version_id"]))) == 1
        assert await db.scalar(select(func.count()).select_from(CollabTask).where(CollabTask.clinic_id == UUID(manager["clinic_id"]))) == 1
