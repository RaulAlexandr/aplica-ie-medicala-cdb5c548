"""T19 on PostgreSQL: populated migration and real concurrency for clock-in and overlapping shifts."""

import asyncio
import os
import subprocess
from datetime import timedelta
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import User, get_db, utcnow
from app.main import app
from app.team.models import TeamAttendance, TeamLeaveRequest, TeamShift
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres
PASSWORD = "correct horse battery staple"


def source_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    return url


def dsn(url: str, name: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{name}"))


async def alembic(url: str, target: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t19-ci", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True)


@pytest.fixture
async def ctx():
    engine = create_async_engine(source_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions, override
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def clinic(client):
    registered = await client.post("/api/auth/register", json={"clinic_name": "T19 fictiv", "full_name": "Manager Fictiv", "email": f"{uuid4().hex}@example.com", "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T19"})).json()
    return auth, me, room["id"]


async def person(sessions, clinic_id, role):
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{uuid4().hex}@example.com", password_hash="unused", full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return {"Authorization": f"Bearer {create_access_token(user)}"}, str(user.id)


def barrier(sessions, parties: int):
    """Each request gets its own session and waits until all of them have one."""
    ready, lock, seen = asyncio.Event(), asyncio.Lock(), []

    async def override():
        async with sessions() as session:
            async with lock:
                seen.append(id(session))
                if len(seen) == parties:
                    ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            yield session

    return override, seen


@pytest.mark.asyncio
async def test_t19_migration_preserves_populated_t16_database():
    source = source_url()
    name = "t19_" + uuid4().hex[:12]
    admin = await asyncpg.connect(dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        base = await alembic(url, "t16_waitlist_recalls")
        assert base.returncode == 0, base.stdout + base.stderr
        clinic_id, user_id = str(uuid4()), str(uuid4())
        connection = await asyncpg.connect(dsn(url, name))
        try:
            await connection.execute("INSERT INTO clinics(id,name,timezone,created_at,updated_at) VALUES($1,'T19 fictiv','Europe/Bucharest',now(),now())", clinic_id)
            await connection.execute("INSERT INTO users(id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES($1,$2,$3,'x','Manager fictiv','clinic_manager',true,now(),now())", user_id, clinic_id, f"{user_id}@example.com")
        finally:
            await connection.close()
        upgraded = await alembic(url, "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        assert revision_is_in_head_lineage("t19_team_shifts")
        connection = await asyncpg.connect(dsn(url, name))
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
            assert await connection.fetchval("SELECT count(*) FROM users") == 1
            assert await connection.fetchval("SELECT count(*) FROM team_shifts") == 0
            constraints = {row[0] for row in await connection.fetch("SELECT conname FROM pg_constraint WHERE contype = 'x'")}
            assert {"ex_team_shift_person", "ex_team_shift_room_post", "ex_team_leave_approved"} <= constraints
        finally:
            await connection.close()
    finally:
        admin = await asyncpg.connect(dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_simultaneous_clock_in_creates_one_open_session(ctx):
    client, sessions, _ = ctx
    _auth, me, _room = await clinic(client)
    auth, user_id = await person(sessions, me["clinic_id"], "assistant")
    override, seen = barrier(sessions, 4)
    app.dependency_overrides[get_db] = override
    results = await asyncio.wait_for(asyncio.gather(*[client.post("/api/team/attendance/clock-in", headers=auth) for _ in range(4)]), 20)
    assert sorted(item.status_code for item in results) == [200, 200, 200, 201]
    assert len(set(seen)) == 4 and len({item.json()["id"] for item in results}) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TeamAttendance).where(TeamAttendance.user_id == UUID(user_id))) == 1


@pytest.mark.asyncio
async def test_open_session_unique_index_rejects_a_direct_duplicate(ctx):
    client, sessions, _ = ctx
    _auth, me, _room = await clinic(client)
    _, user_id = await person(sessions, me["clinic_id"], "doctor")
    values = {"clinic_id": UUID(me["clinic_id"]), "user_id": UUID(user_id), "source": "clock", "created_by_id": UUID(user_id)}
    async with sessions() as session:
        session.add(TeamAttendance(clock_in_at=utcnow(), **values))
        await session.commit()
        session.add(TeamAttendance(clock_in_at=utcnow() + timedelta(seconds=1), **values))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_simultaneous_overlapping_shifts_for_same_person_create_one(ctx):
    client, sessions, _ = ctx
    auth, me, room = await clinic(client)
    second_room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T19 B"})).json()["id"]
    _, doctor = await person(sessions, me["clinic_id"], "doctor")
    override, seen = barrier(sessions, 2)
    app.dependency_overrides[get_db] = override
    bodies = [{"user_id": doctor, "room_id": target, "local_start": "2027-06-07T09:00", "local_end": "2027-06-07T13:00"} for target in (room, second_room)]
    results = await asyncio.wait_for(asyncio.gather(*[client.post("/api/team/shifts", headers=auth, json=body) for body in bodies]), 20)
    assert sorted(item.status_code for item in results) == [201, 409] and len(set(seen)) == 2
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TeamShift).where(TeamShift.user_id == UUID(doctor), TeamShift.status == "active")) == 1


@pytest.mark.asyncio
async def test_simultaneous_shifts_for_same_room_and_post_create_one(ctx):
    client, sessions, _ = ctx
    auth, me, room = await clinic(client)
    _, first = await person(sessions, me["clinic_id"], "doctor")
    _, second = await person(sessions, me["clinic_id"], "doctor")
    override, seen = barrier(sessions, 2)
    app.dependency_overrides[get_db] = override
    bodies = [{"user_id": target, "room_id": room, "local_start": "2027-06-08T09:00", "local_end": "2027-06-08T13:00"} for target in (first, second)]
    results = await asyncio.wait_for(asyncio.gather(*[client.post("/api/team/shifts", headers=auth, json=body) for body in bodies]), 20)
    assert sorted(item.status_code for item in results) == [201, 409] and len(set(seen)) == 2
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TeamShift).where(TeamShift.room_id == UUID(room), TeamShift.status == "active")) == 1


@pytest.mark.asyncio
async def test_exclusion_constraints_reject_direct_overlaps_but_allow_doctor_and_assistant(ctx):
    client, sessions, _ = ctx
    _auth, me, room = await clinic(client)
    _, doctor = await person(sessions, me["clinic_id"], "doctor")
    _, other = await person(sessions, me["clinic_id"], "doctor")
    _, assistant = await person(sessions, me["clinic_id"], "assistant")
    start = utcnow().replace(microsecond=0) + timedelta(days=400)
    def row(user, post):
        return TeamShift(clinic_id=UUID(me["clinic_id"]), user_id=UUID(user), room_id=UUID(room), post=post, starts_at=start, ends_at=start + timedelta(hours=4), status="active", created_by_id=UUID(me["id"]))
    async with sessions() as session:
        session.add(row(doctor, "doctor"))
        session.add(row(assistant, "assistant"))
        await session.commit()
        session.add(row(other, "doctor"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_simultaneous_leave_approval_blocks_agenda_once(ctx):
    client, sessions, _ = ctx
    auth, me, _room = await clinic(client)
    doctor_auth, doctor = await person(sessions, me["clinic_id"], "doctor")
    leave = (await client.post("/api/team/leave", headers=doctor_auth, json={"leave_type": "annual", "starts_on": "2027-07-05", "ends_on": "2027-07-06"})).json()["id"]
    override, seen = barrier(sessions, 2)
    app.dependency_overrides[get_db] = override
    results = await asyncio.wait_for(asyncio.gather(*[client.post(f"/api/team/leave/{leave}/approve", headers=auth) for _ in range(2)]), 20)
    assert sorted(item.status_code for item in results) == [200, 409] and len(set(seen)) == 2
    app.dependency_overrides[get_db] = ctx[2]
    blocks = [item for item in (await client.get("/api/availability/unavailability", headers=auth)).json() if item["resource_id"] == doctor]
    assert len(blocks) == 1
    async with sessions() as session:
        assert (await session.get(TeamLeaveRequest, UUID(leave))).status == "approved"
