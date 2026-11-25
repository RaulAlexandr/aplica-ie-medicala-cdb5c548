"""T15 on PostgreSQL: migration up/down, unique idempotency key under concurrency, two simultaneous queue runs."""
import asyncio
import os
import subprocess
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comms import adapters, service
from app.comms.models import CommConsent, CommOutbox, CommOutboxEvent
from app.database import Appointment, Clinic, Patient, Room, User

pytestmark = pytest.mark.postgres
ZONE = ZoneInfo("Europe/Bucharest")
NEW_TABLES = {"comm_consents", "comm_template_versions", "comm_settings", "comm_outbox", "comm_outbox_events"}


def dsn(url, name):
    p = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(p._replace(scheme="postgresql", path=f"/{name}"))


async def alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t15-ci", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", *args], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True)


@pytest.fixture
async def tempdb():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    name = "t15_" + uuid4().hex[:12]
    admin = await asyncpg.connect(dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def tables(url, name):
    conn = await asyncpg.connect(dsn(url, name))
    try:
        return {r["tablename"] for r in await conn.fetch("select tablename from pg_tables where schemaname='public'")}
    finally:
        await conn.close()


@pytest.fixture
async def migrated(tempdb):
    url, name = tempdb
    up = await alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
    engine = create_async_engine(url)
    try:
        yield url, name, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(sessions, appointments=1, first_start=None):
    first_start = first_start or datetime.combine(date.today() + timedelta(days=30), time(10), tzinfo=ZONE)
    async with sessions() as s:
        clinic = Clinic(name="Clinică fictivă", timezone="Europe/Bucharest")
        s.add(clinic)
        await s.flush()
        user = User(clinic_id=clinic.id, email=f"{uuid4().hex}@example.com", password_hash="x", full_name="Medic Fictiv", role="doctor")
        patient = Patient(clinic_id=clinic.id, first_name="Ana", last_name="Fictiv", phone="+40700000001", email="ana@example.com")
        room = Room(clinic_id=clinic.id, name="Cabinet 1")
        s.add_all([user, patient, room])
        await s.flush()
        s.add_all([CommConsent(clinic_id=clinic.id, patient_id=patient.id, channel=c, granted=True, source="in_person", recorded_by_id=user.id) for c in ("sms", "email")])
        items = []
        for i in range(appointments):
            start = (first_start + timedelta(hours=i)).astimezone(UTC)
            item = Appointment(clinic_id=clinic.id, patient_id=patient.id, doctor_id=user.id, room_id=room.id, starts_at=start, ends_at=start + timedelta(minutes=30), duration_minutes=30, status="scheduled", appointment_type="Control fictiv")
            s.add(item)
            items.append(item)
        await s.commit()
        return clinic.id, user.id, items


@pytest.mark.asyncio
async def test_t15_migration_up_down_up(tempdb):
    url, name = tempdb
    assert (await alembic(url, "upgrade", "head")).returncode == 0
    assert NEW_TABLES <= await tables(url, name)
    down = await alembic(url, "downgrade", "w2_merge_a1")
    assert down.returncode == 0, down.stdout + down.stderr
    assert not (NEW_TABLES & await tables(url, name))
    again = await alembic(url, "upgrade", "head")
    assert again.returncode == 0 and NEW_TABLES <= await tables(url, name)


@pytest.mark.asyncio
async def test_same_event_from_many_connections_queues_one_message(migrated):
    _url, _name, sessions = migrated
    clinic_id, user_id, (appt,) = await seed(sessions)

    async def fire():
        async with sessions() as s:
            fresh = await s.get(Appointment, appt.id)
            queued = await service.enqueue(s, fresh, "confirmation", "sms", datetime.now(UTC), user_id)
            await s.commit()
            return queued

    results = await asyncio.gather(*(fire() for _ in range(10)))
    assert results.count(True) == 1
    async with sessions() as s:
        assert await s.scalar(select(func.count(CommOutbox.id)).where(CommOutbox.appointment_id == appt.id)) == 1
        assert await s.scalar(select(func.count(CommOutboxEvent.id)).where(CommOutboxEvent.clinic_id == clinic_id)) == 1


@pytest.mark.asyncio
async def test_unique_key_is_enforced_by_the_database(migrated):
    _url, _name, sessions = migrated
    clinic_id, _user_id, (appt,) = await seed(sessions)
    key = service.idempotency_key(appt.id, "reminder", "sms", appt.starts_at)
    async with sessions() as s:
        for _ in range(2):
            s.add(CommOutbox(clinic_id=clinic_id, patient_id=appt.patient_id, appointment_id=appt.id, message_type="reminder", channel="sms", appointment_starts_at=appt.starts_at, idempotency_key=key, status="pending", scheduled_for=datetime.now(UTC)))
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_concurrent_sync_of_the_same_appointment_queues_each_message_once(migrated):
    _url, _name, sessions = migrated
    _clinic, user_id, (appt,) = await seed(sessions)

    async def fire():
        async with sessions() as s:
            await service.sync_appointment(s, await s.get(Appointment, appt.id), user_id)
            await s.commit()

    await asyncio.gather(*(fire() for _ in range(6)))
    async with sessions() as s:
        found = (await s.scalars(select(CommOutbox).where(CommOutbox.appointment_id == appt.id))).all()
    assert sorted((r.message_type, r.channel) for r in found) == [("confirmation", "email"), ("confirmation", "sms"), ("reminder", "email"), ("reminder", "sms")]


@pytest.mark.asyncio
async def test_two_simultaneous_queue_runs_never_send_the_same_message_twice(migrated, monkeypatch):
    _url, _name, sessions = migrated
    first = datetime.combine(date.today() + timedelta(days=30), time(10), tzinfo=ZONE)
    _clinic, user_id, items = await seed(sessions, appointments=10, first_start=first)
    async with sessions() as s:
        for item in items:
            await service.sync_appointment(s, await s.get(Appointment, item.id), user_id)
        await s.commit()
    calls = []

    class Counting:
        name = "counting"

        async def send(self, channel, recipient, subject, body):
            calls.append((channel, subject, body))
            await asyncio.sleep(0.02)  # keep the row locked long enough for the other run to overlap
            return adapters.SendResult(provider_ref=f"c-{len(calls)}")

    monkeypatch.setattr(adapters, "get_adapter", Counting)
    now = (first - timedelta(minutes=1)).astimezone(UTC)  # 09:59 local: every message is due, no appointment has started

    async def run():
        async with sessions() as s:
            return await service.process_due(s, now=now)

    one, two = await asyncio.gather(run(), run())
    async with sessions() as s:
        statuses = [r.status for r in (await s.scalars(select(CommOutbox))).all()]
        attempts = [r.attempts for r in (await s.scalars(select(CommOutbox))).all()]
    assert statuses.count("sent") == 40 and set(statuses) == {"sent"} and max(attempts) == 1
    assert len(calls) == 40 and one["sent"] + two["sent"] == 40 and one["sent"] > 0 and two["sent"] > 0
