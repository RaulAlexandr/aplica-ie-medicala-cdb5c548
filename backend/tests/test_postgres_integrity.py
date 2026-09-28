import asyncio
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import hash_password
from app.database import Room, User, get_db
from app.main import app


@pytest.fixture
async def postgres_context():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(url, pool_pre_ping=True)
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


async def create_resources(client: AsyncClient, sessions: async_sessionmaker):
    registration = await client.post(
        "/api/auth/register",
        json={
            "clinic_name": f"Concurrency Clinic {uuid4().hex[:8]}",
            "full_name": "Concurrency Manager",
            "email": f"manager-{uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert registration.status_code == 201
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    manager = (await client.get("/api/auth/me", headers=headers)).json()
    async with sessions() as session:
        users = [
            User(clinic_id=manager["clinic_id"], full_name=f"Doctor {index}", email=f"doctor-{uuid4().hex}@example.com", password_hash=hash_password("correct horse battery staple"), role="doctor", is_active=True)
            for index in range(3)
        ]
        assistants = [
            User(clinic_id=manager["clinic_id"], full_name=f"Assistant {index}", email=f"assistant-{uuid4().hex}@example.com", password_hash=hash_password("correct horse battery staple"), role="assistant", is_active=True)
            for index in range(2)
        ]
        rooms = [Room(clinic_id=manager["clinic_id"], name=f"Room {index}", is_active=True) for index in range(6)]
        session.add_all(users + assistants + rooms)
        await session.commit()
        for entity in users + assistants + rooms:
            await session.refresh(entity)
    return headers, users, assistants, rooms


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_concurrent_doctor_room_and_assistant_conflicts(postgres_context):
    client, sessions = postgres_context
    headers, doctors, assistants, rooms = await create_resources(client, sessions)
    patients = []
    for index in range(6):
        response = await client.post("/api/patients", headers=headers, json={"first_name": f"Patient{index}", "last_name": "Synthetic"})
        assert response.status_code == 201
        patients.append(response.json()["id"])

    async def concurrent_pair(first: dict, second: dict) -> list[int]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as left, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as right:
            responses = await asyncio.gather(
                left.post("/api/appointments", headers=headers, json=first),
                right.post("/api/appointments", headers=headers, json=second),
            )
            return [response.status_code for response in responses]

    base = {"starts_at": "2027-01-05T10:00:00+00:00", "duration_minutes": 60, "appointment_type": "Synthetic"}
    doctor_conflict = await concurrent_pair(
        {**base, "patient_id": patients[0], "doctor_id": str(doctors[0].id), "room_id": str(rooms[0].id)},
        {**base, "patient_id": patients[1], "doctor_id": str(doctors[0].id), "room_id": str(rooms[1].id)},
    )
    room_conflict = await concurrent_pair(
        {**base, "starts_at": "2027-01-05T12:00:00+00:00", "patient_id": patients[2], "doctor_id": str(doctors[1].id), "room_id": str(rooms[2].id)},
        {**base, "starts_at": "2027-01-05T12:00:00+00:00", "patient_id": patients[3], "doctor_id": str(doctors[2].id), "room_id": str(rooms[2].id)},
    )
    assistant_conflict = await concurrent_pair(
        {**base, "starts_at": "2027-01-05T14:00:00+00:00", "patient_id": patients[4], "doctor_id": str(doctors[1].id), "room_id": str(rooms[3].id), "assistant_id": str(assistants[0].id)},
        {**base, "starts_at": "2027-01-05T14:00:00+00:00", "patient_id": patients[5], "doctor_id": str(doctors[0].id), "room_id": str(rooms[4].id), "assistant_id": str(assistants[0].id)},
    )
    assert sorted(doctor_conflict) == [201, 409]
    assert sorted(room_conflict) == [201, 409]
    assert sorted(assistant_conflict) == [201, 409]
