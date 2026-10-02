"""PostgreSQL-specific tests for procedure catalog (T03)."""
import asyncio
import os
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def postgres_context():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")

    async def override():
        pass

    app.dependency_overrides.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _postgres_dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _procedure_migration_database() -> tuple[str, str, int]:
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    assert source is not None
    name = f"dentacare_procedures_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_postgres_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    target = source.rsplit("/", 1)[0] + f"/{name}"
    env = {**os.environ, "DATABASE_URL": target, "JWT_SECRET": "procedure-test-secret", "ENVIRONMENT": "test"}
    import subprocess
    result = await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", "head"], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)
    return target, name, result.returncode


async def _drop_procedure_database(source: str, name: str) -> None:
    admin = await asyncpg.connect(_postgres_dsn(source, "postgres"))
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_procedure_catalog_upgrade_creates_table():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _procedure_migration_database()
    try:
        assert returncode == 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        assert await connection.fetchval("SELECT version_num FROM alembic_version") == "c2195d701d56"
        assert await connection.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_name = 'procedure_catalog'") == 1
        columns = await connection.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'procedure_catalog' ORDER BY ordinal_position")
        column_names = [row["column_name"] for row in columns]
        assert "id" in column_names
        assert "clinic_id" in column_names
        assert "code" in column_names
        assert "code_lower" in column_names
        assert "name" in column_names
        assert "base_price" in column_names
        assert "currency" in column_names
        assert "is_active" in column_names
        await connection.close()
    finally:
        await _drop_procedure_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_procedure_code_case_insensitive_unique():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _procedure_migration_database()
    try:
        assert returncode == 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        clinic_id = str(uuid4())
        await connection.execute("INSERT INTO clinics (id, name, created_at, updated_at) VALUES ($1, 'Test Clinic', now(), now())", clinic_id)
        await connection.execute(
            "INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'EXAM-001', 'exam-001', 'Exam 1', 30, 100.00, 'RON', true, now(), now())",
            str(uuid4()),
            clinic_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'EXAM-001', 'exam-001', 'Exam 1 Dup', 30, 100.00, 'RON', true, now(), now())",
                str(uuid4()),
                clinic_id,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                "INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'exam-001', 'exam-001', 'Exam 1 Dup2', 30, 100.00, 'RON', true, now(), now())",
                str(uuid4()),
                clinic_id,
            )
        await connection.close()
    finally:
        await _drop_procedure_database(source, name)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_postgresql_procedure_catalog_preserved_during_upgrade():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    target, name, returncode = await _procedure_migration_database()
    try:
        assert returncode == 0
        connection = await asyncpg.connect(_postgres_dsn(target, name))
        clinic_id = str(uuid4())
        await connection.execute("INSERT INTO clinics (id, name, created_at, updated_at) VALUES ($1, 'Test Clinic', now(), now())", clinic_id)
        await connection.execute(
            "INSERT INTO procedure_catalog (id, clinic_id, code, code_lower, name, description, category, default_duration_minutes, base_price, currency, is_active, created_at, updated_at) VALUES ($1, $2, 'EXAM-001', 'exam-001', 'Exam', 'A test', 'Diagnostic', 30, 150.00, 'RON', true, now(), now())",
            str(uuid4()),
            clinic_id,
        )
        count = await connection.fetchval("SELECT count(*) FROM procedure_catalog")
        assert count == 1
        await connection.close()
    finally:
        await _drop_procedure_database(source, name)
