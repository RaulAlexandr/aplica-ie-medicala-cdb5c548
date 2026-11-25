"""PostgreSQL proof for T31: migration t31_imaging widens the document constraints and keeps old rows."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from tests.test_imaging import dicom, stl_binary

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, *args: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t31-test-only", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", *args], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)


@pytest.fixture
async def postgres_ctx(tmp_path, monkeypatch):
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t31_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    monkeypatch.setattr(settings, "document_storage_dir", str(tmp_path / "store"))
    try:
        migrated = await _alembic(url, "upgrade", "t31_imaging")
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def override():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, url, name
    finally:
        app.dependency_overrides.clear()
        if "engine" in locals():
            await engine.dispose()
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_postgres_stl_and_dicom_are_stored_with_metadata_and_downgrade_is_guarded(postgres_ctx):
    client, url, name = postgres_ctx
    registered = await client.post("/api/auth/register", json={"clinic_name": "T31 fictiv", "full_name": "Manager fictiv", "email": f"t31-{uuid4().hex}@example.com", "password": "correct horse battery staple"})
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Pacient", "last_name": "Fictiv"})).json()
    for content, file_name in ((stl_binary(), "cub.stl"), (dicom(frames=2), "serie.dcm")):
        response = await client.post(f"/api/documents/patients/{patient['id']}", params={"category": "imaging"},
                                     headers={**headers, "Content-Type": "application/octet-stream", "X-File-Name": file_name}, content=content)
        assert response.status_code == 201, response.text
        assert response.json()["imaging"]["kind"] in {"stl", "dicom"}
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT count(*) FROM clinical_documents WHERE imaging_metadata IS NOT NULL") == 2
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("UPDATE clinical_documents SET category = 'bogus' WHERE category = 'imaging'")
    finally:
        await conn.close()
    downgraded = await _alembic(url, "downgrade", "w2_merge_a1")
    assert downgraded.returncode != 0 and "imaging" in (downgraded.stdout + downgraded.stderr)
