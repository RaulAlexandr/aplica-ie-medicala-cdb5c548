from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, StaffInvitation, User, get_db
from app.main import app

T01_SESSIONS: dict[int, async_sessionmaker] = {}


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        T01_SESSIONS[id(http)] = sessions
        yield http
    T01_SESSIONS.pop(id(http), None)
    app.dependency_overrides.clear()
    await engine.dispose()


async def register_manager(client, clinic: str, email: str) -> tuple[dict, dict]:
    response = await client.post("/api/auth/register", json={"clinic_name": clinic, "full_name": "Manager", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    tokens = response.json()
    return tokens, {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_clinic_settings_and_cross_clinic_authorization(client):
    first, first_headers = await register_manager(client, "First Clinic", "t01-first@example.com")
    second, second_headers = await register_manager(client, "Second Clinic", "t01-second@example.com")
    settings = await client.get("/api/clinic", headers=first_headers)
    assert settings.status_code == 200 and settings.json()["timezone"] == "Europe/Bucharest"
    changed = await client.patch("/api/clinic", headers=first_headers, json={"name": "First Updated", "timezone": "Europe/London"})
    assert changed.status_code == 200 and changed.json()["timezone"] == "Europe/London"
    assert (await client.get("/api/clinic", headers=second_headers)).json()["name"] == "Second Clinic"
    assert first["access_token"] != second["access_token"]


@pytest.mark.asyncio
async def test_invitation_acceptance_expiry_revocation_reuse_and_role_tampering(client):
    _, headers = await register_manager(client, "Invitation Clinic", "t01-invite@example.com")
    response = await client.post("/api/staff/invitations", headers=headers, json={"email": "doctor.t01@example.com", "full_name": "Doctor T01", "role": "administrator"})
    assert response.status_code == 422
    response = await client.post("/api/staff/invitations", headers=headers, json={"email": "doctor.t01@example.com", "full_name": "Doctor T01", "role": "doctor"})
    assert response.status_code == 201
    invitation = response.json()
    token = parse_qs(urlparse(invitation["invitation_url"]).query)["token"][0]
    accepted = await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": "correct horse battery staple"})
    assert accepted.status_code == 200 and accepted.json()["role"] == "doctor"
    doctor_login = await client.post("/api/auth/login", json={"email": "doctor.t01@example.com", "password": "correct horse battery staple"})
    doctor_headers = {"Authorization": f"Bearer {doctor_login.json()['access_token']}"}
    assert (await client.get("/api/clinic", headers=doctor_headers)).status_code == 403
    assert (await client.post("/api/staff/invitations", headers=doctor_headers, json={"email": "tamper@example.com", "full_name": "Tamper", "role": "doctor"})).status_code == 403
    reused = await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": "correct horse battery staple"})
    assert reused.status_code == 400
    revoked = await client.post("/api/staff/invitations", headers=headers, json={"email": "assistant.t01@example.com", "full_name": "Assistant T01", "role": "assistant"})
    revoke_token = parse_qs(urlparse(revoked.json()["invitation_url"]).query)["token"][0]
    assert (await client.post(f"/api/staff/invitations/{revoked.json()['id']}/revoke", headers=headers)).status_code == 200
    assert (await client.post("/api/staff/invitations/accept", params={"token": revoke_token}, json={"password": "correct horse battery staple"})).status_code == 400


@pytest.mark.asyncio
async def test_expired_invitation_and_inactive_access_denial(client):
    _, headers = await register_manager(client, "Expiry Clinic", "t01-expiry@example.com")
    response = await client.post("/api/staff/invitations", headers=headers, json={"email": "expired.t01@example.com", "full_name": "Expired T01", "role": "reception", "expires_in_days": 1})
    invitation_id = response.json()["id"]
    token = parse_qs(urlparse(response.json()["invitation_url"]).query)["token"][0]
    sessions = T01_SESSIONS[id(client)]
    async with sessions() as session:
        invitation = await session.get(StaffInvitation, invitation_id)
        invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    assert (await client.post("/api/staff/invitations/accept", params={"token": token}, json={"password": "correct horse battery staple"})).status_code == 400
    account = await client.post("/api/staff/invitations", headers=headers, json={"email": "inactive.t01@example.com", "full_name": "Inactive T01", "role": "doctor"})
    account_token = parse_qs(urlparse(account.json()["invitation_url"]).query)["token"][0]
    accepted = await client.post("/api/staff/invitations/accept", params={"token": account_token}, json={"password": "correct horse battery staple"})
    user_id = accepted.json()["id"]
    login = await client.post("/api/auth/login", json={"email": "inactive.t01@example.com", "password": "correct horse battery staple"})
    sessions = T01_SESSIONS[id(client)]
    async with sessions() as session:
        user = await session.get(User, user_id)
        user.is_active = False
        await session.commit()
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})).status_code == 401


@pytest.mark.asyncio
async def test_room_uniqueness_inactive_selection_and_future_deactivation(client):
    _, headers = await register_manager(client, "Room Clinic", "t01-room@example.com")
    first = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet T01"})
    assert first.status_code == 201
    assert (await client.post("/api/rooms", headers=headers, json={"name": "cabinet t01"})).status_code == 409
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Patient", "last_name": "T01"})
    manager = (await client.get("/api/auth/me", headers=headers)).json()
    appointment = await client.post("/api/appointments", headers=headers, json={"patient_id": patient.json()["id"], "doctor_id": manager["id"], "room_id": first.json()["id"], "starts_at": "2030-01-01T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Setup"})
    assert appointment.status_code == 201
    blocked = await client.patch(f"/api/rooms/{first.json()['id']}", headers=headers, json={"is_active": False})
    assert blocked.status_code == 200 and blocked.json()["affected_appointments"]
    assert blocked.json()["room"]["is_active"] is True
    await client.patch(f"/api/appointments/{appointment.json()['id']}/status", headers=headers, json={"status": "cancelled"})
    inactive = await client.patch(f"/api/rooms/{first.json()['id']}", headers=headers, json={"is_active": False})
    assert inactive.status_code == 200 and inactive.json()["room"]["is_active"] is False
    unavailable = await client.post("/api/appointments", headers=headers, json={"patient_id": patient.json()["id"], "doctor_id": manager["id"], "room_id": first.json()["id"], "starts_at": "2030-01-02T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Setup"})
    assert unavailable.status_code == 422
