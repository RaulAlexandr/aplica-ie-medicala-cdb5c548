"""PostgreSQL boundary-overlap and row-lock proof for T21 equipment downtime."""
import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Appointment, get_db
from app.equipment.models import EquipmentUnavailability
from app.main import app

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, target: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t21-test-only", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)


@pytest.fixture
async def postgres_ctx():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t21_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        migrated = await _alembic(url, "t21_equipment")
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def override():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        if "engine" in locals():
            await engine.dispose()
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def setup_equipment(client):
    registered = await client.post("/api/auth/register", json={"clinic_name": "T21 fictiv", "full_name": "Manager fictiv", "email": f"t21-{uuid4().hex}@example.com", "password": "correct horse battery staple"})
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    identity = (await client.get("/api/auth/me", headers=headers)).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet T21"})).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = (await client.post("/api/equipment", headers=headers, json={"name": "Unit T21", "equipment_type": "unit", "room_id": room["id"], "responsible_id": identity["id"]})).json()
    return headers, identity, room, patient, equipment


@pytest.mark.asyncio
async def test_postgres_half_open_overlap_and_two_simultaneous_closures(postgres_ctx):
    client, sessions = postgres_ctx
    headers, identity, room, patient, equipment = await setup_equipment(client)
    starts = datetime.now(UTC) + timedelta(days=5)
    ends = starts + timedelta(hours=1)
    async with sessions() as session:
        for appt_start in (starts - timedelta(minutes=30), starts + timedelta(minutes=30), ends):
            session.add(Appointment(clinic_id=identity["clinic_id"], patient_id=patient["id"], doctor_id=identity["id"], room_id=room["id"],
                starts_at=appt_start, ends_at=appt_start + timedelta(minutes=30), duration_minutes=30, status="scheduled", appointment_type="Control"))
        await session.commit()
    reported = await client.post(f"/api/equipment/{equipment['id']}/unavailabilities", headers=headers, json={"reason": "Defecțiune fictivă", "starts_at": starts.isoformat(), "ends_at": ends.isoformat()})
    assert reported.status_code == 201
    assert len(reported.json()["affected_appointments"]) == 1, "touching interval boundaries must not count as overlap"
    outage_id = reported.json()["id"]

    ready = asyncio.Event()
    lock = asyncio.Lock()
    count = 0
    async def barrier_override():
        nonlocal count
        async with sessions() as session:
            async with lock:
                count += 1
                if count == 2:
                    ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            yield session
    app.dependency_overrides[get_db] = barrier_override
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"/api/equipment/{equipment['id']}/unavailabilities/{outage_id}/close", headers=headers)
            for _ in range(2)
        ]), 20)
        assert sorted(result.status_code for result in results) == [200, 409]
    finally:
        app.dependency_overrides.clear()
    async with sessions() as session:
        row = await session.scalar(select(EquipmentUnavailability).where(EquipmentUnavailability.id == outage_id))
        assert row.closed_at is not None and row.closed_by_id is not None
