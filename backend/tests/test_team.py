"""T19 rules on SQLite: roles, clinic isolation, validation, DST, history and timer separation."""

import logging
import struct
import zlib
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.config import settings
from app.database import (
    AuditEvent,
    Base,
    Clinic,
    PerformedProcedure,
    ProcedureExecutionSegment,
    ResourceUnavailability,
    User,
    get_db,
)
from app.main import app
from app.team.models import (
    TeamAttendance,
    TeamAttendanceCorrection,
    TeamDocument,
    TeamShift,
)

PASSWORD = "correct horse battery staple"
SECRET_BYTES = b"continut-administrativ-secret"


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"") + b""


def pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\n% " + SECRET_BYTES + b"\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_storage_dir", str(tmp_path / "store"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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


async def clinic(client, sessions, timezone="Europe/Bucharest"):
    email = f"m-{uuid4().hex[:8]}@example.com"
    registered = await client.post("/api/auth/register", json={"clinic_name": "Clinica fictivă T19", "full_name": "Manager Fictiv", "email": email, "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    async with sessions() as session:
        (await session.get(Clinic, UUID(me["clinic_id"]))).timezone = timezone
        await session.commit()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet 1"})).json()
    return {"auth": auth, "id": me["id"], "clinic_id": me["clinic_id"], "room": room["id"]}


async def person(sessions, clinic_id, role):
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{role}-{uuid4().hex[:8]}@example.com", password_hash="unused",
                full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return {"auth": {"Authorization": f"Bearer {create_access_token(user)}"}, "id": str(user.id)}


async def upload(client, auth, user_id, content=None, name="contract.pdf", key=None):
    headers = {**auth, "Content-Type": "application/octet-stream", "X-File-Name": name}
    if key:
        headers["Idempotency-Key"] = key
    return await client.post(f"/api/team/members/{user_id}/documents", params={"category": "contract"}, headers=headers, content=content or pdf())


def shift(user_id, room, start, end, **extra):
    return {"user_id": user_id, "room_id": room, "local_start": start, "local_end": end, **extra}


@pytest.mark.asyncio
async def test_documents_visible_only_to_manager_and_owner(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    reception = await person(sessions, c["clinic_id"], "reception")
    created = await upload(client, c["auth"], doctor["id"])
    assert created.status_code == 201, created.text
    doc = created.json()["id"]
    assert (await client.get(f"/api/team/members/{doctor['id']}/documents", headers=doctor["auth"])).status_code == 200
    owner_content = await client.get(f"/api/team/documents/{doc}/content", headers=doctor["auth"])
    assert owner_content.status_code == 200 and SECRET_BYTES in owner_content.content
    assert owner_content.headers["cache-control"] == "private, no-store"
    assert (await client.get(f"/api/team/documents/{doc}/content", headers=c["auth"])).status_code == 200
    assert (await client.get(f"/api/team/members/{doctor['id']}/documents", headers=reception["auth"])).status_code == 403
    denied = await client.get(f"/api/team/documents/{doc}/content", headers=reception["auth"])
    missing = await client.get(f"/api/team/documents/{uuid4()}/content", headers=reception["auth"])
    assert denied.status_code == missing.status_code == 404 and denied.json() == missing.json()
    assert (await upload(client, doctor["auth"], doctor["id"])).status_code == 403
    assert (await client.post(f"/api/team/documents/{doc}/archive", headers=doctor["auth"], json={"reason": "x"})).status_code == 403


@pytest.mark.asyncio
async def test_document_other_clinic_is_404_and_retry_is_idempotent(env):
    client, sessions = env
    a, b = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, a["clinic_id"], "doctor")
    key = str(uuid4())
    first = await upload(client, a["auth"], doctor["id"], key=key)
    second = await upload(client, a["auth"], doctor["id"], key=key)
    assert first.status_code == 201 and second.status_code == 201 and first.json()["id"] == second.json()["id"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TeamDocument)) == 1
    assert (await upload(client, a["auth"], doctor["id"], content=png(), name="x.png", key=key)).status_code == 409
    assert (await client.get(f"/api/team/documents/{first.json()['id']}/content", headers=b["auth"])).status_code == 404
    assert (await client.get(f"/api/team/members/{doctor['id']}/documents", headers=b["auth"])).status_code == 404
    assert (await upload(client, b["auth"], doctor["id"])).status_code == 404


@pytest.mark.asyncio
async def test_document_content_validation_and_no_content_in_logs(env, caplog):
    client, sessions = env
    c = await clinic(client, sessions)
    caplog.set_level(logging.DEBUG)
    assert (await upload(client, c["auth"], c["id"], content=b"<html>x</html>", name="x.pdf")).status_code == 415
    assert (await upload(client, c["auth"], c["id"], content=pdf(), name="x.png")).status_code == 415
    assert (await upload(client, c["auth"], c["id"])).status_code == 201
    assert SECRET_BYTES.decode() not in caplog.text
    async with sessions() as session:
        events = (await session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "team_document"))).all()
    assert [event.action for event in events] == ["uploaded"]


@pytest.mark.asyncio
async def test_archive_keeps_document_history(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doc = (await upload(client, c["auth"], c["id"])).json()["id"]
    assert (await client.post(f"/api/team/documents/{doc}/archive", headers=c["auth"], json={"reason": "   "})).status_code == 422
    archived = await client.post(f"/api/team/documents/{doc}/archive", headers=c["auth"], json={"reason": "Expirat"})
    assert archived.status_code == 200 and archived.json()["archive_reason"] == "Expirat"
    assert (await client.post(f"/api/team/documents/{doc}/archive", headers=c["auth"], json={"reason": "Din nou"})).status_code == 409
    assert (await client.get(f"/api/team/members/{c['id']}/documents", headers=c["auth"])).json() == []
    assert len((await client.get(f"/api/team/members/{c['id']}/documents", params={"include_archived": True}, headers=c["auth"])).json()) == 1


@pytest.mark.asyncio
async def test_profile_private_fields_and_role_limits(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    reception = await person(sessions, c["clinic_id"], "reception")
    own = await client.put(f"/api/team/members/{doctor['id']}/profile", headers=doctor["auth"], json={"phone": "0700000000", "emergency_contact_name": "Contact Fictiv"})
    assert own.status_code == 200 and own.json()["emergency_contact_name"] == "Contact Fictiv"
    assert (await client.put(f"/api/team/members/{doctor['id']}/profile", headers=doctor["auth"], json={"job_title": "Șef"})).status_code == 403
    assert (await client.put(f"/api/team/members/{doctor['id']}/profile", headers=reception["auth"], json={"phone": "1"})).status_code == 403
    assert (await client.put(f"/api/team/members/{doctor['id']}/profile", headers=c["auth"], json={"job_title": "Medic primar"})).json()["job_title"] == "Medic primar"
    seen = (await client.get(f"/api/team/members/{doctor['id']}/profile", headers=reception["auth"])).json()
    assert seen["job_title"] == "Medic primar" and "emergency_contact_name" not in seen and "phone" not in seen
    assert (await client.get(f"/api/team/members/{doctor['id']}/profile", headers=other["auth"])).status_code == 404
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User).where(User.id == UUID(doctor["id"]))) == 1
        assert (await session.get(User, UUID(doctor["id"]))).phone == "0700000000"


@pytest.mark.asyncio
async def test_shift_roles_overlap_and_shared_room(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    second = await person(sessions, c["clinic_id"], "doctor")
    assistant = await person(sessions, c["clinic_id"], "assistant")
    reception = await person(sessions, c["clinic_id"], "reception")
    url, day = "/api/team/shifts", "2027-01-11"
    base = shift(doctor["id"], c["room"], f"{day}T09:00", f"{day}T13:00")
    assert (await client.post(url, headers=doctor["auth"], json=base)).status_code == 403
    assert (await client.post(url, headers=reception["auth"], json=base)).status_code == 403
    assert (await client.post(url, headers=other["auth"], json=base)).status_code == 404
    assert (await client.post(url, headers=c["auth"], json={**base, "room_id": other["room"]})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={**base, "room_id": None})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json=shift(reception["id"], c["room"], f"{day}T09:00", f"{day}T13:00"))).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={**base, "local_end": f"{day}T08:00"})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={**base, "local_end": "2027-01-12T10:00"})).status_code == 422
    created = await client.post(url, headers=c["auth"], json=base)
    assert created.status_code == 201, created.text
    assert (await client.post(url, headers=c["auth"], json={**base, "local_start": f"{day}T12:00", "local_end": f"{day}T14:00"})).status_code == 409
    assert (await client.post(url, headers=c["auth"], json=shift(second["id"], c["room"], f"{day}T10:00", f"{day}T11:00"))).status_code == 409
    assert (await client.post(url, headers=c["auth"], json=shift(assistant["id"], c["room"], f"{day}T09:00", f"{day}T13:00"))).status_code == 201
    assert (await client.post(url, headers=c["auth"], json=shift(second["id"], c["room"], f"{day}T13:00", f"{day}T15:00"))).status_code == 201
    assert (await client.post(url, headers=c["auth"], json=shift(reception["id"], None, f"{day}T09:00", f"{day}T13:00"))).status_code == 201


@pytest.mark.asyncio
async def test_cancelled_shift_keeps_history_and_frees_interval(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    body = shift(doctor["id"], c["room"], "2027-01-12T09:00", "2027-01-12T13:00")
    item = (await client.post("/api/team/shifts", headers=c["auth"], json=body)).json()
    cancel = f"/api/team/shifts/{item['id']}/cancel"
    assert (await client.post(cancel, headers=other["auth"], json={"reason": "x"})).status_code == 404
    assert (await client.post(cancel, headers=doctor["auth"], json={"reason": "x"})).status_code == 403
    assert (await client.post(cancel, headers=c["auth"], json={"reason": " "})).status_code == 422
    assert (await client.post(cancel, headers=c["auth"], json={"reason": "Schimbare"})).json()["status"] == "cancelled"
    assert (await client.post(cancel, headers=c["auth"], json={"reason": "Din nou"})).status_code == 409
    assert (await client.post("/api/team/shifts", headers=c["auth"], json=body)).status_code == 201
    async with sessions() as session:
        rows = (await session.scalars(select(TeamShift))).all()
    assert sorted(row.status for row in rows) == ["active", "cancelled"]


@pytest.mark.asyncio
async def test_shift_dst_gap_fold_and_schedule_day_lengths(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    url = "/api/team/shifts"
    assert (await client.post(url, headers=c["auth"], json=shift(doctor["id"], c["room"], "2027-03-28T03:30", "2027-03-28T08:00"))).status_code == 422
    ambiguous = shift(doctor["id"], c["room"], "2027-10-31T03:30", "2027-10-31T08:00")
    assert (await client.post(url, headers=c["auth"], json=ambiguous)).status_code == 422
    first = await client.post(url, headers=c["auth"], json={**ambiguous, "start_fold": 0})
    assert first.status_code == 201 and first.json()["starts_at"] == "2027-10-31T00:30:00Z"
    await client.post(f"/api/team/shifts/{first.json()['id']}/cancel", headers=c["auth"], json={"reason": "Test"})
    second = await client.post(url, headers=c["auth"], json={**ambiguous, "start_fold": 1})
    assert second.status_code == 201 and second.json()["starts_at"] == "2027-10-31T01:30:00Z"
    assert second.json()["local_start"].endswith("+02:00")
    autumn = (await client.get("/api/team/schedule", params={"day": "2027-10-31"}, headers=doctor["auth"])).json()
    spring = (await client.get("/api/team/schedule", params={"day": "2027-03-28"}, headers=doctor["auth"])).json()
    assert autumn["days"][0]["hours"] == 25 and spring["days"][0]["hours"] == 23
    assert [item["id"] for item in autumn["shifts"]] == [second.json()["id"]]
    week = (await client.get("/api/team/schedule", params={"week_start": "2027-10-27"}, headers=doctor["auth"])).json()
    assert week["days"][0]["date"] == "2027-10-25" and len(week["days"]) == 7 and week["days"][6]["hours"] == 25
    assert (await client.get("/api/team/schedule", headers=doctor["auth"])).status_code == 422


@pytest.mark.asyncio
async def test_schedule_is_isolated_and_hides_leave_details(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    reception = await person(sessions, c["clinic_id"], "reception")
    await client.post("/api/team/shifts", headers=c["auth"], json=shift(doctor["id"], c["room"], "2027-02-01T09:00", "2027-02-01T13:00"))
    leave = (await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "medical", "starts_on": "2027-02-02", "ends_on": "2027-02-02", "reason": "Motiv medical fictiv"})).json()
    await client.post(f"/api/team/leave/{leave['id']}/approve", headers=c["auth"])
    seen = (await client.get("/api/team/schedule", params={"week_start": "2027-02-01"}, headers=reception["auth"]))
    assert len(seen.json()["shifts"]) == 1 and len(seen.json()["leaves"]) == 1
    assert "Motiv medical" not in seen.text and "medical" not in seen.text
    foreign = (await client.get("/api/team/schedule", params={"week_start": "2027-02-01"}, headers=other["auth"])).json()
    assert foreign["shifts"] == [] and foreign["leaves"] == []


@pytest.mark.asyncio
async def test_leave_approval_uses_existing_unavailability_as_only_agenda_source(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    reception = await person(sessions, c["clinic_id"], "reception")
    patient = (await client.post("/api/patients", headers=c["auth"], json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    booked = await client.post("/api/appointments", headers=c["auth"], json={"patient_id": patient["id"], "doctor_id": doctor["id"], "room_id": c["room"], "local_start": "2027-03-01T10:00:00", "duration_minutes": 30, "appointment_type": "Control"})
    assert booked.status_code == 201, booked.text
    created = await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "medical", "starts_on": "2027-03-01", "ends_on": "2027-03-02", "reason": "Motiv privat"})
    assert created.status_code == 201 and created.json()["status"] == "pending"
    leave = created.json()["id"]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ResourceUnavailability)) == 0
    assert (await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "annual", "starts_on": "2027-03-02", "ends_on": "2027-03-03"})).status_code == 409
    assert (await client.post(f"/api/team/leave/{leave}/approve", headers=doctor["auth"])).status_code == 403
    assert (await client.post(f"/api/team/leave/{leave}/approve", headers=reception["auth"])).status_code == 403
    approved = await client.post(f"/api/team/leave/{leave}/approve", headers=c["auth"])
    assert approved.status_code == 200 and approved.json()["agenda_blocked"] is True
    assert [item["id"] for item in approved.json()["affected_appointments"]] == [booked.json()["id"]]
    appointment = await client.get(f"/api/appointments/{booked.json()['id']}", headers=c["auth"])
    assert appointment.json()["status"] == "scheduled"  # reported, never changed automatically
    blocks = (await client.get("/api/availability/unavailability", headers=reception["auth"])).json()
    assert len(blocks) == 1 and blocks[0]["reason"] == "Concediu" and blocks[0]["starts_at"].startswith("2027-02-28T22:00")
    refused = await client.post("/api/appointments", headers=c["auth"], json={"patient_id": patient["id"], "doctor_id": doctor["id"], "room_id": c["room"], "local_start": "2027-03-02T10:00:00", "duration_minutes": 30, "appointment_type": "Control"})
    assert refused.status_code == 422
    assert (await client.post("/api/team/shifts", headers=c["auth"], json=shift(doctor["id"], c["room"], "2027-03-02T09:00", "2027-03-02T12:00"))).status_code == 409
    assert (await client.post(f"/api/team/leave/{leave}/cancel", headers=doctor["auth"], json={"reason": "Renunț"})).status_code == 403
    assert (await client.post(f"/api/team/leave/{leave}/cancel", headers=c["auth"], json={"reason": "Revenire"})).json()["status"] == "cancelled"
    assert (await client.get("/api/availability/unavailability", headers=c["auth"])).json() == []


@pytest.mark.asyncio
async def test_leave_rejection_visibility_and_reception_without_agenda_block(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    reception = await person(sessions, c["clinic_id"], "reception")
    assert (await client.post("/api/team/leave", headers=doctor["auth"], json={"user_id": reception["id"], "leave_type": "annual", "starts_on": "2027-04-01", "ends_on": "2027-04-01"})).status_code == 403
    assert (await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "annual", "starts_on": "2027-04-02", "ends_on": "2027-04-01"})).status_code == 422
    first = (await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "annual", "starts_on": "2027-04-01", "ends_on": "2027-04-01"})).json()["id"]
    assert (await client.post(f"/api/team/leave/{first}/reject", headers=c["auth"], json={"reason": ""})).status_code == 422
    assert (await client.post(f"/api/team/leave/{first}/approve", headers=other["auth"])).status_code == 404
    assert (await client.post(f"/api/team/leave/{first}/cancel", headers=reception["auth"], json={"reason": "x"})).status_code == 404
    assert (await client.post(f"/api/team/leave/{first}/reject", headers=c["auth"], json={"reason": "Personal insuficient"})).json()["status"] == "rejected"
    assert (await client.post(f"/api/team/leave/{first}/approve", headers=c["auth"])).status_code == 409
    assert (await client.get("/api/team/leave", headers=reception["auth"])).json() == []
    assert len((await client.get("/api/team/leave", headers=doctor["auth"])).json()) == 1
    desk = (await client.post("/api/team/leave", headers=reception["auth"], json={"leave_type": "annual", "starts_on": "2027-04-05", "ends_on": "2027-04-05"})).json()["id"]
    assert (await client.post(f"/api/team/leave/{desk}/approve", headers=c["auth"])).json()["agenda_blocked"] is False
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ResourceUnavailability)) == 0


@pytest.mark.asyncio
async def test_deleting_the_agenda_block_is_reported_on_the_leave(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    leave = (await client.post("/api/team/leave", headers=doctor["auth"], json={"leave_type": "annual", "starts_on": "2027-05-03", "ends_on": "2027-05-03"})).json()["id"]
    await client.post(f"/api/team/leave/{leave}/approve", headers=c["auth"])
    block = (await client.get("/api/availability/unavailability", headers=c["auth"])).json()[0]["id"]
    assert (await client.delete(f"/api/availability/unavailability/{block}", headers=c["auth"])).status_code == 204
    assert (await client.get("/api/team/leave", headers=doctor["auth"])).json()[0]["agenda_blocked"] is False
    assert (await client.post(f"/api/team/leave/{leave}/cancel", headers=c["auth"], json={"reason": "Curățare"})).status_code == 200


@pytest.mark.asyncio
async def test_clock_in_is_idempotent_and_clock_out_closes_once(env):
    client, sessions = env
    c = await clinic(client, sessions)
    assistant = await person(sessions, c["clinic_id"], "assistant")
    first = await client.post("/api/team/attendance/clock-in", headers=assistant["auth"])
    second = await client.post("/api/team/attendance/clock-in", headers=assistant["auth"])
    assert (first.status_code, second.status_code) == (201, 200) and first.json()["id"] == second.json()["id"]
    assert (await client.get("/api/team/attendance/me", headers=assistant["auth"])).json()["open"]["id"] == first.json()["id"]
    closed = await client.post("/api/team/attendance/clock-out", headers=assistant["auth"])
    assert closed.status_code == 200 and closed.json()["is_open"] is False and closed.json()["worked_seconds"] >= 0
    assert (await client.post("/api/team/attendance/clock-out", headers=assistant["auth"])).status_code == 409
    assert (await client.get("/api/team/attendance/me", headers=assistant["auth"])).json()["open"] is None
    assert (await client.post("/api/team/attendance/clock-in", headers=assistant["auth"])).status_code == 201


@pytest.mark.asyncio
async def test_corrections_are_new_records_with_reason_and_author(env):
    client, sessions = env
    c, other = await clinic(client, sessions), await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    item = (await client.post("/api/team/attendance/clock-in", headers=doctor["auth"])).json()
    await client.post("/api/team/attendance/clock-out", headers=doctor["auth"])
    now = datetime.now(UTC)
    url = f"/api/team/attendance/{item['id']}/corrections"
    new_in = (now - timedelta(hours=3)).isoformat()
    assert (await client.post(url, headers=doctor["auth"], json={"clock_in_at": new_in, "reason": "x"})).status_code == 403
    assert (await client.post(url, headers=other["auth"], json={"clock_in_at": new_in, "reason": "x"})).status_code == 404
    assert (await client.post(url, headers=c["auth"], json={"clock_in_at": new_in, "reason": "  "})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={"reason": "fără valori"})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={"clock_out_at": (now + timedelta(hours=1)).isoformat(), "reason": "viitor"})).status_code == 422
    assert (await client.post(url, headers=c["auth"], json={"clock_in_at": (now - timedelta(hours=30)).isoformat(), "reason": "prea lung"})).status_code == 422
    corrected = await client.post(url, headers=c["auth"], json={"clock_in_at": new_in, "reason": "A uitat să pontească"})
    assert corrected.status_code == 201, corrected.text
    body = corrected.json()
    assert body["clock_in_at"] == item["clock_in_at"] and body["effective_clock_in_at"] != item["clock_in_at"]
    assert body["corrections"][0]["reason"] == "A uitat să pontească" and body["corrections"][0]["created_by"] == "Manager Fictiv"
    again = await client.post(url, headers=c["auth"], json={"clock_in_at": (now - timedelta(hours=2)).isoformat(), "reason": "Precizare"})
    assert [row["revision"] for row in again.json()["corrections"]] == [1, 2]
    async with sessions() as session:
        original = await session.get(TeamAttendance, UUID(item["id"]))
        assert original.clock_in_at.isoformat().replace("+00:00", "Z") == item["clock_in_at"]
        assert await session.scalar(select(func.count()).select_from(TeamAttendanceCorrection)) == 2


@pytest.mark.asyncio
async def test_manager_closes_open_session_and_manual_entry_cannot_overlap(env):
    client, sessions = env
    c = await clinic(client, sessions)
    doctor = await person(sessions, c["clinic_id"], "doctor")
    now = datetime.now(UTC)
    manual = {"user_id": doctor["id"], "clock_in_at": (now - timedelta(hours=8)).isoformat(), "clock_out_at": (now - timedelta(hours=6)).isoformat(), "reason": "Pontaj uitat"}
    assert (await client.post("/api/team/attendance/manual", headers=doctor["auth"], json=manual)).status_code == 403
    created = await client.post("/api/team/attendance/manual", headers=c["auth"], json=manual)
    assert created.status_code == 201 and created.json()["source"] == "manager" and created.json()["corrections"][0]["reason"] == "Pontaj uitat"
    assert (await client.post("/api/team/attendance/manual", headers=c["auth"], json=manual)).status_code == 409
    opened = (await client.post("/api/team/attendance/clock-in", headers=doctor["auth"])).json()
    closing = await client.post(f"/api/team/attendance/{opened['id']}/corrections", headers=c["auth"], json={"clock_out_at": datetime.now(UTC).isoformat(), "reason": "A uitat ieșirea"})
    assert closing.status_code == 201 and closing.json()["is_open"] is False
    assert (await client.get("/api/team/attendance/me", headers=doctor["auth"])).json()["open"] is None
    listed = await client.get("/api/team/attendance", params={"start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(days=1)).isoformat()}, headers=c["auth"])
    assert listed.status_code == 200 and len(listed.json()) == 2
    assert (await client.get("/api/team/attendance", params={"start": now.isoformat(), "end": now.isoformat()}, headers=doctor["auth"])).status_code == 403


@pytest.mark.asyncio
async def test_attendance_is_separate_from_procedure_timers(env):
    client, sessions = env
    c = await clinic(client, sessions)
    await client.post("/api/team/attendance/clock-in", headers=c["auth"])
    await client.post("/api/team/attendance/clock-out", headers=c["auth"])
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PerformedProcedure)) == 0
        assert await session.scalar(select(func.count()).select_from(ProcedureExecutionSegment)) == 0
    referenced = {fk.column.table.name for table in (TeamAttendance.__table__, TeamAttendanceCorrection.__table__) for fk in table.foreign_keys}
    assert not referenced & {"performed_procedures", "procedure_execution_segments", "appointments"}


def test_team_tables_do_not_define_a_second_user_model():
    assert date(2027, 1, 1)  # fixed calendar import used by the suite
    team_tables = [table for name, table in Base.metadata.tables.items() if name.startswith("team_")]
    assert team_tables and not any({"email", "password_hash"} & set(table.columns.keys()) for table in team_tables)
