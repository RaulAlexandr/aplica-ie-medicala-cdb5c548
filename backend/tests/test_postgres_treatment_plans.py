"""PostgreSQL proof for T08: additive migration over existing data and the single-link invariant."""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head

pytestmark = pytest.mark.postgres
TIMEOUT = 10


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t08-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t08_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_t08_migration_preserves_t00_t07_data_and_enforces_link_uniqueness(temp_database):
    url, name = temp_database
    base = await _run_alembic(url, "t06_add_persisted_odontogram")
    assert base.returncode == 0, base.stdout + base.stderr
    ids = {key: str(uuid4()) for key in ("clinic", "user", "patient", "room", "catalog", "visit", "performed", "note", "finding")}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'T08 fictive', 'Europe/Bucharest', now(), now())", ids["clinic"])
        await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 't08@example.com', 'x', 'Doctor Fictiv', 'doctor', true, now(), now())", ids["user"], ids["clinic"])
        await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Ana', 'Fictiv', now(), now())", ids["patient"], ids["clinic"])
        await conn.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Cabinet', true)", ids["room"], ids["clinic"])
        await conn.execute("INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'OBT', 'obt', 'Obturație', 30, 250.00, 'RON', true, now(), now())", ids["catalog"], ids["clinic"])
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, '2026-12-01T09:00:00Z', 30, 'completed', 'Tratament', now(), now())", ids["visit"], ids["clinic"], ids["patient"], ids["user"], ids["room"])
        await conn.execute("INSERT INTO performed_procedures (id, clinic_id, appointment_id, patient_id, procedure_catalog_id, executed_by_id, procedure_code, procedure_name, procedure_base_price, procedure_currency, procedure_default_duration, tooth_fdi, status, current_price, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'OBT', 'Obturație', 250.00, 'RON', 30, '16', 'completed', 250.00, now(), now())", ids["performed"], ids["clinic"], ids["visit"], ids["patient"], ids["catalog"], ids["user"])
        await conn.execute("INSERT INTO clinical_notes (id, clinic_id, patient_id, author_id, note_type, content, status, is_alert, created_at, updated_at) VALUES ($1, $2, $3, $4, 'treatment_plan', 'Plan vechi ca notă', 'finalized', false, now(), now())", ids["note"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO odontogram_findings (id, clinic_id, patient_id, tooth_fdi, kind, description, surfaces, status, observed_at, created_at, updated_at, author_id) VALUES ($1, $2, $3, '16', 'caries', 'Fictiv', '[\"occlusal\"]', 'finalized', now(), now(), now(), $4)", ids["finding"], ids["clinic"], ids["patient"], ids["user"])
    finally:
        await conn.close()

    upgraded = await _run_alembic(url, "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head() == "t08_add_treatment_plans"
        assert await conn.fetchval("SELECT content FROM clinical_notes WHERE id = $1", ids["note"]) == "Plan vechi ca notă"
        assert await conn.fetchval("SELECT status FROM performed_procedures WHERE id = $1", ids["performed"]) == "completed"
        assert await conn.fetchval("SELECT description FROM odontogram_findings WHERE id = $1", ids["finding"]) == "Fictiv"
        assert await conn.fetchval("SELECT count(*) FROM treatment_plans") == 0, "treatment_plan notes are not auto-migrated"

        plan_ids = [str(uuid4()), str(uuid4())]
        for plan_id in plan_ids:
            stage_id = str(uuid4())
            await conn.execute("INSERT INTO treatment_plans (id, clinic_id, patient_id, title, status, version, accepted_version, created_by_id, created_at, updated_at) VALUES ($1, $2, $3, 'Plan', 'in_progress', 1, 1, $4, now(), now())", plan_id, ids["clinic"], ids["patient"], ids["user"])
            await conn.execute("INSERT INTO treatment_plan_stages (id, clinic_id, plan_id, position, title, created_at, updated_at) VALUES ($1, $2, $3, 1, 'E1', now(), now())", stage_id, ids["clinic"], plan_id)
            insert_item = conn.execute(
                "INSERT INTO treatment_plan_items (id, clinic_id, plan_id, stage_id, position, procedure_catalog_id, procedure_code, procedure_name, currency, estimated_price, estimated_duration_minutes, tooth_fdi, performed_procedure_id, created_at, updated_at) VALUES ($1, $2, $3, $4, 1, $5, 'OBT', 'Obturație', 'RON', 250.00, 30, '16', $6, now(), now())",
                str(uuid4()), ids["clinic"], plan_id, stage_id, ids["catalog"], ids["performed"])
            if plan_id == plan_ids[0]:
                await insert_item
            else:
                with pytest.raises(asyncpg.UniqueViolationError):
                    await insert_item
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE treatment_plans SET status = 'signed' WHERE id = $1", plan_ids[0])
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE treatment_plan_items SET estimated_price = -1")
        assert await conn.fetchval("SELECT count(*) FROM treatment_plan_items WHERE performed_procedure_id = $1", ids["performed"]) == 1
    finally:
        await conn.close()


async def _wait_for_lock_wait(url: str, name: str) -> None:
    deadline = asyncio.get_running_loop().time() + TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        conn = await asyncpg.connect(_dsn(url, "postgres"))
        try:
            waiting = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = $1 AND wait_event_type = 'Lock')", name)
        finally:
            await conn.close()
        if waiting:
            return
        await asyncio.sleep(0.025)
    raise AssertionError("Timed out waiting for the HTTP request to block on the unique index")


async def test_concurrent_links_of_one_performed_procedure_keep_a_single_fulfilment(temp_database):
    url, name = temp_database
    migrated = await _run_alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    blocker = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=TIMEOUT * 2) as client:
            reg = await client.post("/api/auth/register", json={"clinic_name": "T08 PG", "full_name": "Manager Fictiv",
                                                               "email": f"t08-{uuid4().hex[:8]}@example.com",
                                                               "password": "correct horse battery staple"})
            assert reg.status_code == 201, reg.text
            auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}
            me = (await client.get("/api/auth/me", headers=auth)).json()
            room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet PG"})).json()["id"]
            patient = (await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "PG"})).json()["id"]
            catalog = (await client.post("/api/procedures", headers=auth, json={
                "code": "PG1", "name": "Serviciu PG", "base_price": "120.00", "default_duration_minutes": 30})).json()["id"]
            visit = await client.post("/api/appointments", headers=auth, json={
                "patient_id": patient, "doctor_id": me["id"], "room_id": room, "starts_at": "2026-12-05T09:00:00+00:00",
                "duration_minutes": 60, "appointment_type": "Tratament"})
            assert visit.status_code == 201, visit.text

            async def performed() -> str:
                response = await client.post("/api/performed-procedures", headers=auth, json={
                    "appointment_id": visit.json()["id"], "procedure_catalog_id": catalog, "tooth_fdi": "11"})
                assert response.status_code == 201, response.text
                return response.json()["id"]

            body = {"title": "Plan PG", "stages": [{"title": "E1", "items": [
                {"procedure_catalog_id": catalog, "tooth_fdi": "11"}, {"procedure_catalog_id": catalog, "tooth_fdi": "11"}]}]}
            plans = []
            for _ in range(2):
                plan = (await client.post(f"/api/treatment-plans/patients/{patient}", headers=auth, json=body)).json()
                accepted = await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord PG"})
                assert accepted.status_code == 200, accepted.text
                plans.append([item["id"] for item in accepted.json()["stages"][0]["items"]])

            # Controlled point: another transaction holds the unique entry uncommitted. The HTTP
            # request passes its pre-check, blocks on the index, then gets a 409 after commit.
            first = await performed()
            blocker = await asyncpg.connect(_dsn(url, name))
            transaction = blocker.transaction()
            await transaction.start()
            await blocker.execute("UPDATE treatment_plan_items SET performed_procedure_id = $1 WHERE id = $2", first, plans[0][0])
            request = asyncio.create_task(client.put(f"/api/treatment-plans/items/{plans[1][0]}/performed-procedure",
                                                     headers=auth, json={"performed_procedure_id": first}))
            await _wait_for_lock_wait(url, name)
            assert not request.done()
            await transaction.commit()
            response = await asyncio.wait_for(request, TIMEOUT)
            assert response.status_code == 409, response.text
            await blocker.close()
            blocker = None

            # Uncontrolled race through the API on separate sessions: exactly one wins.
            second = await performed()
            results = await asyncio.wait_for(asyncio.gather(
                client.put(f"/api/treatment-plans/items/{plans[0][1]}/performed-procedure", headers=auth, json={"performed_procedure_id": second}),
                client.put(f"/api/treatment-plans/items/{plans[1][1]}/performed-procedure", headers=auth, json={"performed_procedure_id": second}),
            ), TIMEOUT * 2)
            assert sorted(result.status_code for result in results) == [200, 409], [r.text for r in results]

        check = await asyncpg.connect(_dsn(url, name))
        try:
            for performed_id in (first, second):
                assert await check.fetchval(
                    "SELECT count(*) FROM treatment_plan_items WHERE performed_procedure_id = $1", performed_id) == 1
            assert await check.fetchval("SELECT count(*) FROM treatment_plan_events WHERE action = 'performed_linked'") == 1
            assert await check.fetchval("SELECT count(*) FROM procedure_execution_segments") == 0
        finally:
            await check.close()
    finally:
        if blocker is not None:
            await blocker.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
