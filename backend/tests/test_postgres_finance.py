"""PostgreSQL proof for T13: additive migration over populated pilot data from merge_pilot_t09_t11,
schema constraints (CHECK, unique client request, single reversal) and concurrency through the
real API on separate sessions: two simultaneous reversals of one movement and two simultaneous
POSTs with the same client_request_id."""

import asyncio
import os
import subprocess
from decimal import Decimal
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres
TIMEOUT = 10
PASSWORD = "correct horse battery staple"


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t13-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t13_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_t13_migration_preserves_existing_data_and_enforces_ledger_constraints(temp_database):
    url, name = temp_database
    base = await _run_alembic(url, "merge_pilot_t09_t11")
    assert base.returncode == 0, base.stdout + base.stderr
    ids = {key: str(uuid4()) for key in ("clinic", "user", "patient", "room", "catalog", "visit", "performed",
                                          "note", "plan", "document")}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'T13 fictive', 'Europe/Bucharest', now(), now())", ids["clinic"])
        await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 't13@example.com', 'x', 'Manager Fictiv', 'clinic_manager', true, now(), now())", ids["user"], ids["clinic"])
        await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Ana', 'Fictiv', now(), now())", ids["patient"], ids["clinic"])
        await conn.execute("INSERT INTO rooms (id, clinic_id, name, is_active) VALUES ($1, $2, 'Cabinet', true)", ids["room"], ids["clinic"])
        await conn.execute("INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'OBT', 'obt', 'Obturație', 30, 250.00, 'RON', true, now(), now())", ids["catalog"], ids["clinic"])
        await conn.execute("INSERT INTO appointments (id, clinic_id, patient_id, doctor_id, room_id, starts_at, duration_minutes, status, appointment_type, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, '2026-12-01T09:00:00Z', 30, 'completed', 'Tratament', now(), now())", ids["visit"], ids["clinic"], ids["patient"], ids["user"], ids["room"])
        await conn.execute("INSERT INTO performed_procedures (id, clinic_id, appointment_id, patient_id, procedure_catalog_id, executed_by_id, procedure_code, procedure_name, procedure_base_price, procedure_currency, procedure_default_duration, tooth_fdi, status, current_price, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, 'OBT', 'Obturație', 250.00, 'RON', 30, '16', 'completed', 230.00, now(), now())", ids["performed"], ids["clinic"], ids["visit"], ids["patient"], ids["catalog"], ids["user"])
        await conn.execute("INSERT INTO clinical_notes (id, clinic_id, patient_id, author_id, note_type, content, status, is_alert, created_at, updated_at) VALUES ($1, $2, $3, $4, 'finding', 'Notă existentă', 'finalized', false, now(), now())", ids["note"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO treatment_plans (id, clinic_id, patient_id, title, status, version, created_by_id, created_at, updated_at) VALUES ($1, $2, $3, 'Plan existent', 'draft', 1, $4, now(), now())", ids["plan"], ids["clinic"], ids["patient"], ids["user"])
        await conn.execute("INSERT INTO clinical_documents (id, clinic_id, patient_id, appointment_id, category, original_filename, content_type, size_bytes, sha256, storage_key, uploaded_by_id, uploaded_at, status) VALUES ($1, $2, $3, $4, 'document', 'fictiv.pdf', 'application/pdf', 10, repeat('a', 64), $5, $6, now(), 'active')", ids["document"], ids["clinic"], ids["patient"], ids["visit"], uuid4().hex, ids["user"])
    finally:
        await conn.close()

    upgraded = await _run_alembic(url, "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        assert revision_is_in_head_lineage("t13_add_patient_financial_entries")
        assert revision_is_in_head_lineage("merge_pilot_t09_t11")
        assert await conn.fetchval("SELECT current_price FROM performed_procedures WHERE id = $1", ids["performed"]) == 230
        assert await conn.fetchval("SELECT status FROM performed_procedures WHERE id = $1", ids["performed"]) == "completed"
        assert await conn.fetchval("SELECT content FROM clinical_notes WHERE id = $1", ids["note"]) == "Notă existentă"
        assert await conn.fetchval("SELECT title FROM treatment_plans WHERE id = $1", ids["plan"]) == "Plan existent"
        assert await conn.fetchval("SELECT original_filename FROM clinical_documents WHERE id = $1", ids["document"]) == "fictiv.pdf"
        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries") == 0

        def insert(**overrides):
            row = {"id": str(uuid4()), "kind": "payment", "amount": "10.00", "currency": "RON", "method": "cash",
                   "reason": None, "reverses": None, "request": str(uuid4()), "appointment": ids["visit"]}
            row.update(overrides)
            return conn.execute(
                "INSERT INTO patient_financial_entries (id, clinic_id, patient_id, appointment_id, kind, amount, currency, method, reason, reverses_entry_id, client_request_id, created_by_id, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())",
                row["id"], ids["clinic"], ids["patient"], row["appointment"], row["kind"], Decimal(row["amount"]), row["currency"],
                row["method"], row["reason"], row["reverses"], row["request"], ids["user"])

        payment_id, request = str(uuid4()), str(uuid4())
        await insert(id=payment_id, request=request)
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert(request=request)
        for bad in ({"kind": "invoice"}, {"amount": "0"}, {"amount": "-1.00"}, {"currency": "EUR"},
                    {"method": None}, {"method": "crypto"}, {"kind": "adjustment_debit", "reason": "x"},
                    {"kind": "refund", "reason": None}, {"kind": "refund", "reason": "   "},
                    {"kind": "reversal", "method": None, "reason": "x"},
                    {"kind": "payment", "reverses": payment_id}):
            with pytest.raises(asyncpg.CheckViolationError):
                await insert(**bad)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await insert(appointment=str(uuid4()))
        await insert(kind="reversal", method=None, reason="greșit", reverses=payment_id)
        with pytest.raises(asyncpg.UniqueViolationError):
            await insert(kind="reversal", method=None, reason="a doua", reverses=payment_id)
        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries") == 2
    finally:
        await conn.close()


async def _wait_for_lock_wait(url: str, name: str) -> None:
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
    raise AssertionError("Timed out waiting for the second request to block on the movement row lock")


async def _settle(tasks: list[asyncio.Task], release: asyncio.Event) -> None:
    release.set()
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=TIMEOUT)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _setup(client) -> tuple[dict, str]:
    reg = await client.post("/api/auth/register", json={
        "clinic_name": "T13 PG", "full_name": "Manager Fictiv",
        "email": f"t13-pg-{uuid4().hex[:8]}@example.com", "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "PG"})
    assert patient.status_code == 201, patient.text
    return auth, patient.json()["id"]


@pytest.fixture
async def pg_client(temp_database):
    url, name = temp_database
    migrated = await _run_alembic(url, "head")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=TIMEOUT * 3) as client:
            yield client, url, name
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


async def test_two_simultaneous_reversals_of_one_movement_only_one_succeeds(pg_client, monkeypatch):
    """The first reversal holds the movement row lock (SELECT ... FOR UPDATE) at a controlled point;
    the second must observably wait on that lock and then see the committed reversal (409)."""
    from app.finance import router as finance_router

    client, url, name = pg_client
    auth, patient = await _setup(client)
    paid = await client.post(f"/api/finance/patients/{patient}/entries", headers=auth, json={
        "kind": "payment", "amount": "120.00", "method": "cash", "client_request_id": str(uuid4())})
    assert paid.status_code == 201, paid.text
    entry_id = paid.json()["id"]

    reached, release = asyncio.Event(), asyncio.Event()
    original = finance_router._lock_entry

    async def lock_then_hold(db, clinic_id, target_id):
        row = await original(db, clinic_id, target_id)
        if not reached.is_set():
            reached.set()
            await asyncio.wait_for(release.wait(), TIMEOUT)
        return row

    monkeypatch.setattr(finance_router, "_lock_entry", lock_then_hold)

    def reverse(reason: str):
        return client.post(f"/api/finance/entries/{entry_id}/reverse", headers=auth,
                           json={"reason": reason, "client_request_id": str(uuid4())})

    tasks: list[asyncio.Task] = []
    try:
        first = asyncio.create_task(reverse("prima anulare"))
        tasks.append(first)
        await asyncio.wait_for(reached.wait(), TIMEOUT)
        second = asyncio.create_task(reverse("a doua anulare"))
        tasks.append(second)
        await _wait_for_lock_wait(url, name)
        assert not second.done()
        release.set()
        first_response = await asyncio.wait_for(first, TIMEOUT)
        second_response = await asyncio.wait_for(second, TIMEOUT)
    finally:
        await _settle(tasks, release)

    assert first_response.status_code == 201, first_response.text
    assert second_response.status_code == 409, second_response.text
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        rows = await conn.fetch("SELECT reason FROM patient_financial_entries WHERE reverses_entry_id = $1", entry_id)
    finally:
        await conn.close()
    assert [r["reason"] for r in rows] == ["prima anulare"]
    account = (await client.get(f"/api/finance/patients/{patient}/account", headers=auth)).json()
    assert account["totals"]["payments"] == "0.00" and account["totals"]["balance"] == "0.00"


async def test_two_simultaneous_posts_with_one_client_request_id_persist_one_movement(pg_client, monkeypatch):
    """Both requests pass the idempotency pre-check before either inserts (controlled point); the
    unique (clinic_id, client_request_id) constraint makes the late insert fall back to the existing row."""
    from app.finance import router as finance_router

    client, url, name = pg_client
    auth, patient = await _setup(client)
    first_reached, second_reached, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def gate() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_reached.set()
            await asyncio.wait_for(release.wait(), TIMEOUT)
        elif calls == 2:
            second_reached.set()

    monkeypatch.setattr(finance_router, "_after_idempotency_check", gate)
    key = str(uuid4())

    def post():
        return client.post(f"/api/finance/patients/{patient}/entries", headers=auth, json={
            "kind": "payment", "amount": "55.50", "method": "card", "client_request_id": key})

    tasks: list[asyncio.Task] = []
    try:
        first = asyncio.create_task(post())
        tasks.append(first)
        await asyncio.wait_for(first_reached.wait(), TIMEOUT)
        second = asyncio.create_task(post())
        tasks.append(second)
        await asyncio.wait_for(second_reached.wait(), TIMEOUT)
        second_response = await asyncio.wait_for(second, TIMEOUT)
        release.set()
        first_response = await asyncio.wait_for(first, TIMEOUT)
    finally:
        await _settle(tasks, release)

    assert second_response.status_code == 201, second_response.text
    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["id"] == second_response.json()["id"]
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries WHERE client_request_id = $1", key) == 1
    finally:
        await conn.close()
