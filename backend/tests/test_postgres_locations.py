"""PostgreSQL constraints for T32B sites."""
import asyncio
import os
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _drop_database(source: str, name: str) -> None:
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgres_populated_w2_migration_backfills_all_clinic_assets():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_locations_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    target = source.rsplit("/", 1)[0] + f"/{name}"
    env = {**os.environ, "DATABASE_URL": target, "JWT_SECRET": "locations-migration-test-secret", "ENVIRONMENT": "test"}
    try:
        initial = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "w2_merge_a1"], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)
        assert initial.returncode == 0, initial.stderr
        conn = await asyncpg.connect(_dsn(target, name))
        clinic_id, user_id, room_id, equipment_id, stock_location_id = [uuid4() for _ in range(5)]
        now = datetime.now(UTC)
        await conn.execute("INSERT INTO clinics (id,name,timezone,created_at,updated_at) VALUES ($1,'Legacy','Europe/Bucharest',$2,$2)", clinic_id, now)
        await conn.execute("INSERT INTO users (id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES ($1,$2,$3,'hash','Legacy manager','clinic_manager',true,$4,$4)", user_id, clinic_id, f"{user_id}@example.com", now)
        await conn.execute("INSERT INTO rooms (id,clinic_id,name,is_active) VALUES ($1,$2,'Legacy room',true)", room_id, clinic_id)
        await conn.execute("INSERT INTO equipment (id,clinic_id,name,equipment_type,responsible_id,state,is_active,created_by_id,created_at,updated_at) VALUES ($1,$2,'Legacy chair','chair',$3,'available',true,$3,$4,$4)", equipment_id, clinic_id, user_id, now)
        await conn.execute("INSERT INTO inventory_locations (id,clinic_id,name,is_active) VALUES ($1,$2,'Legacy stock',true)", stock_location_id, clinic_id)
        await conn.close()
        upgraded = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)
        assert upgraded.returncode == 0, upgraded.stderr
        conn = await asyncpg.connect(_dsn(target, name))
        principal = await conn.fetchrow("SELECT id FROM clinic_locations WHERE clinic_id=$1 AND name='Principal'", clinic_id)
        assert principal is not None
        assert await conn.fetchval("SELECT location_id FROM rooms WHERE id=$1", room_id) == principal["id"]
        assert await conn.fetchval("SELECT location_id FROM equipment WHERE id=$1", equipment_id) == principal["id"]
        assert await conn.fetchval("SELECT site_id FROM inventory_locations WHERE id=$1", stock_location_id) == principal["id"]
        assert await conn.fetchval("SELECT default_location_id FROM users WHERE id=$1", user_id) == principal["id"]
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        await conn.close()
    finally:
        await _drop_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgres_location_name_is_unique_per_clinic():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registration = await client.post("/api/auth/register", json={
                "clinic_name": f"Sites {uuid4().hex[:8]}", "full_name": "Manager",
                "email": f"sites-{uuid4().hex}@example.com", "password": "correct horse battery staple",
            })
            assert registration.status_code == 201
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
            name = f"Branch {uuid4().hex[:8]}"
            assert (await client.post("/api/locations", headers=headers, json={"name": name})).status_code == 201
            duplicate = await client.post("/api/locations", headers=headers, json={"name": name})
            assert duplicate.status_code == 409
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
