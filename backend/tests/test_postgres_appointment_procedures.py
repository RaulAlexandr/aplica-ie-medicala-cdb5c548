"""PostgreSQL proof for A1 migration backfill, partial uniqueness and optimistic concurrency."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.appointment_procedures.models import AppointmentProcedure
from app.database import get_db

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, revision: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "a1-postgres-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", revision], cwd=os.path.dirname(__file__) + "/..",
                                   env=env, capture_output=True, text=True, check=False)


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_a1_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_migration_backfills_existing_appointments(temp_database):
    url, name = temp_database
    migrated = await _alembic(url, "t16_waitlist_recalls")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    ids = {key: str(uuid4()) for key in ("clinic", "doctor", "patient", "room", "visit")}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'A1 test', 'Europe/Bucharest', now(), now())", ids["clinic"])
        await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 'a1-doctor@test.invalid', 'x', 'Doctor A1', 'doctor', true, now(), now())", ids["doctor"], ids["clinic"])
        await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Ana', 'Test', now(), now())", ids["patient"], ids["clinic"])
        await conn.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Cabinet A1', true)", ids["room"], ids["clinic"])
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, now(), 30, 'scheduled', 'Consultație', now(), now())", ids["visit"], ids["clinic"], ids["patient"], ids["doctor"], ids["room"])
    finally:
        await conn.close()
    migrated = await _alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        row = await conn.fetchrow("SELECT clinic_id, appointment_id, revision FROM appointment_procedure_revisions WHERE appointment_id = $1", ids["visit"])
        assert row and str(row["clinic_id"]) == ids["clinic"] and row["revision"] == 0
    finally:
        await conn.close()


async def test_simultaneous_replacements_and_partial_unique_index(temp_database):
    url, _ = temp_database
    migrated = await _alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    from app.main import app
    engine = create_async_engine(url, pool_size=5)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registered = await client.post("/api/auth/register", json={"clinic_name": "A1 PG Fictivă", "full_name": "Doctor PG", "email": f"{uuid4().hex}@example.com", "password": "correct horse battery staple"})
            assert registered.status_code == 201, registered.text
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            me = (await client.get("/api/auth/me", headers=headers)).json()
            room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet"})).json()
            patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Ana", "last_name": "PG"})).json()
            appointment = (await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": me["id"], "room_id": room["id"], "starts_at": "2026-12-05T09:00:00+00:00", "duration_minutes": 30, "appointment_type": "Test"})).json()
            procedure = (await client.post("/api/procedures", headers=headers, json={"code": "A1", "name": "Procedură A1", "base_price": "10.00", "default_duration_minutes": 10})).json()
            body = {"expected_revision": 0, "procedures": [{"procedure_catalog_id": procedure["id"], "quantity": 1}]}
            responses = await asyncio.gather(*[client.put(f"/api/appointment-procedures/{appointment['id']}", headers=headers, json=body) for _ in range(2)])
            assert sorted(response.status_code for response in responses) == [200, 409]
            async with sessions() as session:
                session.add(AppointmentProcedure(clinic_id=UUID(me["clinic_id"]), appointment_id=UUID(appointment["id"]),
                    procedure_catalog_id=UUID(procedure["id"]), quantity=1, added_by_id=UUID(me["id"])))
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
