"""Sol review point 1: every path that touches refresh tokens locks the users row before any refresh_tokens row.

SQLite ignores FOR UPDATE, so this records the ORM-level intent (SELECT ... FOR UPDATE per entity) together
with the refresh_tokens INSERT/UPDATE statements, in execution order, and checks the order per HTTP path.
The PostgreSQL behaviour under contention is proven by tests/test_postgres_t01_refresh_deactivation.py.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.database import Base, RefreshToken, User, get_db
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def recorded():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    events: list[tuple[str, str]] = []

    def on_orm_execute(state):
        statement = state.statement
        if state.is_select and getattr(statement, "_for_update_arg", None) is not None:
            entity = statement.column_descriptions[0].get("entity")
            name = {User: "users", RefreshToken: "refresh_tokens"}.get(entity)
            if name:
                events.append(("lock", name))

    def on_cursor(conn, cursor, statement, parameters, context, executemany):
        text = statement.lstrip().upper()
        if text.startswith(("INSERT INTO REFRESH_TOKENS", "UPDATE REFRESH_TOKENS")):
            events.append(("write", "refresh_tokens"))

    event.listen(Session, "do_orm_execute", on_orm_execute)
    event.listen(engine.sync_engine, "before_cursor_execute", on_cursor)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, events
    finally:
        app.dependency_overrides.clear()
        event.remove(Session, "do_orm_execute", on_orm_execute)
        event.remove(engine.sync_engine, "before_cursor_execute", on_cursor)
        await engine.dispose()


def assert_users_locked_first(events: list[tuple[str, str]], path: str) -> None:
    touches = [index for index, item in enumerate(events) if item[1] == "refresh_tokens"]
    assert touches, f"{path}: expected a refresh_tokens lock or write, got {events}"
    assert ("lock", "users") in events[:touches[0]], f"{path}: refresh_tokens touched before the users row lock: {events}"


async def test_every_refresh_token_path_locks_users_before_refresh_tokens(recorded):
    client, events = recorded
    registered = await client.post("/api/auth/register", json={"clinic_name": "Lock Clinic", "full_name": "Manager", "email": "lock-manager@example.com", "password": PASSWORD})
    manager = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    invitation = await client.post("/api/staff/invitations", headers=manager, json={"email": "lock-member@example.com", "full_name": "Member", "role": "doctor"})
    token = parse_qs(urlparse(invitation.json()["invitation_url"]).query)["token"][0]
    member_id = (await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": PASSWORD})).json()["id"]

    async def run(path: str, call):
        events.clear()
        response = await call()
        assert response.status_code in (200, 204), (path, response.status_code, response.text)
        assert_users_locked_first(events, path)
        return response

    login = await run("login", lambda: client.post("/api/auth/login", json={"email": "lock-member@example.com", "password": PASSWORD}))
    refreshed = await run("refresh", lambda: client.post("/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]}))
    member = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    await run("logout", lambda: client.post("/api/auth/logout", headers=member, json={"refresh_token": refreshed.json()["refresh_token"]}))
    await client.post("/api/auth/login", json={"email": "lock-member@example.com", "password": PASSWORD})
    await run("deactivate", lambda: client.post(f"/api/staff/{member_id}/deactivate", headers=manager))
    await run("reactivate", lambda: client.post(f"/api/staff/{member_id}/reactivate", headers=manager))
