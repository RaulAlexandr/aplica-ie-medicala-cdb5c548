"""Run T09 migration from T06 head while preserving pre-existing rows."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest

from tests.alembic_helpers import configured_alembic_head


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))

async def _alembic(url: str, target: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t09-migration-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)

@pytest.mark.asyncio
@pytest.mark.postgres
async def test_t09_migration_from_t06_preserves_existing_rows():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t09_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    connection = None
    try:
        base = await _alembic(url, "t06_add_persisted_odontogram")
        assert base.returncode == 0, base.stdout + base.stderr
        connection = await asyncpg.connect(_dsn(url, name))
        clinic, doctor, patient, note, tooth = [str(uuid4()) for _ in range(5)]
        await connection.execute("INSERT INTO clinics (id,name,created_at,updated_at) VALUES ($1,'Fictional',now(),now())", clinic)
        await connection.execute("INSERT INTO users (id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES ($1,$2,'fictional@example.com','x','Fictional doctor','doctor',true,now(),now())", doctor, clinic)
        await connection.execute("INSERT INTO patients (id,clinic_id,first_name,last_name,created_at,updated_at) VALUES ($1,$2,'Fictiv','Test',now(),now())", patient, clinic)
        await connection.execute("INSERT INTO clinical_notes (id,clinic_id,patient_id,author_id,note_type,content,status,is_alert,created_at,updated_at) VALUES ($1,$2,$3,$4,'finding','Existing fictional note','draft',false,now(),now())", note, clinic, patient, doctor)
        await connection.execute("INSERT INTO odontogram_tooth_states (id,clinic_id,patient_id,tooth_fdi,status,updated_by_id,created_at,updated_at) VALUES ($1,$2,$3,'16','present',$4,now(),now())", tooth, clinic, patient, doctor)
        await connection.close(); connection = None
        result = await _alembic(url, "head")
        assert result.returncode == 0, result.stdout + result.stderr
        connection = await asyncpg.connect(_dsn(url, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        assert await connection.fetchval("SELECT content FROM clinical_notes WHERE id=$1", note) == "Existing fictional note"
        assert await connection.fetchval("SELECT status FROM odontogram_tooth_states WHERE id=$1", tooth) == "present"
        for table in ("periodontal_assessments", "periodontal_assessment_teeth", "periodontal_site_measurements"):
            assert await connection.fetchval("SELECT to_regclass($1)", f"public.{table}") == table
    finally:
        if connection:
            await connection.close()
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
