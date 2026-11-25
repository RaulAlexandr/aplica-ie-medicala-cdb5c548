import json
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, settings
from app.database import AccessLog, AuditEvent, Base, LoginAttempt, User, get_db, utcnow
from app.main import app


@pytest.fixture
async def security_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.sessions = sessions
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


async def create_user(client, email="manager@example.com", role="clinic_manager"):
    response = await client.post("/api/auth/register", json={"clinic_name": "Clinica", "full_name": "Manager",
        "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    async with client.sessions() as db:
        user = await db.scalar(select(User).where(User.email == email))
        user.role = role
        await db.commit()
        return user.id, response.json()


@pytest.mark.asyncio
async def test_login_failures_are_generic_and_lock_after_five(security_client):
    await create_user(security_client)
    responses = [await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "wrong"}) for _ in range(5)]
    unknown = await security_client.post("/api/auth/login", json={"email": "missing@example.com", "password": "wrong"})
    blocked = await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})
    assert all(r.status_code == 401 for r in [*responses, unknown, blocked])
    assert len({json.dumps(r.json(), sort_keys=True) for r in [*responses, unknown, blocked]}) == 1
    async with security_client.sessions() as db:
        rows = (await db.scalars(select(LoginAttempt))).all()
        assert len(rows) == 7 and all(not hasattr(row, "password") for row in rows)
        failed = [row for row in rows if row.email == "manager@example.com"]
        assert all(row.email == row.email.lower() and row.ip_address == "127.0.0.1" and not row.succeeded for row in failed)


@pytest.mark.asyncio
async def test_security_headers_on_success_and_error(security_client):
    success = await security_client.get("/health")
    error = await security_client.get("/api/patients")
    for response in (success, error):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_hsts_only_when_https_setting_is_enabled(security_client, monkeypatch):
    monkeypatch.setattr(settings, "https_enabled", True)
    response = await security_client.get("/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_credentialed_cors_rejects_wildcard_origin():
    with pytest.raises(ValueError, match="Wildcard CORS"):
        Settings(cors_origins="*")


@pytest.mark.asyncio
async def test_password_minimum_is_enforced_on_change(security_client):
    _, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    response = await security_client.post("/api/auth/change-password", headers=headers,
        json={"current_password": "correct horse battery staple", "new_password": "short"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_manager_can_view_access_log_but_no_delete_endpoint(security_client):
    _, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await security_client.get("/api/security/access-log", headers=headers)).status_code == 200
    assert (await security_client.delete("/api/security/access-log", headers=headers)).status_code == 405


@pytest.mark.asyncio
async def test_access_log_model_records_request_context(security_client):
    _, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    patient = await security_client.post("/api/patients", headers=headers,
        json={"first_name": "Ana", "last_name": "Test"})
    assert (await security_client.get(f"/api/patients/{patient.json()['id']}", headers=headers)).status_code == 200
    async with security_client.sessions() as db:
        entry = await db.scalar(select(AccessLog))
        assert entry and entry.patient_id and entry.action == "patient.read"
        assert entry.occurred_at <= utcnow() + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_lock_expires_after_fifteen_minutes(security_client):
    await create_user(security_client)
    for _ in range(5):
        await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "wrong"})
    async with security_client.sessions() as db:
        attempts = (await db.scalars(select(LoginAttempt).where(LoginAttempt.email == "manager@example.com"))).all()
        for attempt in attempts:
            attempt.attempted_at = utcnow() - timedelta(minutes=16)
        await db.commit()
    unlocked = await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})
    assert unlocked.status_code == 200


@pytest.mark.asyncio
async def test_manager_unlock_is_audited(security_client):
    user_id, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    for _ in range(5):
        await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "wrong"})
    response = await security_client.post(f"/api/security/staff/{user_id}/unlock", headers=headers)
    assert response.status_code == 204
    assert (await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})).status_code == 200
    async with security_client.sessions() as db:
        event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "login_unlocked"))
        assert event and event.entity_id == user_id


@pytest.mark.asyncio
async def test_export_is_clinic_scoped_complete_and_audited(security_client):
    _, manager_tokens = await create_user(security_client)
    manager_headers = {"Authorization": f"Bearer {manager_tokens['access_token']}"}
    created = await security_client.post("/api/patients", headers=manager_headers, json={"first_name": "Ana", "last_name": "Test"})
    patient_id = created.json()["id"]
    exported = await security_client.get(f"/api/security/patients/{patient_id}/export", headers=manager_headers)
    assert exported.status_code == 200
    assert exported.json()["data"]["patients"][0]["first_name"] == "Ana"
    foreign = await security_client.post("/api/auth/register", json={"clinic_name": "Altă clinică", "full_name": "Manager", "email": "second@example.com", "password": "correct horse battery staple"})
    foreign_headers = {"Authorization": f"Bearer {foreign.json()['access_token']}"}
    assert (await security_client.get(f"/api/security/patients/{patient_id}/export", headers=foreign_headers)).status_code == 404
    async with security_client.sessions() as db:
        assert await db.scalar(select(AuditEvent).where(AuditEvent.action == "exported"))


@pytest.mark.asyncio
async def test_password_change_revokes_other_sessions(security_client):
    _, first = await create_user(security_client)
    second = await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})
    current = second.json()
    headers = {"Authorization": f"Bearer {current['access_token']}"}
    changed = await security_client.post("/api/auth/change-password", headers=headers, json={
        "current_password": "correct horse battery staple", "new_password": "a new long password"})
    assert changed.status_code == 204
    assert (await security_client.get("/api/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"})).status_code == 401
    listed = await security_client.get("/api/security/sessions", headers=headers)
    assert listed.status_code == 200 and len(listed.json()) == 1 and listed.json()[0]["current"] is True


@pytest.mark.asyncio
async def test_one_session_can_be_closed_and_access_log_has_no_delete(security_client):
    _, first = await create_user(security_client)
    second = (await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})).json()
    headers = {"Authorization": f"Bearer {second['access_token']}"}
    sessions = (await security_client.get("/api/security/sessions", headers=headers)).json()
    other = next(item for item in sessions if not item["current"])
    closed = await security_client.delete(f"/api/security/sessions/{other['session_id']}", headers=headers)
    assert closed.status_code == 204
    assert (await security_client.get("/api/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"})).status_code == 401
    third = (await security_client.post("/api/auth/login", json={"email": "manager@example.com", "password": "correct horse battery staple"})).json()
    assert (await security_client.delete("/api/security/sessions/others", headers=headers)).status_code == 204
    assert (await security_client.get("/api/auth/me", headers={"Authorization": f"Bearer {third['access_token']}"})).status_code == 401
    assert (await security_client.get("/api/auth/me", headers=headers)).status_code == 200
    assert (await security_client.delete("/api/security/access-log", headers=headers)).status_code == 405


@pytest.mark.asyncio
async def test_patient_list_is_not_written_to_access_log(security_client):
    _, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await security_client.get("/api/patients", headers=headers)
    async with security_client.sessions() as db:
        assert await db.scalar(select(AccessLog.id)) is None


@pytest.mark.asyncio
async def test_patient_listing_has_constant_query_count(security_client):
    _, tokens = await create_user(security_client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    for number in range(5):
        await security_client.post("/api/patients", headers=headers,
            json={"first_name": f"P{number}", "last_name": "Test"})
    statements = []
    engine = security_client.sessions.kw["bind"]

    def count_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        response = await security_client.get("/api/patients", headers=headers)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
    assert response.status_code == 200 and len(response.json()) == 5
    assert len(statements) <= 5
    assert sum("from patients" in statement for statement in statements) == 1
    assert not any("patient_access_log" in statement for statement in statements)
