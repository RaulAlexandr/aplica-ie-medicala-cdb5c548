import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.operations.retention import cleanup_security_history

pytestmark = pytest.mark.postgres


def dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


@pytest.fixture
async def pg_security_client():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t34_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t34-security-test", "ENVIRONMENT": "test"}
    migrated = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    previous = app.dependency_overrides.copy()

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60) as client:
            yield client, url
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
        admin = await asyncpg.connect(dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_concurrent_failed_logins_are_serialized_and_access_log_is_immutable(pg_security_client):
    client, url = pg_security_client
    email = f"t34-{uuid4().hex[:10]}@example.com"
    registered = await client.post("/api/auth/register", json={"clinic_name": "T34", "full_name": "Manager",
        "email": email, "password": "correct horse battery staple"})
    assert registered.status_code == 201
    failures = await asyncio.gather(*(client.post("/api/auth/login", json={"email": email, "password": "wrong password"}) for _ in range(7)))
    assert all(response.status_code == 401 and response.json() == {"detail": "Invalid email or password"} for response in failures)
    blocked = await client.post("/api/auth/login", json={"email": email, "password": "correct horse battery staple"})
    assert blocked.status_code == 401 and blocked.json() == failures[0].json()
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Fictiv", "last_name": "Test"})
    read = await client.get(f"/api/patients/{patient.json()['id']}", headers=headers)
    assert read.status_code == 200
    connection = await asyncpg.connect(dsn(url, url.rsplit("/", 1)[-1]))
    try:
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            async with connection.transaction():
                await connection.execute("DELETE FROM patient_access_log")
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_bounded_retention_is_the_only_security_history_delete_path(pg_security_client):
    _, url = pg_security_client
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        old = datetime.now(UTC) - timedelta(days=400)
        from app.database import LoginAttempt

        db.add(LoginAttempt(email=f"expired-{uuid4().hex}@example.test", attempted_at=old, succeeded=False))
        await db.commit()
        result = await cleanup_security_history(db, now=datetime.now(UTC), login_days=90, access_days=365, dry_run=False)
        assert result["deleted"]["login_attempts"] == 1
    await engine.dispose()
