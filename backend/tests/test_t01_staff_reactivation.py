"""T01 gap: a manager/administrator reactivates a deactivated staff member.

Sessions ended by the deactivation stay ended: refresh tokens are revoked and access tokens issued
before the deactivation are rejected even after reactivation; the person signs in again.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import AuditEvent, Base, RefreshToken, User, get_db
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def register(client, clinic: str, email: str) -> dict:
    response = await client.post("/api/auth/register", json={"clinic_name": clinic, "full_name": f"Manager {clinic}", "email": email, "password": PASSWORD})
    assert response.status_code == 201
    return response.json()


async def login(client, email: str):
    return await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


async def invite_member(client, manager_headers: dict, email: str, role: str = "doctor") -> str:
    invitation = await client.post("/api/staff/invitations", headers=manager_headers, json={"email": email, "full_name": f"Member {role}", "role": role})
    assert invitation.status_code == 201
    token = parse_qs(urlparse(invitation.json()["invitation_url"]).query)["token"][0]
    accepted = await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": PASSWORD})
    assert accepted.status_code == 200
    return accepted.json()["id"]


async def add_user(sessions, clinic_id: str, email: str, role: str) -> None:
    async with sessions() as session:
        session.add(User(clinic_id=clinic_id, email=email, password_hash=hash_password(PASSWORD), full_name=f"Fictive {role}", role=role))
        await session.commit()


async def test_reactivation_restores_login_but_never_revives_old_sessions(ctx):
    client, sessions = ctx
    manager = bearer(await register(client, "Reactivation Clinic", "t01r-manager@example.com"))
    doctor_id = await invite_member(client, manager, "t01r-doctor@example.com")
    old = (await login(client, "t01r-doctor@example.com")).json()
    assert (await client.get("/api/auth/me", headers=bearer(old))).status_code == 200

    deactivated = await client.post(f"/api/staff/{doctor_id}/deactivate", headers=manager)
    assert deactivated.status_code == 200 and deactivated.json()["staff"]["is_active"] is False
    assert (await login(client, "t01r-doctor@example.com")).status_code == 401
    async with sessions() as session:
        tokens = (await session.scalars(select(RefreshToken).where(RefreshToken.user_id == doctor_id))).all()
        assert tokens and all(token.revoked_at is not None for token in tokens)

    reactivated = await client.post(f"/api/staff/{doctor_id}/reactivate", headers=manager)
    assert reactivated.status_code == 200
    body = reactivated.json()
    assert body["is_active"] is True and body["id"] == doctor_id
    assert body["deactivated_at"] is not None  # kept as the session cutoff

    # Old access and refresh tokens stay invalid; the person must authenticate again.
    assert (await client.get("/api/auth/me", headers=bearer(old))).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401
    fresh = await login(client, "t01r-doctor@example.com")
    assert fresh.status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(fresh.json()))).json()["id"] == doctor_id
    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": fresh.json()["refresh_token"]})
    assert refreshed.status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(refreshed.json()))).status_code == 200

    # Traceability: who and when, consistent with the deactivation record.
    manager_id = (await client.get("/api/auth/me", headers=manager)).json()["id"]
    detail = await client.get(f"/api/staff/members/{doctor_id}", headers=manager)
    assert detail.status_code == 200 and detail.json()["is_active"] is True
    history = detail.json()["history"]
    assert [entry["action"] for entry in history][:2] == ["reactivated", "deactivated"]
    assert all(entry["actor_id"] == manager_id and entry["actor_name"] == "Manager Reactivation Clinic" for entry in history[:2])
    assert history[0]["occurred_at"] >= history[1]["occurred_at"]
    async with sessions() as session:
        actions = (await session.scalars(select(AuditEvent.action).where(AuditEvent.entity_type == "staff", AuditEvent.entity_id == doctor_id))).all()
    assert sorted(actions) == ["created_from_invitation", "deactivated", "reactivated"]

    # A second deactivation/reactivation cycle again ends the session issued in between.
    between = fresh.json()
    assert (await client.post(f"/api/staff/{doctor_id}/deactivate", headers=manager)).status_code == 200
    assert (await client.post(f"/api/staff/{doctor_id}/reactivate", headers=manager)).status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(between))).status_code == 401
    assert (await login(client, "t01r-doctor@example.com")).status_code == 200


async def test_reactivation_roles_foreign_clinic_double_and_self(ctx):
    client, sessions = ctx
    manager_tokens = await register(client, "Roles Clinic", "t01r-roles@example.com")
    manager = bearer(manager_tokens)
    manager_me = (await client.get("/api/auth/me", headers=manager)).json()
    foreign = bearer(await register(client, "Foreign Clinic", "t01r-foreign@example.com"))
    target_id = await invite_member(client, manager, "t01r-target@example.com", "assistant")
    assert (await client.post(f"/api/staff/{target_id}/deactivate", headers=manager)).status_code == 200

    for role in ("doctor", "assistant", "reception"):
        await invite_member(client, manager, f"t01r-{role}@example.com", role)
        headers = bearer((await login(client, f"t01r-{role}@example.com")).json())
        denied = await client.post(f"/api/staff/{target_id}/reactivate", headers=headers)
        assert denied.status_code == 403, role
    assert (await client.post(f"/api/staff/{target_id}/reactivate")).status_code == 401

    # Another clinic's manager cannot see or change the member: 404, state unchanged.
    assert (await client.post(f"/api/staff/{target_id}/reactivate", headers=foreign)).status_code == 404
    assert (await client.get(f"/api/staff/members/{target_id}", headers=manager)).json()["is_active"] is False

    # The clinic administrator may reactivate.
    await add_user(sessions, manager_me["clinic_id"], "t01r-admin@example.com", "administrator")
    admin = bearer((await login(client, "t01r-admin@example.com")).json())
    assert (await client.post(f"/api/staff/{target_id}/reactivate", headers=admin)).status_code == 200
    duplicate = await client.post(f"/api/staff/{target_id}/reactivate", headers=manager)
    assert duplicate.status_code == 409 and duplicate.json()["detail"] == "Staff member is already active"

    # Own account and management accounts are excluded, mirroring the deactivation guard.
    assert (await client.post(f"/api/staff/{manager_me['id']}/reactivate", headers=admin)).status_code == 409
    own = await client.post(f"/api/staff/{manager_me['id']}/reactivate", headers=manager)
    assert own.status_code == 409 and own.json()["detail"] == "You cannot reactivate your own account"
    assert (await client.post("/api/staff/00000000-0000-0000-0000-000000000000/reactivate", headers=manager)).status_code == 404


async def test_history_is_only_exposed_to_management(ctx):
    client, _ = ctx
    manager = bearer(await register(client, "History Clinic", "t01r-history@example.com"))
    await invite_member(client, manager, "t01r-history-doctor@example.com")
    reception_id = await invite_member(client, manager, "t01r-history-reception@example.com", "reception")
    await client.post(f"/api/staff/{reception_id}/deactivate", headers=manager)
    await client.post(f"/api/staff/{reception_id}/reactivate", headers=manager)
    doctor = bearer((await login(client, "t01r-history-doctor@example.com")).json())
    seen = await client.get(f"/api/staff/members/{reception_id}", headers=doctor)
    assert seen.status_code == 200 and seen.json()["history"] == []
    assert len((await client.get(f"/api/staff/members/{reception_id}", headers=manager)).json()["history"]) == 3
