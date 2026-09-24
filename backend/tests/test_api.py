from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, Clinic, Room, User
from app.main import app
from app.database import get_db
from app.auth.service import hash_password


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client, clinic="Bright Smiles", email="manager@example.com"):
    response = await client.post("/api/auth/register", json={"clinic_name": clinic, "full_name": "Clinic Manager", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_auth_and_patient_tenant_isolation(client):
    first = await register(client)
    headers = {"Authorization": f"Bearer {first['access_token']}"}
    created = await client.post("/api/patients", headers=headers, json={"first_name": "Ana", "last_name": "Popescu", "phone": "0700"})
    assert created.status_code == 201
    second = await register(client, "Other Clinic", "other@example.com")
    other_headers = {"Authorization": f"Bearer {second['access_token']}"}
    listing = await client.get("/api/patients", headers=other_headers)
    assert listing.status_code == 200 and listing.json() == []
    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert refreshed.status_code == 200
    replay = await client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_appointment_conflict_detection(client):
    tokens = await register(client, email="scheduler@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    room = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet 1"})
    assert room.status_code == 201
    patient = await client.post("/api/patients", headers=headers, json={"first_name": "Ion", "last_name": "Ionescu"})
    assert patient.status_code == 201
    me = await client.get("/api/auth/me", headers=headers)
    payload = {"patient_id": patient.json()["id"], "doctor_id": me.json()["id"], "room_id": room.json()["id"], "starts_at": "2026-10-01T10:00:00+00:00", "duration_minutes": 60, "appointment_type": "Consultation"}
    assert (await client.post("/api/appointments", headers=headers, json=payload)).status_code == 201
    conflict = await client.post("/api/appointments", headers=headers, json={**payload, "starts_at": "2026-10-01T10:30:00+00:00"})
    assert conflict.status_code == 409
