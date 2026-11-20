"""PostgreSQL proof for the T01 lock order (Sol review point 1).

Every path that touches refresh tokens locks the user row first and the refresh-token rows second:
refresh rotation, login, logout, deactivation and reactivation. The scenarios below hold the refresh/login
request inside its critical section (after its locks, before it inserts the successor token) with a barrier,
start a deactivation from a second session, wait until PostgreSQL reports the deactivation blocked on a
lock, then release. Previously refresh locked the token, deactivation locked the user and waited on the
token, and the successor INSERT needed a key-share lock on the user: a deadlock that could roll back the
deactivation. Now both requests finish, and after both commits the deactivated user holds no usable
refresh token; tokens issued in the race also stay invalid after a reactivation.
"""

import asyncio
import os
import subprocess
from urllib.parse import parse_qs, urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.auth.router as auth_router
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.postgres
TIMEOUT = 10
PASSWORD = "correct horse battery staple"


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t01-lock-order-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
async def pg_client():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t01_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    engine = None
    previous = app.dependency_overrides.copy()
    try:
        migrated = await _run_alembic(url, "head")
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        engine = create_async_engine(url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def override():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=TIMEOUT * 3) as client:
            yield client, url, name
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        if engine is not None:
            await engine.dispose()
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _wait_for_lock_wait(name: str, url: str) -> None:
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
    raise AssertionError("Timed out waiting for the deactivation to block on a row lock")


async def _setup(client) -> tuple[dict, str, str]:
    manager = await client.post("/api/auth/register", json={
        "clinic_name": "T01 PG", "full_name": "Manager Fictiv", "email": f"t01-pg-{uuid4().hex[:8]}@example.com", "password": PASSWORD})
    assert manager.status_code == 201, manager.text
    headers = {"Authorization": f"Bearer {manager.json()['access_token']}"}
    email = f"t01-pg-member-{uuid4().hex[:8]}@example.com"
    invitation = await client.post("/api/staff/invitations", headers=headers, json={"email": email, "full_name": "Asistent Fictiv", "role": "assistant"})
    assert invitation.status_code == 201, invitation.text
    token = parse_qs(urlparse(invitation.json()["invitation_url"]).query)["token"][0]
    accepted = await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return headers, accepted.json()["id"], email


def _hold_inside_critical_section(monkeypatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Pause the request after its locks are taken, right before it inserts the successor refresh token."""
    entered, release = asyncio.Event(), asyncio.Event()
    original = auth_router.issue_refresh_token

    async def held(db, user):
        entered.set()
        await asyncio.wait_for(release.wait(), TIMEOUT)
        return await original(db, user)

    monkeypatch.setattr(auth_router, "issue_refresh_token", held)
    return entered, release


async def _usable_refresh_tokens(url: str, name: str, user_id: str) -> int:
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM refresh_tokens WHERE user_id = $1 AND revoked_at IS NULL AND expires_at > now()", user_id)
    finally:
        await conn.close()


async def _race(client, url, name, monkeypatch, first_request, manager_headers, member_id):
    entered, release = _hold_inside_critical_section(monkeypatch)
    held = asyncio.create_task(first_request())
    deactivation = None
    try:
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        deactivation = asyncio.create_task(client.post(f"/api/staff/{member_id}/deactivate", headers=manager_headers))
        await _wait_for_lock_wait(name, url)
    finally:
        release.set()
    tasks = [task for task in (held, deactivation) if task is not None]
    _done, pending = await asyncio.wait(tasks, timeout=TIMEOUT)
    for task in pending:
        task.cancel()
    assert not pending, "a request did not finish: possible lock wait cycle"
    return held.result(), deactivation.result()


async def test_refresh_concurrent_with_deactivation_has_no_deadlock_and_leaves_no_valid_refresh(pg_client, monkeypatch):
    client, url, name = pg_client
    manager_headers, member_id, email = await _setup(client)
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    old_refresh = login.json()["refresh_token"]

    refreshed, deactivated = await _race(
        client, url, name, monkeypatch,
        lambda: client.post("/api/auth/refresh", json={"refresh_token": old_refresh}), manager_headers, member_id)

    # No deadlock: the deactivation committed (and the refresh either finished first or was refused cleanly).
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["staff"]["is_active"] is False
    assert refreshed.status_code in (200, 401), refreshed.text
    # After both commits the deactivated user holds no usable refresh token, including the successor.
    assert await _usable_refresh_tokens(url, name, member_id) == 0
    for token in (old_refresh, refreshed.json()["refresh_token"] if refreshed.status_code == 200 else None):
        if token:
            assert (await client.post("/api/auth/refresh", json={"refresh_token": token})).status_code == 401
    # A reactivation does not revive anything issued during the race.
    assert (await client.post(f"/api/staff/{member_id}/reactivate", headers=manager_headers)).status_code == 200
    if refreshed.status_code == 200:
        racing = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
        assert (await client.get("/api/auth/me", headers=racing)).status_code == 401
        assert (await client.post("/api/auth/refresh", json={"refresh_token": refreshed.json()["refresh_token"]})).status_code == 401
    assert await _usable_refresh_tokens(url, name, member_id) == 0
    assert (await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200


async def test_login_concurrent_with_deactivation_cannot_outlive_a_reactivation(pg_client, monkeypatch):
    client, url, name = pg_client
    manager_headers, member_id, email = await _setup(client)

    logged_in, deactivated = await _race(
        client, url, name, monkeypatch,
        lambda: client.post("/api/auth/login", json={"email": email, "password": PASSWORD}), manager_headers, member_id)

    assert deactivated.status_code == 200, deactivated.text
    assert logged_in.status_code in (200, 401), logged_in.text
    assert await _usable_refresh_tokens(url, name, member_id) == 0
    assert (await client.post(f"/api/staff/{member_id}/reactivate", headers=manager_headers)).status_code == 200
    if logged_in.status_code == 200:
        racing = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
        assert (await client.get("/api/auth/me", headers=racing)).status_code == 401
        assert (await client.post("/api/auth/refresh", json={"refresh_token": logged_in.json()["refresh_token"]})).status_code == 401
