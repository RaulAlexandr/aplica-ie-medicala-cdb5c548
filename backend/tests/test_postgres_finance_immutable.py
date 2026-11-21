"""CUR-04 PostgreSQL proof: T13 financial movements are append-only at the database level.

UPDATE and DELETE (also raw SQL that bypasses the ORM) and TRUNCATE are refused; INSERT of a new
movement, including a reversal, still works; downgrade removes the trigger cleanly and upgrade
restores it.
"""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest

from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres
IMMUTABLE_REVISION = "t13_finance_immutable"
PREVIOUS_REVISION = "t16_waitlist_recalls"
TRIGGERS = ("trg_patient_financial_entries_immutable", "trg_patient_financial_entries_no_truncate")


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "cur04-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run, ["alembic", *args], cwd=os.path.dirname(__file__) + "/..",
        env=env, capture_output=True, text=True, check=False,
    )


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_cur04_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _seed(conn) -> dict:
    ids = {key: str(uuid4()) for key in ("clinic", "user", "patient")}
    await conn.execute("INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ($1, 'CUR04 fictiv', 'Europe/Bucharest', now(), now())", ids["clinic"])
    await conn.execute("INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 'cur04@example.com', 'x', 'Manager Fictiv', 'clinic_manager', true, now(), now())", ids["user"], ids["clinic"])
    await conn.execute("INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Ana', 'Fictiv', now(), now())", ids["patient"], ids["clinic"])
    return ids


def _insert(conn, ids: dict, **overrides):
    row = {"id": str(uuid4()), "kind": "payment", "method": "cash", "reason": None, "reverses": None}
    row.update(overrides)
    return conn.execute(
        "INSERT INTO patient_financial_entries (id, clinic_id, patient_id, kind, amount, currency, method, reason, reverses_entry_id, client_request_id, created_by_id, created_at) "
        "VALUES ($1, $2, $3, $4, 10.00, 'RON', $5, $6, $7, $8, $9, now())",
        row["id"], ids["clinic"], ids["patient"], row["kind"], row["method"], row["reason"], row["reverses"],
        str(uuid4()), ids["user"])


async def _trigger_count(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname = ANY($1::text[]) AND NOT tgisinternal",
                               list(TRIGGERS))


async def test_financial_movements_are_append_only_in_postgresql(temp_database):
    url, name = temp_database
    upgraded = await _alembic(url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert configured_alembic_head() == IMMUTABLE_REVISION or revision_is_in_head_lineage(IMMUTABLE_REVISION)

    conn = await asyncpg.connect(_dsn(url, name))
    try:
        ids = await _seed(conn)
        payment_id = str(uuid4())
        await _insert(conn, ids, id=payment_id)
        # Legitimate flow stays intact: a reversal is a NEW row, the original is never modified.
        await _insert(conn, ids, kind="reversal", method=None, reason="greșit", reverses=payment_id)
        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries") == 2

        refused = (
            "UPDATE patient_financial_entries SET amount = 99.00",
            "UPDATE patient_financial_entries SET reason = 'rescris' WHERE id = '%s'" % payment_id,
            "UPDATE patient_financial_entries SET created_at = now() WHERE kind = 'payment'",
            "DELETE FROM patient_financial_entries WHERE id = '%s'" % payment_id,
            "DELETE FROM patient_financial_entries",
            "TRUNCATE patient_financial_entries",
        )
        for statement in refused:
            with pytest.raises(asyncpg.RestrictViolationError):
                await conn.execute(statement)
        # An UPDATE that changes nothing is refused as well (no hidden tolerated transition).
        with pytest.raises(asyncpg.RestrictViolationError):
            await conn.execute("UPDATE patient_financial_entries SET amount = amount WHERE id = $1", payment_id)

        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries") == 2
        assert await conn.fetchval("SELECT amount FROM patient_financial_entries WHERE id = $1", payment_id) == 10
        # Unrelated tables are untouched by the trigger.
        await conn.execute("UPDATE patients SET first_name = 'Ioana' WHERE id = $1", ids["patient"])
    finally:
        await conn.close()


async def test_downgrade_removes_trigger_cleanly_and_upgrade_restores_it(temp_database):
    url, name = temp_database
    assert (await _alembic(url, "upgrade", "head")).returncode == 0
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await _trigger_count(conn) == 2
        ids = await _seed(conn)
        entry = str(uuid4())
        await _insert(conn, ids, id=entry)
    finally:
        await conn.close()

    down = await _alembic(url, "downgrade", PREVIOUS_REVISION)
    assert down.returncode == 0, down.stdout + down.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await _trigger_count(conn) == 0
        assert await conn.fetchval("SELECT count(*) FROM pg_proc WHERE proname = 'patient_financial_entries_immutable'") == 0
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == PREVIOUS_REVISION
        # Data survived the downgrade and the table is writable again.
        assert await conn.fetchval("SELECT count(*) FROM patient_financial_entries WHERE id = $1", entry) == 1
        await conn.execute("UPDATE patient_financial_entries SET amount = 11.00 WHERE id = $1", entry)
    finally:
        await conn.close()

    again = await _alembic(url, "upgrade", "head")
    assert again.returncode == 0, again.stdout + again.stderr
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await _trigger_count(conn) == 2
        with pytest.raises(asyncpg.RestrictViolationError):
            await conn.execute("DELETE FROM patient_financial_entries WHERE id = $1", entry)
    finally:
        await conn.close()
