"""T26 PostgreSQL migration and concurrency guarantees."""
import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comms.models import CommConsent, CommOutbox
from app.database import Appointment, Clinic, Patient, Room, User, get_db
from app.feedback.models import FeedbackRequest, PatientFeedback
from app.feedback.service import schedule_visit_messages
from app.main import app

pytestmark = pytest.mark.postgres


def admin_dsn(url):
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path="/postgres"))


async def run_alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t26-ci-only", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", *args], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True)


@pytest.fixture
async def migrated():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    name = "t26_" + uuid4().hex[:12]
    admin = await asyncpg.connect(admin_dsn(source))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        result = await run_alembic(url, "upgrade", "head")
        assert result.returncode == 0, result.stdout + result.stderr
        engine = create_async_engine(url)
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()
    finally:
        admin = await asyncpg.connect(admin_dsn(source))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def seed(sessions):
    async with sessions() as db:
        clinic = Clinic(name="Clinică fictivă", timezone="Europe/Bucharest")
        db.add(clinic)
        await db.flush()
        manager = User(clinic_id=clinic.id, email=f"{uuid4().hex}@example.com", password_hash="x", full_name="Manager fictiv", role="clinic_manager")
        patient = Patient(clinic_id=clinic.id, first_name="Ana", last_name="Fictiv", phone="+40700000001", email=f"{uuid4().hex}@example.com")
        room = Room(clinic_id=clinic.id, name="Cabinet fictiv")
        db.add_all((manager, patient, room))
        await db.flush()
        appointment = Appointment(clinic_id=clinic.id, patient_id=patient.id, doctor_id=manager.id, room_id=room.id, starts_at=datetime.now(UTC) - timedelta(days=1), ends_at=datetime.now(UTC) - timedelta(days=1) + timedelta(minutes=30), duration_minutes=30, status="completed", appointment_type="Control")
        db.add(appointment)
        db.add(CommConsent(clinic_id=clinic.id, patient_id=patient.id, channel="email", granted=True, source="in_person", recorded_by_id=manager.id))
        await db.commit()
        return clinic.id, patient.id, appointment.id


@pytest.mark.asyncio
async def test_feedback_token_is_consumed_once_under_concurrent_posts(migrated):
    sessions = migrated
    _clinic_id, _patient_id, appointment_id = await seed(sessions)
    async with sessions() as db:
        appt = await db.get(Appointment, appointment_id)
        await schedule_visit_messages(db, appt)
        await db.commit()
    async with sessions() as db:
        outbox = await db.scalar(select(CommOutbox).where(CommOutbox.message_type == "feedback_request"))
        token = outbox.body.rsplit("/feedback/", 1)[-1]

    async def override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(*(client.post(f"/api/feedback/public/{token}", json={"rating": 2}) for _ in range(2)))
        assert all(response.status_code == 200 for response in responses)
    finally:
        app.dependency_overrides.clear()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(FeedbackRequest).where(FeedbackRequest.used_at.is_not(None))) == 1
        assert await db.scalar(select(func.count()).select_from(PatientFeedback)) == 1


@pytest.mark.asyncio
async def test_campaign_patient_idempotency_is_enforced_under_concurrency(migrated):
    sessions = migrated
    clinic_id, patient_id, appointment_id = await seed(sessions)
    campaign_id = uuid4()
    async with sessions() as db:
        from app.feedback.models import Campaign
        user_id = await db.scalar(select(User.id).where(User.clinic_id == clinic_id))
        db.add(Campaign(id=campaign_id, clinic_id=clinic_id, name="Campanie", filter_type="no_future_appointment", template_type="campaign", scheduled_for=datetime.now(UTC), status="draft", created_by_id=user_id))
        await db.commit()
    key = f"campaign:{campaign_id}:{patient_id}"

    async def insert_one():
        async with sessions() as db:
            db.add(CommOutbox(clinic_id=clinic_id, patient_id=patient_id, appointment_id=appointment_id, campaign_id=campaign_id, message_type="campaign", channel="email", appointment_starts_at=datetime.now(UTC), idempotency_key=key, status="pending", scheduled_for=datetime.now(UTC), attempts=0))
            try:
                await db.commit()
                return True
            except IntegrityError:
                await db.rollback()
                return False

    results = await asyncio.gather(*(insert_one() for _ in range(8)))
    assert results.count(True) == 1
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(CommOutbox).where(CommOutbox.campaign_id == campaign_id, CommOutbox.patient_id == patient_id)) == 1
