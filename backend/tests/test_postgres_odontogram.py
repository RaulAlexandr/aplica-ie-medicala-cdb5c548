"""PostgreSQL migration proof for the T06 odontogram schema."""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, target: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t06-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run,
        ["alembic", "upgrade", target],
        cwd=os.path.dirname(__file__) + "/..",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_t06_migration_upgrades_merge_head_and_preserves_existing_clinical_data():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t06_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    target_url = source.rsplit("/", 1)[0] + f"/{name}"
    connection = None
    try:
        base = await _run_alembic(target_url, "merge_t02_t05")
        assert base.returncode == 0, base.stdout + base.stderr
        connection = await asyncpg.connect(_dsn(target_url, name))
        clinic_id, patient_id, user_id, note_id = [str(uuid4()) for _ in range(4)]
        await connection.execute("INSERT INTO clinics (id, name, created_at, updated_at) VALUES ($1, 'T06 fictive clinic', now(), now())", clinic_id)
        await connection.execute(
            "INSERT INTO users (id, clinic_id, email, password_hash, full_name, role, is_active, created_at, updated_at) VALUES ($1, $2, 'doctor@example.com', 'x', 'Doctor Fictional', 'doctor', true, now(), now())",
            user_id, clinic_id,
        )
        await connection.execute(
            "INSERT INTO patients (id, clinic_id, first_name, last_name, created_at, updated_at) VALUES ($1, $2, 'Patient', 'Fictional', now(), now())",
            patient_id, clinic_id,
        )
        await connection.execute(
            "INSERT INTO clinical_notes (id, clinic_id, patient_id, author_id, note_type, content, status, is_alert, created_at, updated_at) VALUES ($1, $2, $3, $4, 'finding', 'Existing fictional note', 'draft', false, now(), now())",
            note_id, clinic_id, patient_id, user_id,
        )
        await connection.close()
        connection = None

        upgraded = await _run_alembic(target_url, "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        connection = await asyncpg.connect(_dsn(target_url, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "t06_add_persisted_odontogram"
        assert await connection.fetchval("SELECT content FROM clinical_notes WHERE id = $1", note_id) == "Existing fictional note"
        expected_tables = {
            "odontogram_tooth_states", "odontogram_tooth_revisions",
            "odontogram_findings", "odontogram_finding_revisions",
        }
        found = await connection.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
            list(expected_tables),
        )
        assert {row["table_name"] for row in found} == expected_tables
    finally:
        if connection:
            await connection.close()
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
