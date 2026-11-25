"""PostgreSQL proof for T23: unique cycle number under concurrency, one use per sterilized pack, row locks."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import User, get_db
from app.main import app
from app.sterilization.models import SterilizationCycle, SterilizationPackEvent

pytestmark = pytest.mark.postgres
API = "/api/sterilization"
PASSWORD = "correct horse battery staple"


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, target: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t23-test-only", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)


@pytest.fixture
async def postgres_ctx():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t23_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        migrated = await _alembic(url, "t23_sterilization")
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


def barrier_for(sessions, parties=2):
    ready, lock, count = asyncio.Event(), asyncio.Lock(), 0

    async def override():
        nonlocal count
        async with sessions() as session:
            async with lock:
                count += 1
                if count == parties:
                    ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            yield session

    return override


async def world(client, sessions, packs=2):
    registered = await client.post("/api/auth/register", json={"clinic_name": "T23 fictiv", "full_name": "Manager fictiv", "email": f"t23-{uuid4().hex}@example.com", "password": PASSWORD})
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=headers)).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet T23"})).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    equipment = (await client.post("/api/equipment", headers=headers, json={"name": "Autoclav T23", "equipment_type": "sterilizator", "responsible_id": me["id"]})).json()
    set_type = (await client.post(f"{API}/set-types", headers=headers, json={"name": "Set T23"})).json()
    pack_ids = [(await client.post(f"{API}/packs", headers=headers, json={"set_type_id": set_type["id"], "code": f"T23-{index}"})).json()["id"] for index in range(packs)]
    async with sessions() as session:
        assistant = User(clinic_id=me["clinic_id"], email=f"assistant-{uuid4().hex}@example.com", password_hash="unused", full_name="Asistent", role="assistant", is_active=True)
        session.add(assistant)
        await session.commit()
        assistant_headers = {"Authorization": f"Bearer {create_access_token(assistant)}"}
    return headers, assistant_headers, me, room, patient, equipment, pack_ids


async def appointment(client, headers, me, room, patient, day):
    response = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient["id"], "doctor_id": me["id"], "room_id": room["id"], "starts_at": f"2030-05-{day:02d}T09:00:00+00:00",
        "duration_minutes": 30, "appointment_type": "Control"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def approved(client, headers, assistant, equipment_id, pack_ids):
    cycle = await client.post(f"{API}/cycles", headers=headers, json={"equipment_id": equipment_id, "program": "134", "pack_ids": pack_ids})
    assert cycle.status_code == 201, cycle.text
    cycle_id = cycle.json()["id"]
    finished = await client.post(f"{API}/cycles/{cycle_id}/finish", headers=headers, json={
        "temperature_c": "134.0", "pressure_kpa": "210", "hold_minutes": 18, "chemical_result": "pass"})
    assert finished.status_code == 200, finished.text
    decision = await client.post(f"{API}/cycles/{cycle_id}/decision", headers=assistant, json={"decision": "approved"})
    assert decision.status_code == 200, decision.text
    return cycle_id


@pytest.mark.asyncio
async def test_postgres_duplicate_cycle_number_under_concurrency(postgres_ctx):
    client, sessions = postgres_ctx
    headers, _assistant, _me, _room, _patient, equipment, pack_ids = await world(client, sessions)
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/cycles", headers=headers, json={"equipment_id": equipment["id"], "program": "134", "pack_ids": [pack_id], "cycle_number": 5})
            for pack_id in pack_ids]), 30)
    finally:
        app.dependency_overrides.clear()
    assert sorted(result.status_code for result in results) == [201, 409], [r.text for r in results]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SterilizationCycle).where(SterilizationCycle.cycle_number == 5)) == 1


@pytest.mark.asyncio
async def test_postgres_automatic_numbers_stay_distinct_under_concurrency(postgres_ctx):
    client, sessions = postgres_ctx
    headers, _assistant, _me, _room, _patient, equipment, pack_ids = await world(client, sessions)
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/cycles", headers=headers, json={"equipment_id": equipment["id"], "program": "134", "pack_ids": [pack_id]})
            for pack_id in pack_ids]), 30)
    finally:
        app.dependency_overrides.clear()
    assert [result.status_code for result in results] == [201, 201], [r.text for r in results]
    assert sorted(result.json()["cycle_number"] for result in results) == [1, 2]


@pytest.mark.asyncio
async def test_postgres_same_pack_used_concurrently_links_once(postgres_ctx):
    client, sessions = postgres_ctx
    headers, assistant, me, room, patient, equipment, pack_ids = await world(client, sessions, packs=1)
    await approved(client, headers, assistant, equipment["id"], pack_ids)
    first = await appointment(client, headers, me, room, patient, 6)
    second = await appointment(client, headers, me, room, patient, 7)
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/packs/{pack_ids[0]}/use", headers=headers, json={"appointment_id": appointment_id}) for appointment_id in (first, second)]), 30)
    finally:
        app.dependency_overrides.clear()
    assert sorted(result.status_code for result in results) == [201, 409], [r.text for r in results]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SterilizationPackEvent).where(SterilizationPackEvent.event_type == "used")) == 1


@pytest.mark.asyncio
async def test_postgres_double_click_on_same_visit_is_one_link(postgres_ctx):
    client, sessions = postgres_ctx
    headers, assistant, me, room, patient, equipment, pack_ids = await world(client, sessions, packs=1)
    await approved(client, headers, assistant, equipment["id"], pack_ids)
    visit = await appointment(client, headers, me, room, patient, 8)
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/packs/{pack_ids[0]}/use", headers=headers, json={"appointment_id": visit}) for _ in range(2)]), 30)
    finally:
        app.dependency_overrides.clear()
    assert sorted(result.status_code for result in results) == [200, 201], [r.text for r in results]
    assert results[0].json()["event_id"] == results[1].json()["event_id"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SterilizationPackEvent).where(SterilizationPackEvent.event_type == "used")) == 1


@pytest.mark.asyncio
async def test_postgres_pack_cannot_enter_two_cycles_concurrently(postgres_ctx):
    client, sessions = postgres_ctx
    headers, _assistant, _me, _room, _patient, equipment, pack_ids = await world(client, sessions, packs=1)
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/cycles", headers=headers, json={"equipment_id": equipment["id"], "program": "134", "pack_ids": pack_ids}) for _ in range(2)]), 30)
    finally:
        app.dependency_overrides.clear()
    assert sorted(result.status_code for result in results) == [201, 409], [r.text for r in results]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SterilizationPackEvent).where(SterilizationPackEvent.event_type == "entered_cycle")) == 1


@pytest.mark.asyncio
async def test_postgres_two_simultaneous_decisions_apply_once(postgres_ctx):
    client, sessions = postgres_ctx
    headers, assistant, _me, _room, _patient, equipment, pack_ids = await world(client, sessions, packs=1)
    cycle = (await client.post(f"{API}/cycles", headers=headers, json={"equipment_id": equipment["id"], "program": "134", "pack_ids": pack_ids})).json()
    await client.post(f"{API}/cycles/{cycle['id']}/finish", headers=headers, json={"temperature_c": "134", "pressure_kpa": "210", "hold_minutes": 18, "chemical_result": "pass"})
    app.dependency_overrides[get_db] = barrier_for(sessions)
    try:
        results = await asyncio.wait_for(asyncio.gather(*[
            client.post(f"{API}/cycles/{cycle['id']}/decision", headers=assistant, json={"decision": decision}) for decision in ("approved", "rejected")]), 30)
    finally:
        app.dependency_overrides.clear()
    assert sorted(result.status_code for result in results) == [200, 409], [r.text for r in results]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SterilizationPackEvent).where(SterilizationPackEvent.event_type.in_(("sterilized", "cycle_rejected")))) == 1
