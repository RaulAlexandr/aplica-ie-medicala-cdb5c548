"""PostgreSQL migration preservation and concurrent publication proof for T12."""

import asyncio
import json
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, target: str):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t12-test-only", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", "upgrade", target], cwd=os.path.dirname(__file__) + "/..", env=env, capture_output=True, text=True, check=False)


@pytest.fixture
async def temp_database():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t12_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield source.rsplit("/", 1)[0] + f"/{name}", name
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_t12_migration_preserves_t03_populated_rows_and_uses_configured_head(temp_database):
    url, name = temp_database
    base = await _alembic(url, "t03_catalog_allowed_locations")
    assert base.returncode == 0, base.stdout + base.stderr
    clinic, user, patient = (str(uuid4()) for _ in range(3))
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        await conn.execute("INSERT INTO clinics (id,name,timezone,created_at,updated_at) VALUES ($1,'T12 fictive','Europe/Bucharest',now(),now())", clinic)
        await conn.execute("INSERT INTO users (id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES ($1,$2,$3,'x','Manager fictiv','clinic_manager',true,now(),now())", user, clinic, f"{user}@example.test")
        await conn.execute("INSERT INTO patients (id,clinic_id,first_name,last_name,created_at,updated_at) VALUES ($1,$2,'Pacient','Fictiv',now(),now())", patient, clinic)
    finally:
        await conn.close()
    upgraded = await _alembic(url, "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    assert revision_is_in_head_lineage("t03_catalog_allowed_locations")
    assert revision_is_in_head_lineage("t12_intake_forms")
    conn = await asyncpg.connect(_dsn(url, name))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
        assert await conn.fetchval("SELECT count(*) FROM clinics WHERE id=$1", clinic) == 1
        assert await conn.fetchval("SELECT count(*) FROM users WHERE id=$1", user) == 1
        assert await conn.fetchval("SELECT count(*) FROM patients WHERE id=$1", patient) == 1
        assert await conn.fetchval("SELECT count(*) FROM patient_forms") == 0

        template_id, version_id = str(uuid4()), str(uuid4())
        await conn.execute(
            "INSERT INTO form_templates (id,clinic_id,name,created_by_id,created_at) VALUES ($1,$2,'Template unicitate',$3,now())",
            template_id, clinic, user,
        )
        await conn.execute(
            "INSERT INTO form_template_versions (id,clinic_id,template_id,version_number,status,definition,created_by_id,created_at) VALUES ($1,$2,$3,1,'draft','{\"sections\":[]}'::jsonb,$4,now())",
            version_id, clinic, template_id, user,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO form_template_versions (id,clinic_id,template_id,version_number,status,definition,created_by_id,created_at) VALUES ($1,$2,$3,1,'draft','{\"sections\":[]}'::jsonb,$4,now())",
                str(uuid4()), clinic, template_id, user,
            )
        question_id, draft_version_id = str(uuid4()), str(uuid4())
        await conn.execute(
            "INSERT INTO form_template_questions (id,clinic_id,template_version_id,question_id,section_id,position,question_type,definition) VALUES ($1,$2,$3,'q','s',0,'short_text','{\"id\":\"q\",\"type\":\"short_text\"}'::jsonb)",
            question_id, clinic, version_id,
        )
        await conn.execute("UPDATE form_template_versions SET status='published' WHERE id=$1", version_id)
        await conn.execute(
            "INSERT INTO form_template_versions (id,clinic_id,template_id,version_number,status,definition,created_by_id,created_at) VALUES ($1,$2,$3,2,'draft','{\"sections\":[]}'::jsonb,$4,now())",
            draft_version_id, clinic, template_id, user,
        )
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "UPDATE form_template_questions SET template_version_id=$1, definition='{\"id\":\"q\",\"type\":\"short_text\",\"label\":\"edited\"}'::jsonb WHERE id=$2",
                draft_version_id, question_id,
            )
        preserved_question = await conn.fetchrow(
            "SELECT template_version_id, definition FROM form_template_questions WHERE id=$1", question_id,
        )
        assert str(preserved_question["template_version_id"]) == version_id
        assert json.loads(preserved_question["definition"]) == {"id": "q", "type": "short_text"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_two_concurrent_publications_of_same_version_have_one_winner():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_async_engine(source)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    email = f"t12-{uuid4().hex}@example.com"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            registered = await client.post("/api/auth/register", json={"clinic_name": "T12 concurență fictivă", "full_name": "Manager fictiv", "email": email, "password": "correct horse battery staple"})
            assert registered.status_code == 201
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            template = await client.post("/api/forms/templates", headers=headers, json={"name": "Publicare fictivă"})
            version_id = template.json()["version_id"]
            body = {"definition": {"sections": [{"id": "s", "title": "Secțiune fictivă", "questions": [{"id": "q", "label": "Întrebare fictivă", "type": "short_text", "required": True}]}]}}
            assert (await client.put(f"/api/forms/versions/{version_id}", headers=headers, json=body)).status_code == 200
            ready = asyncio.Event()
            arrivals_lock = asyncio.Lock()
            arrivals = 0
            session_ids = set()
            backend_pids = set()

            async def concurrent_override():
                nonlocal arrivals
                async with sessions() as session:
                    session_ids.add(id(session))
                    backend_pids.add((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                    async with arrivals_lock:
                        arrivals += 1
                        if arrivals == 2:
                            ready.set()
                    await asyncio.wait_for(ready.wait(), timeout=10)
                    yield session

            app.dependency_overrides[get_db] = concurrent_override
            async def publish():
                return await asyncio.wait_for(client.post(f"/api/forms/versions/{version_id}/publish", headers=headers), timeout=10)
            results = await asyncio.wait_for(asyncio.gather(publish(), publish()), timeout=15)
            assert sorted(response.status_code for response in results) == [200, 409]
            assert len(session_ids) == 2
            assert len(backend_pids) == 2
            async with sessions() as session:
                persisted = (await session.execute(text(
                    "SELECT id, status FROM form_template_versions WHERE id = :version_id"
                ), {"version_id": version_id})).one()
                assert str(persisted.id) == version_id
                assert persisted.status == "published"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
