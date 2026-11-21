"""T20 on PostgreSQL: populated migration and real concurrency for conversations, messages and tasks."""

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
from app.collab.models import CollabConversation, CollabHandover, CollabMessage, CollabTaskEvent
from app.database import User, get_db, utcnow
from app.main import app
from app.team.models import TeamShift
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
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t20-ci", "ENVIRONMENT": "test"}
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
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def clinic(client):
    registered = await client.post("/api/auth/register", json={"clinic_name": "T20 fictiv", "full_name": "Manager Fictiv", "email": f"{uuid4().hex}@example.com", "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet T20"})).json()
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
async def test_t20_migration_preserves_populated_t19_database():
    source = source_url()
    name = "t20_" + uuid4().hex[:12]
    admin = await asyncpg.connect(dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        base = await alembic(url, "t19_team_shifts")
        assert base.returncode == 0, base.stdout + base.stderr
        clinic_id, user_id, room_id, shift_id = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
        connection = await asyncpg.connect(dsn(url, name))
        try:
            await connection.execute("INSERT INTO clinics(id,name,timezone,created_at,updated_at) VALUES($1,'T20 fictiv','Europe/Bucharest',now(),now())", clinic_id)
            await connection.execute("INSERT INTO users(id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES($1,$2,$3,'x','Medic fictiv','doctor',true,now(),now())", user_id, clinic_id, f"{user_id}@example.com")
            await connection.execute("INSERT INTO rooms(id,clinic_id,name,is_active) VALUES($1,$2,'Cabinet',true)", room_id, clinic_id)
            await connection.execute("INSERT INTO team_shifts(id,clinic_id,user_id,room_id,post,starts_at,ends_at,status,created_by_id,created_at) VALUES($1,$2,$3,$4,'doctor',now(),now()+interval '1 hour','active',$3,now())", shift_id, clinic_id, user_id, room_id)
        finally:
            await connection.close()
        upgraded = await alembic(url, "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        assert revision_is_in_head_lineage("t20_chat_tasks")
        connection = await asyncpg.connect(dsn(url, name))
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
            assert await connection.fetchval("SELECT count(*) FROM team_shifts") == 1
            assert await connection.fetchval("SELECT count(*) FROM collab_messages") == 0
            constraints = {row[0] for row in await connection.fetch("SELECT conname FROM pg_constraint WHERE contype = 'u'")}
            assert {"uq_collab_direct_pair", "uq_collab_room", "uq_collab_message_retry", "uq_collab_message_seq", "uq_collab_task_event_version", "uq_collab_handover_shift"} <= constraints
        finally:
            await connection.close()
    finally:
        admin = await asyncpg.connect(dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_simultaneous_direct_conversation_creates_one(ctx):
    client, sessions = ctx
    _auth, me, _room = await clinic(client)
    (one, one_id), (two, two_id) = await person(sessions, me["clinic_id"], "doctor"), await person(sessions, me["clinic_id"], "assistant")
    override, seen = barrier(sessions, 6)
    app.dependency_overrides[get_db] = override
    calls = [client.post("/api/collab/direct", headers=one if index % 2 else two, json={"user_id": two_id if index % 2 else one_id}) for index in range(6)]
    results = await asyncio.wait_for(asyncio.gather(*calls), 20)
    assert len(set(seen)) == 6 and sorted(item.status_code for item in results) == [200, 200, 200, 200, 200, 201]
    assert len({item.json()["id"] for item in results}) == 1
    key = ":".join(sorted((one_id, two_id)))
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabConversation).where(CollabConversation.direct_key == key)) == 1


@pytest.mark.asyncio
async def test_direct_pair_constraint_rejects_a_duplicate(ctx):
    client, sessions = ctx
    _auth, me, _room = await clinic(client)
    _, one = await person(sessions, me["clinic_id"], "doctor")
    values = {"clinic_id": UUID(me["clinic_id"]), "kind": "direct", "direct_key": f"{one}:duplicat", "last_seq": 0, "created_by_id": UUID(one)}
    async with sessions() as session:
        session.add(CollabConversation(**values))
        await session.commit()
    async with sessions() as session:
        session.add(CollabConversation(**values))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_same_client_request_id_simultaneously_stores_one_message(ctx):
    client, sessions = ctx
    _auth, me, _room = await clinic(client)
    (one, _), (_, two_id) = await person(sessions, me["clinic_id"], "doctor"), await person(sessions, me["clinic_id"], "assistant")
    conversation = (await client.post("/api/collab/direct", headers=one, json={"user_id": two_id})).json()["id"]
    key = str(uuid4())
    override, seen = barrier(sessions, 5)
    app.dependency_overrides[get_db] = override
    calls = [client.post(f"/api/collab/conversations/{conversation}/messages", headers=one, json={"body": "O singură dată", "client_request_id": key}) for _ in range(5)]
    results = await asyncio.wait_for(asyncio.gather(*calls), 20)
    assert len(set(seen)) == 5 and sorted(item.status_code for item in results) == [200, 200, 200, 200, 201]
    assert len({item.json()["id"] for item in results}) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabMessage).where(CollabMessage.conversation_id == UUID(conversation))) == 1


@pytest.mark.asyncio
async def test_simultaneous_senders_get_distinct_sequence_numbers(ctx):
    client, sessions = ctx
    _auth, me, _room = await clinic(client)
    people = [await person(sessions, me["clinic_id"], "doctor") for _ in range(4)]
    group = await client.post("/api/collab/conversations", headers=people[0][0], json={"kind": "group", "title": "Concurență", "member_ids": [row[1] for row in people[1:]]})
    conversation = group.json()["id"]
    override, _seen = barrier(sessions, 4)
    app.dependency_overrides[get_db] = override
    calls = [client.post(f"/api/collab/conversations/{conversation}/messages", headers=row[0], json={"body": f"Mesaj {index}", "client_request_id": str(uuid4())}) for index, row in enumerate(people)]
    results = await asyncio.wait_for(asyncio.gather(*calls), 20)
    assert [item.status_code for item in results] == [201] * 4 and sorted(item.json()["seq"] for item in results) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_simultaneous_first_room_messages_share_one_conversation(ctx):
    client, sessions = ctx
    _auth, me, room = await clinic(client)
    _, doctor_id = await person(sessions, me["clinic_id"], "doctor")
    senders = [await person(sessions, me["clinic_id"], "reception") for _ in range(3)]
    now = utcnow()
    async with sessions() as session:
        session.add(TeamShift(clinic_id=UUID(me["clinic_id"]), user_id=UUID(doctor_id), room_id=UUID(room), post="doctor", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1), status="active", created_by_id=UUID(me["id"])))
        await session.commit()
    override, _seen = barrier(sessions, 3)
    app.dependency_overrides[get_db] = override
    calls = [client.post(f"/api/collab/rooms/{room}/messages", headers=row[0], json={"body": "Către cabinet", "client_request_id": str(uuid4())}) for row in senders]
    results = await asyncio.wait_for(asyncio.gather(*calls), 20)
    assert [item.status_code for item in results] == [201] * 3
    assert len({item.json()["conversation_id"] for item in results}) == 1 and sorted(item.json()["seq"] for item in results) == [1, 2, 3]


@pytest.mark.asyncio
async def test_simultaneous_status_change_accepts_exactly_one(ctx):
    client, sessions = ctx
    auth, me, _room = await clinic(client)
    doctor, doctor_id = await person(sessions, me["clinic_id"], "doctor")
    task = (await client.post("/api/collab/tasks", headers=doctor, json={"title": "Sarcină fictivă", "assignee_id": doctor_id})).json()
    override, seen = barrier(sessions, 4)
    app.dependency_overrides[get_db] = override
    url = f"/api/collab/tasks/{task['id']}/changes"
    bodies = [{"expected_version": 1, "status": state} for state in ("in_progress", "resolved", "in_progress", "resolved")]
    results = await asyncio.wait_for(asyncio.gather(*[client.post(url, headers=auth if index % 2 else doctor, json=body) for index, body in enumerate(bodies)]), 20)
    assert len(set(seen)) == 4 and sorted(item.status_code for item in results) == [200, 409, 409, 409]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabTaskEvent).where(CollabTaskEvent.task_id == UUID(task["id"]))) == 2


@pytest.mark.asyncio
async def test_simultaneous_handover_for_one_shift_creates_one(ctx):
    client, sessions = ctx
    auth, me, room = await clinic(client)
    doctor, doctor_id = await person(sessions, me["clinic_id"], "doctor")
    _, next_id = await person(sessions, me["clinic_id"], "doctor")
    now = utcnow()
    shift = TeamShift(id=uuid4(), clinic_id=UUID(me["clinic_id"]), user_id=UUID(doctor_id), room_id=UUID(room), post="doctor", starts_at=now - timedelta(hours=2), ends_at=now + timedelta(hours=1), status="active", created_by_id=UUID(me["id"]))
    async with sessions() as session:
        session.add(shift)
        await session.commit()
    override, _seen = barrier(sessions, 3)
    app.dependency_overrides[get_db] = override
    body = {"shift_id": str(shift.id), "to_user_id": next_id, "notes": "Note fictive"}
    results = await asyncio.wait_for(asyncio.gather(*[client.post("/api/collab/handovers", headers=auth if index else doctor, json=body) for index in range(3)]), 20)
    assert sorted(item.status_code for item in results) == [201, 409, 409]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CollabHandover).where(CollabHandover.shift_id == shift.id)) == 1
