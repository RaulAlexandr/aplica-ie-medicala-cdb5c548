"""Every datetime in every API response is ISO 8601 with an explicit offset, on SQLite too.

SQLite stores timestamptz wall time without an offset. A browser on Europe/Bucharest reads an
offset-less value as local time (2 hours off). These tests drive representative flows over HTTP on
SQLite, check each datetime string, and verify that instants (including an input sent with +02:00)
survive unchanged. PostgreSQL is covered by CI, not here.
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Appointment, Base, UTCDateTime, get_db
from app.main import app
from tests.test_documents import png, storage_quote
from tests.test_treatment_plans import catalog, h, patient, plan_payload

DATETIME_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
OFFSET = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def datetimes(value, path="$"):
    """Yield (path, string) for every ISO datetime-looking string in a JSON value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from datetimes(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from datetimes(item, f"{path}[{index}]")
    elif isinstance(value, str) and DATETIME_TEXT.match(value):
        yield path, value


class Checker:
    def __init__(self):
        self.seen = 0

    def check(self, response, expected_status=None, label=""):
        if expected_status is not None:
            assert response.status_code == expected_status, f"{label}: {response.status_code} {response.text}"
        for path, raw in datetimes(response.json()):
            assert OFFSET.search(raw), f"{label} {response.request.url.path} {path}={raw!r} has no UTC offset"
            parsed = datetime.fromisoformat(raw)
            assert parsed.utcoffset() is not None
            self.seen += 1
        return response


def instant(raw: str) -> datetime:
    return datetime.fromisoformat(raw).astimezone(UTC)


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_all_representative_flows_return_offset_datetimes_with_correct_instants(env):
    client, sessions = env
    ck = Checker()
    reg = ck.check(await client.post("/api/auth/register", json={
        "clinic_name": "Clinica UTC", "full_name": "Doctor Fictiv", "email": f"utc-{uuid4().hex[:6]}@example.com",
        "password": "correct horse battery staple"}), 201, "register")
    auth = h(reg.json()["access_token"])
    me = ck.check(await client.get("/api/auth/me", headers=auth), 200, "me").json()
    room = ck.check(await client.post("/api/rooms", headers=auth, json={"name": "Cabinet UTC"}), 201, "room").json()["id"]
    patient_id = await patient(client, auth)
    cat = await catalog(client, auth, "UTC1")

    # T02: input with +02:00 is the same instant; stored wall time is UTC.
    sent = "2026-12-01T11:00:00+02:00"
    appt = ck.check(await client.post("/api/appointments", headers=auth, json={
        "patient_id": patient_id, "doctor_id": me["id"], "room_id": room, "starts_at": sent,
        "duration_minutes": 60, "appointment_type": "Tratament"}), 201, "T02 create").json()
    assert instant(appt["starts_at"]) == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    assert instant(appt["ends_at"]) == datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
    ck.check(await client.get(f"/api/appointments/{appt['id']}", headers=auth), 200, "T02 get")
    ck.check(await client.get("/api/appointments", headers=auth, params={"start": "2026-11-30T00:00:00+00:00", "end": "2026-12-03T00:00:00+00:00"}), None, "T02 list")
    async with sessions() as session:
        raw = (await session.execute(text("SELECT starts_at FROM appointments"))).scalar_one()
    assert raw.startswith("2026-12-01 09:00:00"), raw  # UTC wall time, not 11:00

    # T02 unavailability: a +02:00 input used to be stored as local wall time on SQLite (pre-fix finding).
    ck.check(await client.post("/api/availability/unavailability", headers=auth, json={
        "resource_type": "room", "resource_id": room, "starts_at": "2026-12-05T12:00:00+02:00",
        "ends_at": "2026-12-05T13:00:00+02:00", "reason": "Mentenanță"}), 201, "T02 unavailability")
    async with sessions() as session:
        raw = (await session.execute(text("SELECT starts_at, ends_at FROM resource_unavailability"))).one()
    assert raw[0].startswith("2026-12-05 10:00:00") and raw[1].startswith("2026-12-05 11:00:00"), raw

    # T04: created -> started -> paused -> resumed -> completed, plus segments and summary.
    before = datetime.now(UTC)
    proc = ck.check(await client.post("/api/performed-procedures", headers=auth, json={
        "appointment_id": appt["id"], "procedure_catalog_id": cat, "tooth_fdi": "16", "tooth_surface": "occlusal"}), 201, "T04 create").json()
    pid = proc["id"]
    for action in ("start", "pause", "resume", "complete"):
        done = ck.check(await client.post(f"/api/performed-procedures/{pid}/{action}", headers=auth), 200, f"T04 {action}")
    for field in ("started_at", "completed_at"):
        if done.json().get(field):
            assert abs(instant(done.json()[field]) - before) < timedelta(minutes=2), field
    segments = ck.check(await client.get(f"/api/performed-procedures/{pid}/segments", headers=auth), 200, "T04 segments").json()
    assert len(segments) >= 2 and all(OFFSET.search(s["started_at"]) for s in segments)
    ck.check(await client.get(f"/api/performed-procedures/{pid}/revisions", headers=auth), 200, "T04 revisions")
    ck.check(await client.get(f"/api/performed-procedures/visit/{appt['id']}/summary", headers=auth), 200, "T04 summary")

    # T05: note + timeline.
    note = ck.check(await client.post("/api/clinical-notes", headers=auth, json={
        "patient_id": patient_id, "note_type": "finding", "title": "Examen", "content": "Observație fictivă.",
        "tooth_fdi": "16", "tooth_surface": "occlusal"}), 201, "T05 note").json()
    ck.check(await client.post(f"/api/clinical-notes/{note['id']}/finalize", headers=auth), 200, "T05 finalize")
    ck.check(await client.get(f"/api/clinical-notes/patients/{patient_id}/timeline", headers=auth), 200, "T05 timeline")

    # T06: odontogram finding and tooth state.
    ck.check(await client.put(f"/api/odontogram/patients/{patient_id}/teeth/17", headers=auth, json={"status": "missing"}), 200, "T06 tooth")
    finding = ck.check(await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers={**auth, "Idempotency-Key": str(uuid4())},
        json={"toothId": "18", "kind": "caries", "description": "Suspiciune fictivă", "surfaces": ["occlusal"]}), 201, "T06 finding").json()
    ck.check(await client.get(f"/api/odontogram/patients/{patient_id}", headers=auth), 200, "T06 get")
    assert instant(finding["observedAt"]) - before < timedelta(minutes=2)

    # T08: plan, accept.
    c2 = await catalog(client, auth, "UTC2")
    plan = ck.check(await client.post(f"/api/treatment-plans/patients/{patient_id}", headers=auth, json=plan_payload(cat, c2)), 201, "T08 create").json()
    ck.check(await client.post(f"/api/treatment-plans/{plan['id']}/accept", headers=auth, json={"note": "Acord"}), 200, "T08 accept")
    ck.check(await client.get(f"/api/treatment-plans/{plan['id']}", headers=auth), 200, "T08 get")

    # T09: periodontal assessment finalized.
    sites = ["buccal_mesial", "buccal_mid", "buccal_distal", "oral_mesial", "oral_mid", "oral_distal"]
    tooth = {"tooth_fdi": "16", "is_excluded": False, "mobility": "0", "furcation": None, "sites": [
        {"site": s, "probing_depth_mm": 2 if i == 0 else None, "recession_mm": 0 if i == 0 else None,
         "bop": False if i == 0 else None, "plaque": None, "suppuration": None} for i, s in enumerate(sites)]}
    assessment = ck.check(await client.post(f"/api/periodontal/patients/{patient_id}/assessments", headers=auth, json={"clinical_date": "2026-11-19"}), 201, "T09 create").json()
    saved = await client.put(f"/api/periodontal/assessments/{assessment['id']}/measurements", headers=auth, json={"teeth": [tooth]})
    ck.check(saved, 200, "T09 save")
    ck.check(await client.post(f"/api/periodontal/assessments/{assessment['id']}/finalize", headers=auth), 200, "T09 finalize")
    ck.check(await client.get(f"/api/periodontal/patients/{patient_id}/assessments", headers=auth), 200, "T09 list")

    # T11: document linked to the visit.
    doc = await client.post(f"/api/documents/patients/{patient_id}", params={"category": "radiograph", "appointment_id": appt["id"], "tooth_fdi": "16"},
        headers={**auth, "Content-Type": "application/octet-stream", "X-File-Name": storage_quote("rx.png")}, content=png())
    ck.check(doc, 201, "T11 upload")
    ck.check(await client.get(f"/api/documents/patients/{patient_id}", headers=auth), 200, "T11 list")

    assert ck.seen > 60, ck.seen  # the walk really found datetimes everywhere


async def test_naive_values_in_existing_sqlite_rows_read_as_utc_and_writes_are_converted(env):
    client, sessions = env
    # Unit-level contract of the type.
    t = UTCDateTime()
    naive = datetime(2026, 11, 19, 17, 48, 7)
    assert t.process_result_value(naive, None) == naive.replace(tzinfo=UTC)
    assert t.process_result_value(naive, None).utcoffset() == timedelta(0)
    plus2 = datetime(2026, 11, 19, 19, 48, 7, tzinfo=timezone(timedelta(hours=2)))
    assert t.process_bind_param(plus2, None) == datetime(2026, 11, 19, 17, 48, 7, tzinfo=UTC)
    assert t.process_bind_param(naive, None) == naive.replace(tzinfo=UTC)  # naive on write = UTC (documented)
    assert t.process_bind_param(None, None) is None and t.process_result_value(None, None) is None

    # Existing database written before the fix: offset-less wall time (UTC) read through the ORM.
    reg = await client.post("/api/auth/register", json={
        "clinic_name": "Clinica Veche", "full_name": "Doctor Vechi", "email": f"old-{uuid4().hex[:6]}@example.com",
        "password": "correct horse battery staple"})
    auth = h(reg.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": "C"})).json()["id"]
    pid = await patient(client, auth)
    appt = (await client.post("/api/appointments", headers=auth, json={
        "patient_id": pid, "doctor_id": me["id"], "room_id": room, "starts_at": "2026-12-02T09:00:00+00:00",
        "duration_minutes": 30, "appointment_type": "Control"})).json()
    async with sessions() as session:
        await session.execute(text("UPDATE appointments SET created_at = '2026-11-19 17:48:07.000000'"))
        await session.commit()
    async with sessions() as session:
        row = await session.get(Appointment, __import__("uuid").UUID(appt["id"]))
        assert row.created_at == datetime(2026, 11, 19, 17, 48, 7, tzinfo=UTC)
        # Assignment is normalized in memory too (no offset-less value can reach a serializer).
        row.starts_at = datetime(2026, 12, 2, 11, 0, tzinfo=timezone(timedelta(hours=2)))
        assert row.starts_at == datetime(2026, 12, 2, 9, 0, tzinfo=UTC) and row.starts_at.utcoffset() == timedelta(0)
        row.updated_at = datetime(2026, 12, 2, 9, 0)
        assert row.updated_at.tzinfo is UTC or row.updated_at.utcoffset() == timedelta(0)
    got = await client.get(f"/api/appointments/{appt['id']}", headers=auth)
    assert all(OFFSET.search(raw) for _, raw in datetimes(got.json()))
