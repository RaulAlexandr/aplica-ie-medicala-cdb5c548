"""PostgreSQL uniqueness and simultaneous retry coverage for T32 imports."""

import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import ImportRun, Patient, get_db
from app.main import app

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _run_alembic(url: str, action: str, revision: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t32-import-pg-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(
        subprocess.run,
        ["alembic", action, revision],
        cwd=os.path.dirname(__file__) + "/..",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


async def test_same_patient_file_confirmed_concurrently_creates_once():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t32_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        migrated = await _run_alembic(url, "upgrade", "head")
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        downgraded = await _run_alembic(url, "downgrade", "w2_merge_a1")
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        upgraded = await _run_alembic(url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        engine = create_async_engine(url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def override():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                registered = await client.post(
                    "/api/auth/register",
                    json={
                        "clinic_name": "Clinica T32",
                        "full_name": "Manager T32",
                        "email": "t32-pg@example.com",
                        "password": "correct horse battery staple",
                    },
                )
                assert registered.status_code == 201, registered.text
                headers = {
                    "Authorization": f"Bearer {registered.json()['access_token']}",
                    "X-Filename": "patients.csv",
                }
                content = b"Nume,Prenume,CNP\nPop,Ana,2800101123456\n"
                params = {"columns": '{"last_name":"Nume","first_name":"Prenume","cnp":"CNP"}'}
                responses = await asyncio.gather(
                    *[
                        client.post(
                            "/api/import-export/patients/confirm",
                            params=params,
                            headers=headers,
                            content=content,
                        )
                        for _ in range(2)
                    ]
                )
                assert all(response.status_code == 200 for response in responses), [
                    response.text for response in responses
                ]
                assert responses[0].json()["id"] == responses[1].json()["id"]
                async with sessions() as session:
                    assert await session.scalar(select(func.count(Patient.id))) == 1
                    assert await session.scalar(select(func.count(ImportRun.id))) == 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
