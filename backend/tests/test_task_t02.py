from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.appointments.service import local_to_utc
from app.database import Base, get_db
from app.main import app


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
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def setup(client: AsyncClient, email: str) -> tuple[dict[str, str], dict, dict, dict]:
    registration = await client.post(
        "/api/auth/register",
        json={"clinic_name": "Calendar Clinic", "full_name": "Manager", "email": email, "password": "correct horse battery staple"},
    )
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=headers)).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Calendar", "last_name": "Patient"})).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Room 1"})).json()
    return headers, me, patient, room


@pytest.mark.asyncio
async def test_calendar_day_week_filters_and_local_timezone(client):
    headers, manager, patient, room = await setup(client, "calendar@example.com")
    created = await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": manager["id"], "room_id": room["id"], "local_start": "2027-01-04T10:00", "duration_minutes": 30, "appointment_type": "Consultation"})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["starts_at"] == "2027-01-04T08:00:00Z"
    assert item["local_start"].startswith("2027-01-04T10:00:00")
    assert (await client.get("/api/appointments", headers=headers, params={"day": "2027-01-04"})).json()[0]["id"] == item["id"]
    assert (await client.get("/api/appointments", headers=headers, params={"week_start": "2027-01-04", "patient_search": "Calendar"})).json()[0]["id"] == item["id"]
    assert (await client.get("/api/appointments", headers=headers, params={"day": "2027-01-05"})).json() == []


@pytest.mark.asyncio
async def test_edit_status_reschedule_and_history_are_auditable(client):
    headers, manager, patient, room = await setup(client, "history-calendar@example.com")
    created = (await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": manager["id"], "room_id": room["id"], "starts_at": "2027-02-01T08:00:00+00:00", "duration_minutes": 30, "appointment_type": "Review"})).json()
    updated = await client.patch(f"/api/appointments/{created['id']}", headers=headers, json={"notes": "Updated note", "duration_minutes": 45})
    assert updated.status_code == 200
    moved = await client.post(f"/api/appointments/{created['id']}/reschedule", headers=headers, json={"local_start": "2027-02-01T12:00", "reason": "Patient request"})
    assert moved.status_code == 200
    cancelled = await client.patch(f"/api/appointments/{created['id']}/status", headers=headers, json={"status": "cancelled"})
    assert cancelled.status_code == 200
    history = await client.get(f"/api/appointments/{created['id']}/history", headers=headers)
    assert history.status_code == 200
    assert {entry["action"] for entry in history.json()} >= {"created", "updated", "rescheduled", "status_changed"}


@pytest.mark.asyncio
async def test_working_hours_and_room_unavailability_are_enforced(client):
    headers, manager, patient, room = await setup(client, "availability-calendar@example.com")
    hours = await client.put("/api/availability/working-hours", headers=headers, json=[{"day_of_week": 0, "start_time": "09:00:00", "end_time": "17:00:00"}])
    assert hours.status_code == 200
    outside = await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": manager["id"], "room_id": room["id"], "local_start": "2027-02-01T08:00", "duration_minutes": 30, "appointment_type": "Outside"})
    assert outside.status_code == 422 and "working hours" in outside.json()["detail"]
    blocked = await client.post("/api/availability/unavailability", headers=headers, json={"resource_type": "room", "resource_id": room["id"], "starts_at": "2027-02-01T12:00:00+00:00", "ends_at": "2027-02-01T13:00:00+00:00", "reason": "Maintenance"})
    assert blocked.status_code == 201
    unavailable = await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": manager["id"], "room_id": room["id"], "local_start": "2027-02-01T14:00", "duration_minutes": 30, "appointment_type": "Blocked"})
    assert unavailable.status_code == 422 and "room" in unavailable.json()["detail"]


def test_dst_policy_is_explicit():
    assert local_to_utc(datetime.fromisoformat("2027-03-28T10:00:00"), "Europe/Bucharest") == datetime(2027, 3, 28, 7, 0, tzinfo=UTC)
    with pytest.raises(Exception, match="does not exist"):
        local_to_utc(datetime.fromisoformat("2027-03-28T03:30:00"), "Europe/Bucharest")
    with pytest.raises(Exception, match="ambiguous"):
        local_to_utc(datetime.fromisoformat("2027-10-31T03:30:00"), "Europe/Bucharest")
    assert local_to_utc(datetime.fromisoformat("2027-10-31T03:30:00"), "Europe/Bucharest", fold=1).utcoffset() == UTC.utcoffset(None)
