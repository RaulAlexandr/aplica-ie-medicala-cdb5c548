"""PostgreSQL proof for T11: additive migration over existing T00-T08 data, schema constraints and
patient/visit/tooth references through the real API on PostgreSQL."""

import asyncio
import os
import struct
import subprocess
import zlib
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t11-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + b"\x00" * 6 + b"\x00" + b"\x00" * 6)) + chunk(b"IEND", b""))


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t11_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_t11_migration_preserves_existing_data_and_enforces_document_constraints(temp_database):
    url, name = temp_database
    base = await _run_alembic(url, "t08_add_treatment_plans")
    assert base.returncode == 0, base.stdout + base.stderr
    ids = {key: str(uuid4()) for key in ("clinic", "user", "patient", "other_patient", "room", "catalog", "visit",
                                          "performed", "note", "finding", "plan", "stage")}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'T11 fictive', 'Europe/Bucharest', now(), now())", ids["clinic"])
        await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 't11@example.com', 'x', 'Doctor Fictiv', 'doctor', true, now(), now())", ids["user"], ids["clinic"])
        for key, first in (("patient", "Ana"), ("other_patient", "Bianca")):
            await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, $3, 'Fictiv', now(), now())", ids[key], ids["clinic"], first)
        await conn.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Cabinet', true)", ids["room"], ids["clinic"])
        await conn.execute("INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'OBT', 'obt', 'Obturație', 30, 250.00, 'RON', true, now(), now())", ids["catalog"], ids["clinic"])
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, '2026-12-01T09:00:00Z', 30, 'completed', 'Tratament', now(), now())", ids["visit"], ids["clinic"], ids["patient"], ids["user"], ids["room"])
        await conn.execute("INSERT INTO performed_procedures (id, clinic_id, appointment_id, patient_id, procedure_catalog_id, executed_by_id, procedure_code, procedure_name, procedure_base_price, procedure_currency, procedure_default_duration, tooth_fdi, status, current_price, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'OBT', 'Obturație', 250.00, 'RON', 30, '16', 'completed', 250.00, now(), now())", ids["performed"], ids["clinic"], ids["visit"], ids["patient"], ids["catalog"], ids["user"])
        await conn.execute("INSERT INTO clinical_notes (id, clinic_id, patient_id, author_id, note_type, content, status, is_alert, created_at, updated_at) VALUES ($1, $2, $3, $4, 'finding', 'Notă existentă', 'finalized', false, now(), now())", ids["note"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO odontogram_findings (id, clinic_id, patient_id, tooth_fdi, kind, description, surfaces, status, observed_at, created_at, updated_at, author_id) VALUES ($1, $2, $3, '16', 'caries', 'Fictiv', '[\"occlusal\"]', 'finalized', now(), now(), now(), $4)", ids["finding"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO treatment_plans (id, clinic_id, patient_id, title, status, version, created_by_id, created_at, updated_at) VALUES ($1, $2, $3, 'Plan existent', 'draft', 1, $4, now(), now())", ids["plan"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO treatment_plan_stages (id, clinic_id, plan_id, position, title, created_at, updated_at) VALUES ($1, $2, $3, 1, 'E1', now(), now())", ids["stage"], ids["clinic"], ids["plan"])
    finally:
        await conn.close()

    upgraded = await _run_alembic(url, "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        assert revision_is_in_head_lineage("t11_add_clinical_documents")
        assert revision_is_in_head_lineage("t08_add_treatment_plans")
        assert await conn.fetchval("SELECT content FROM clinical_notes WHERE id = $1", ids["note"]) == "Notă existentă"
        assert await conn.fetchval("SELECT status FROM performed_procedures WHERE id = $1", ids["performed"]) == "completed"
        assert await conn.fetchval("SELECT description FROM odontogram_findings WHERE id = $1", ids["finding"]) == "Fictiv"
        assert await conn.fetchval("SELECT title FROM treatment_plans WHERE id = $1", ids["plan"]) == "Plan existent"
        assert await conn.fetchval("SELECT count(*) FROM clinical_documents") == 0

        def insert(**overrides):
            row = {"id": str(uuid4()), "appointment_id": ids["visit"], "tooth_fdi": "16", "category": "radiograph",
                   "content_type": "image/png", "size_bytes": 10, "storage_key": uuid4().hex, "status": "active"}
            row.update(overrides)
            return conn.execute(
                "INSERT INTO clinical_documents (id, clinic_id, patient_id, appointment_id, tooth_fdi, category, original_filename, content_type, size_bytes, sha256, storage_key, uploaded_by_id, uploaded_at, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'fictiv.png', $7, $8, repeat('0', 64), $9, $10, now(), $11)",
                row["id"], ids["clinic"], ids["patient"], row["appointment_id"], row["tooth_fdi"], row["category"],
                row["content_type"], row["size_bytes"], row["storage_key"], ids["user"], row["status"])

        key = uuid4().hex
        await insert(storage_key=key)
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert(storage_key=key)
        for bad in ({"tooth_fdi": "19"}, {"tooth_fdi": "51"}, {"category": "dicom"}, {"content_type": "image/svg+xml"},
                    {"size_bytes": 0}, {"status": "archived"}):
            with pytest.raises(asyncpg.CheckViolationError):
                await insert(**bad)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await insert(appointment_id=str(uuid4()))
        assert await conn.fetchval("SELECT count(*) FROM clinical_documents") == 1
    finally:
        await conn.close()


async def test_t11_api_references_and_persistence_on_postgresql(temp_database, tmp_path, monkeypatch):
    url, name = temp_database
    migrated = await _run_alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    monkeypatch.setattr(settings, "document_storage_dir", str(tmp_path / "store"))
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def register(tag: str) -> dict:
                reg = await client.post("/api/auth/register", json={
                    "clinic_name": f"T11 PG {tag}", "full_name": "Manager Fictiv",
                    "email": f"t11-{tag}-{uuid4().hex[:8]}@example.com", "password": "correct horse battery staple"})
                assert reg.status_code == 201, reg.text
                auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}
                me = (await client.get("/api/auth/me", headers=auth)).json()
                room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet PG"})).json()["id"]
                return {"auth": auth, "me": me, "room": room}

            a, b = await register("a"), await register("b")

            async def patient(ctx, first):
                return (await client.post("/api/patients", headers=ctx["auth"], json={"first_name": first, "last_name": "PG"})).json()["id"]

            async def visit(ctx, patient_id, starts_at):
                response = await client.post("/api/appointments", headers=ctx["auth"], json={
                    "patient_id": patient_id, "doctor_id": ctx["me"]["id"], "room_id": ctx["room"],
                    "starts_at": starts_at, "duration_minutes": 30, "appointment_type": "Control"})
                assert response.status_code == 201, response.text
                return response.json()["id"]

            p1, p2, pb = await patient(a, "Ana"), await patient(a, "Bianca"), await patient(b, "Corina")
            slot_9, slot_10 = "2026-12-05T09:00:00+00:00", "2026-12-05T10:00:00+00:00"
            v1, v2, vb = await visit(a, p1, slot_9), await visit(a, p2, slot_10), await visit(b, pb, slot_9)

            async def upload(ctx, patient_id, **params):
                return await client.post(f"/api/documents/patients/{patient_id}", headers={
                    **ctx["auth"], "Content-Type": "application/octet-stream", "X-File-Name": "rx.png"},
                    params={"category": "radiograph", **params}, content=_png())

            ok = await upload(a, p1, appointment_id=v1, tooth_fdi="36")
            assert ok.status_code == 201, ok.text
            assert (await upload(a, p1, appointment_id=v2)).status_code == 422
            assert (await upload(a, p1, appointment_id=vb)).status_code == 422
            assert (await upload(a, p1, tooth_fdi="49")).status_code == 422
            assert (await upload(b, p1)).status_code == 404
            assert (await client.get(f"/api/documents/{ok.json()['id']}/content", headers=b["auth"])).status_code == 404
            listed = await client.get(f"/api/documents/patients/{p1}", headers=a["auth"])
            assert [(row["appointment"]["id"], row["tooth_fdi"]) for row in listed.json()] == [(v1, "36")]
            content = await client.get(f"/api/documents/{ok.json()['id']}/content", headers=a["auth"])
            assert content.status_code == 200 and content.content == _png()

        check = await asyncpg.connect(_dsn(url, name))
        try:
            assert await check.fetchval("SELECT count(*) FROM clinical_documents") == 1
            assert await check.fetchval("SELECT count(*) FROM performed_procedures") == 0
        finally:
            await check.close()
        assert len([p for p in (tmp_path / "store").rglob("*") if p.is_file()]) == 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


TIMEOUT = 10


async def _wait_for_lock_wait(url: str, name: str) -> None:
    """Controlled point: return once a backend of this database waits on a row lock."""
    deadline = asyncio.get_running_loop().time() + TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        conn = await asyncpg.connect(_dsn(url, "postgres"))
        try:
            waiting = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = $1 AND wait_event_type = 'Lock')", name)
        finally:
            await conn.close()
        if waiting:
            return
        await asyncio.sleep(0.025)
    raise AssertionError("Timed out waiting for the second request to block on the appointment row lock")


@pytest.mark.parametrize("winner", ["upload", "reassign"])
async def test_upload_and_visit_reassignment_are_serialized_on_the_appointment_row(
        temp_database, tmp_path, monkeypatch, winner):
    """Two HTTP requests on separate sessions. The first holds the appointment row lock at a
    controlled point (asyncio.Event); the second must observably wait on that lock."""
    from app.appointments import router as appointments_router

    url, name = temp_database
    migrated = await _run_alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    store = tmp_path / "store"
    monkeypatch.setattr(settings, "document_storage_dir", str(store))
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    reached, release = asyncio.Event(), asyncio.Event()

    async def hold_once() -> None:
        """Gate for the first operation only: signal, then wait for the test to release it."""
        if reached.is_set():
            return
        reached.set()
        await asyncio.wait_for(release.wait(), TIMEOUT)

    def install_gate() -> None:
        # Installed only after the initial data exists: creating the appointment also calls
        # validate_slot and must never reach the gate.
        if winner == "upload":
            from app.documents import router as documents_router
            original_validate = documents_router._validate_refs

            async def validate_then_hold(db, clinic_id, patient_id, appointment_id, *, lock=False):
                await original_validate(db, clinic_id, patient_id, appointment_id, lock=lock)
                if lock:  # the authoritative check holds the appointment row lock here, before commit
                    await hold_once()

            monkeypatch.setattr(documents_router, "_validate_refs", validate_then_hold)
        else:
            original_slot = appointments_router.validate_slot

            async def slot_then_hold(*args, **kwargs):  # PATCH holds the row lock here, before commit
                await original_slot(*args, **kwargs)
                await hold_once()

            monkeypatch.setattr(appointments_router, "validate_slot", slot_then_hold)

    async def settle(tasks: list[asyncio.Task]) -> None:
        """Release the gate, then let started requests finish (bounded) or cancel them, and await
        them, so no request still uses a session when the client/engine are closed."""
        release.set()
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=TIMEOUT)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    tasks: list[asyncio.Task] = []
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=TIMEOUT * 3) as client:
            try:
                reg = await client.post("/api/auth/register", json={
                    "clinic_name": "T11 PG lock", "full_name": "Manager Fictiv",
                    "email": f"t11-lock-{uuid4().hex[:8]}@example.com", "password": "correct horse battery staple"})
                assert reg.status_code == 201, reg.text
                auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}
                me = (await client.get("/api/auth/me", headers=auth)).json()
                room = (await client.post("/api/rooms", headers=auth, json={"name": "Cabinet"})).json()["id"]
                pa = (await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "PG"})).json()["id"]
                pb = (await client.post("/api/patients", headers=auth, json={"first_name": "Bianca", "last_name": "PG"})).json()["id"]
                visit = await client.post("/api/appointments", headers=auth, json={
                    "patient_id": pa, "doctor_id": me["id"], "room_id": room, "starts_at": "2026-12-07T09:00:00+00:00",
                    "duration_minutes": 30, "appointment_type": "Control"})
                assert visit.status_code == 201, visit.text
                vid = visit.json()["id"]
                assert not reached.is_set(), "the gate must not be reached during setup"

                def do_upload():
                    return client.post(f"/api/documents/patients/{pa}", headers={
                        **auth, "Content-Type": "application/octet-stream", "X-File-Name": "rx.png"},
                        params={"category": "radiograph", "appointment_id": vid}, content=_png())

                def do_reassign():
                    return client.patch(f"/api/appointments/{vid}", headers=auth, json={"patient_id": pb})

                install_gate()
                first, second = (do_upload, do_reassign) if winner == "upload" else (do_reassign, do_upload)
                first_task = asyncio.create_task(first())
                tasks.append(first_task)
                await asyncio.wait_for(reached.wait(), TIMEOUT)
                second_task = asyncio.create_task(second())
                tasks.append(second_task)
                await _wait_for_lock_wait(url, name)
                assert not second_task.done()
                release.set()
                first_response = await asyncio.wait_for(first_task, TIMEOUT)
                second_response = await asyncio.wait_for(second_task, TIMEOUT)
            finally:
                await settle(tasks)

        files = [p for p in store.rglob("*") if p.is_file()] if store.exists() else []
        check = await asyncpg.connect(_dsn(url, name))
        try:
            visit_patient = str(await check.fetchval("SELECT patient_id FROM appointments WHERE id = $1", vid))
            docs = await check.fetch("SELECT patient_id, appointment_id FROM clinical_documents")
            uploaded = await check.fetchval(
                "SELECT count(*) FROM audit_events WHERE entity_type = 'clinical_document' AND action = 'uploaded'")
        finally:
            await check.close()
        if winner == "upload":
            assert first_response.status_code == 201, first_response.text
            assert second_response.status_code == 409, second_response.text
            assert visit_patient == pa
            assert [(str(r["patient_id"]), str(r["appointment_id"])) for r in docs] == [(pa, vid)]
            assert uploaded == 1 and len(files) == 1
        else:
            assert first_response.status_code == 200, first_response.text
            assert second_response.status_code == 422, second_response.text
            assert visit_patient == pb
            assert docs == [] and uploaded == 0 and files == []
    finally:
        release.set()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()
