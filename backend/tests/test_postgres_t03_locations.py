"""PostgreSQL proof for T03 allowed locations: additive migration over a database populated at
t13_add_patient_financial_entries, data preserved, existing services become 'any', the CHECK constraint
holds, and the API on the migrated database keeps the old behaviour for existing services while
refusing a forbidden location for a restricted one."""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres
PASSWORD = "correct horse battery staple"
BASE_REVISION = "t13_add_patient_financial_entries"


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t03-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t03_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_t03_migration_preserves_data_defaults_to_any_and_keeps_api_behaviour(temp_database):
    url, name = temp_database
    base = await _run_alembic(url, BASE_REVISION)
    assert base.returncode == 0, base.stdout + base.stderr
    ids = {key: str(uuid4()) for key in ("clinic", "user", "patient", "room", "obt", "cons", "visit", "open_visit",
                                          "performed", "surface_only", "plan", "stage", "item", "entry")}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'T03 fictive', 'Europe/Bucharest', now(), now())", ids["clinic"])
        await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 't03-pg@example.com', $3, 'Manager Fictiv', 'clinic_manager', true, now(), now())", ids["user"], ids["clinic"], hash_password(PASSWORD))
        await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Ana', 'Fictiv', now(), now())", ids["patient"], ids["clinic"])
        await conn.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Cabinet', true)", ids["room"], ids["clinic"])
        for key, code in (("obt", "OBT"), ("cons", "CONS")):
            await conn.execute("INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, $3, lower($3), $3, 30, 250.00, 'RON', true, now(), now())", ids[key], ids["clinic"], code)
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, '2026-12-01T09:00:00Z', 30, 'completed', 'Tratament', now(), now())", ids["visit"], ids["clinic"], ids["patient"], ids["user"], ids["room"])
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, '2030-12-01T09:00:00Z', 30, 'scheduled', 'Tratament', now(), now())", ids["open_visit"], ids["clinic"], ids["patient"], ids["user"], ids["room"])
        await conn.execute("INSERT INTO performed_procedures (id, clinic_id, appointment_id, patient_id, procedure_catalog_id, executed_by_id, procedure_code, procedure_name, procedure_base_price, procedure_currency, procedure_default_duration, tooth_fdi, tooth_surface, status, current_price, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'OBT', 'OBT', 250.00, 'RON', 30, '16', 'occlusal', 'completed', 230.00, now(), now())", ids["performed"], ids["clinic"], ids["visit"], ids["patient"], ids["obt"], ids["user"])
        await conn.execute("INSERT INTO performed_procedures (id, clinic_id, appointment_id, patient_id, procedure_catalog_id, executed_by_id, procedure_code, procedure_name, procedure_base_price, procedure_currency, procedure_default_duration, tooth_fdi, tooth_surface, status, current_price, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'CONS', 'CONS', 250.00, 'RON', 30, NULL, 'buccal', 'completed', 250.00, now(), now())", ids["surface_only"], ids["clinic"], ids["visit"], ids["patient"], ids["cons"], ids["user"])
        await conn.execute("INSERT INTO treatment_plans (id, clinic_id, patient_id, title, status, version, created_by_id, created_at, updated_at) VALUES ($1, $2, $3, 'Plan existent', 'draft', 1, $4, now(), now())", ids["plan"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO treatment_plan_stages (id, clinic_id, plan_id, position, title, created_at, updated_at) VALUES ($1, $2, $3, 1, 'Etapa 1', now(), now())", ids["stage"], ids["clinic"], ids["plan"])
        await conn.execute("INSERT INTO treatment_plan_items (id, clinic_id, plan_id, stage_id, position, procedure_catalog_id, procedure_code, procedure_name, currency, estimated_price, estimated_duration_minutes, tooth_fdi, tooth_surface, priority, status, created_at, updated_at) VALUES ($1, $2, $3, $4, 1, $5, 'OBT', 'OBT', 'RON', 250.00, 30, '21', 'distal,mesial', 'normal', 'active', now(), now())", ids["item"], ids["clinic"], ids["plan"], ids["stage"], ids["obt"])
        await conn.execute("INSERT INTO patient_financial_entries (id, clinic_id, patient_id, appointment_id, kind, amount, currency, method, client_request_id, created_by_id, created_at) VALUES ($1, $2, $3, $4, 'payment', 100.00, 'RON', 'cash', $5, $6, now())", ids["entry"], ids["clinic"], ids["patient"], ids["visit"], str(uuid4()), ids["user"])
    finally:
        await conn.close()

    upgraded = await _run_alembic(url, "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        assert revision_is_in_head_lineage("t03_catalog_allowed_locations")
        assert revision_is_in_head_lineage(BASE_REVISION)
        rows = await conn.fetch("SELECT code, allowed_location, base_price, is_active FROM procedure_catalog ORDER BY code")
        assert [(row["code"], row["allowed_location"], str(row["base_price"]), row["is_active"]) for row in rows] == [
            ("CONS", "any", "250.00", True), ("OBT", "any", "250.00", True)]
        assert await conn.fetchval("SELECT tooth_fdi || ':' || tooth_surface || ':' || status || ':' || current_price FROM performed_procedures WHERE id = $1", ids["performed"]) == "16:occlusal:completed:230.00"
        assert await conn.fetchval("SELECT tooth_fdi IS NULL AND tooth_surface = 'buccal' FROM performed_procedures WHERE id = $1", ids["surface_only"])
        assert await conn.fetchval("SELECT tooth_fdi || ':' || tooth_surface FROM treatment_plan_items WHERE id = $1", ids["item"]) == "21:distal,mesial"
        assert await conn.fetchval("SELECT title FROM treatment_plans WHERE id = $1", ids["plan"]) == "Plan existent"
        assert await conn.fetchval("SELECT amount FROM patient_financial_entries WHERE id = $1", ids["entry"]) == 100
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE procedure_catalog SET allowed_location = 'quadrant' WHERE id = $1", ids["obt"])
        with pytest.raises(asyncpg.NotNullViolationError):
            await conn.execute("UPDATE procedure_catalog SET allowed_location = NULL WHERE id = $1", ids["obt"])
        legacy_insert = str(uuid4())
        await conn.execute("INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'RAW', 'raw', 'Raw', 10, 1.00, 'RON', true, now(), now())", legacy_insert, ids["clinic"])
        assert await conn.fetchval("SELECT allowed_location FROM procedure_catalog WHERE id = $1", legacy_insert) == "any"
    finally:
        await conn.close()

    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30) as client:
            login = await client.post("/api/auth/login", json={"email": "t03-pg@example.com", "password": PASSWORD})
            assert login.status_code == 200, login.text
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

            def create(catalog_id, tooth=None, surface=None):
                return client.post("/api/performed-procedures", headers=auth, json={
                    "appointment_id": ids["open_visit"], "procedure_catalog_id": catalog_id, "tooth_fdi": tooth, "tooth_surface": surface})

            # Existing services keep today's behaviour, including a surface without a tooth in T04.
            assert (await create(ids["cons"])).status_code == 201
            assert (await create(ids["cons"], None, "buccal")).status_code == 201
            assert (await create(ids["obt"], "16", "occlusal")).status_code == 201
            restricted = await client.patch(f"/api/procedures/{ids['obt']}", headers=auth, json={"allowed_location": "tooth_surface"})
            assert restricted.status_code == 200 and restricted.json()["allowed_location"] == "tooth_surface"
            refused = await create(ids["obt"], "16")
            assert refused.status_code == 422 and refused.json()["detail"] == "Service OBT requires an FDI tooth and at least one surface"
            # The stored record that predates the rule is still served unchanged.
            stored = await client.get(f"/api/performed-procedures/{ids['performed']}", headers=auth)
            assert stored.status_code == 200 and stored.json()["tooth_surface"] == "occlusal"
            added = await client.post(f"/api/treatment-plans/{ids['plan']}/stages/{ids['stage']}/items", headers=auth, json={"procedure_catalog_id": ids["obt"], "tooth_fdi": "17"})
            assert added.status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
