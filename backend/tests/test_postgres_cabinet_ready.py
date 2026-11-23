"""T22 PostgreSQL concurrency and migration checks."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.cabinet_ready.models import CabinetChecklistVersion, CabinetConfirmationEvent, CabinetRequestKey
from app.database import User, get_db
from app.main import app

pytestmark = pytest.mark.postgres
PASSWORD = "correct horse battery staple"
LINES = [{"kind": "instrument", "label": "Trusă fictivă", "required": True, "prep_minutes": 5}]
TABLES = {
    "cabinet_checklist_versions", "cabinet_checklist_items", "cabinet_preparations", "cabinet_preparation_items",
    "cabinet_confirmation_events", "cabinet_manual_blocks", "cabinet_manual_block_closures", "cabinet_particularities",
    "cabinet_particularity_decisions", "cabinet_request_keys",
}


@pytest.fixture
async def pg():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    engine = create_async_engine(url, pool_size=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def scenario(client, sessions):
    registered = await client.post("/api/auth/register", json={
        "clinic_name": "Clinică T22 PG fictivă", "full_name": "Manager PG", "email": f"{uuid4().hex}@example.com", "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    patient = (await client.post("/api/patients", headers=auth, json={"first_name": "Pacient", "last_name": "PG"})).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": f"Cabinet {uuid4().hex[:6]}"})).json()
    procedure = (await client.post("/api/procedures", headers=auth, json={
        "code": f"T22-{uuid4().hex[:6]}", "name": "Procedură PG", "base_price": "10", "default_duration_minutes": 30})).json()
    appointment = (await client.post("/api/appointments", headers=auth, json={
        "patient_id": patient["id"], "doctor_id": me["id"], "room_id": room["id"], "starts_at": "2026-12-02T09:00:00+00:00",
        "duration_minutes": 30, "status": "scheduled", "appointment_type": "Control"})).json()
    linked = await client.put(f"/api/appointment-procedures/{appointment['id']}", headers=auth, json={
        "expected_revision": 0, "procedures": [{"procedure_catalog_id": procedure["id"], "quantity": 1}]})
    assert linked.status_code == 200, linked.text
    assistant = User(clinic_id=UUID(me["clinic_id"]), email=f"{uuid4().hex}@example.com", password_hash="unused",
                     full_name="Asistent PG", role="assistant", is_active=True)
    async with sessions() as session:
        session.add(assistant)
        await session.commit()
    return auth, {"Authorization": f"Bearer {create_access_token(assistant)}"}, procedure["id"], appointment["id"]


async def started_item(client, auth, procedure_id, appointment_id):
    saved = await client.put(f"/api/cabinet-ready/checklists/{procedure_id}", headers=auth, json={
        "expected_version": 0, "lines": LINES, "client_request_id": str(uuid4())})
    assert saved.status_code == 201, saved.text
    started = await client.post(f"/api/cabinet-ready/appointments/{appointment_id}/start", headers=auth, json={"client_request_id": str(uuid4())})
    assert started.status_code == 201, started.text
    board = (await client.get("/api/cabinet-ready/board", headers=auth, params={"day": "2026-12-02"})).json()
    return next(card for card in board["cards"] if card["appointment_id"] == appointment_id)["items"][0]["id"]


async def test_simultaneous_confirmation_same_request_id_records_once(pg):
    client, sessions = pg
    auth, _assistant, procedure_id, appointment_id = await scenario(client, sessions)
    item_id = await started_item(client, auth, procedure_id, appointment_id)
    request_id = str(uuid4())
    responses = await asyncio.gather(*[client.post(f"/api/cabinet-ready/items/{item_id}/confirm", headers=auth,
                                                   json={"client_request_id": request_id}) for _ in range(4)])
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetConfirmationEvent.id)).where(
            CabinetConfirmationEvent.preparation_item_id == UUID(item_id))) == 1
        assert await session.scalar(select(func.count(CabinetRequestKey.id)).where(CabinetRequestKey.client_request_id == request_id)) == 1


async def test_simultaneous_confirmation_by_two_users_records_one(pg):
    client, sessions = pg
    auth, assistant, procedure_id, appointment_id = await scenario(client, sessions)
    item_id = await started_item(client, auth, procedure_id, appointment_id)
    responses = await asyncio.gather(*[client.post(f"/api/cabinet-ready/items/{item_id}/confirm", headers=headers,
                                                   json={"client_request_id": str(uuid4())}) for headers in (auth, assistant)])
    assert sorted(response.status_code for response in responses) == [201, 409]
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetConfirmationEvent.id)).where(
            CabinetConfirmationEvent.preparation_item_id == UUID(item_id))) == 1


async def test_simultaneous_checklist_save_creates_one_version(pg):
    client, sessions = pg
    auth, _assistant, procedure_id, _appointment_id = await scenario(client, sessions)
    responses = await asyncio.gather(*[client.put(f"/api/cabinet-ready/checklists/{procedure_id}", headers=auth, json={
        "expected_version": 0, "lines": LINES, "client_request_id": str(uuid4())}) for _ in range(2)])
    assert sorted(response.status_code for response in responses) == [201, 409]
    async with sessions() as session:
        assert await session.scalar(select(func.count(CabinetChecklistVersion.id)).where(
            CabinetChecklistVersion.procedure_catalog_id == UUID(procedure_id))) == 1


async def test_simultaneous_checklist_save_same_request_id_is_one_version(pg):
    client, sessions = pg
    auth, _assistant, procedure_id, _appointment_id = await scenario(client, sessions)
    request_id = str(uuid4())
    responses = await asyncio.gather(*[client.put(f"/api/cabinet-ready/checklists/{procedure_id}", headers=auth, json={
        "expected_version": 0, "lines": LINES, "client_request_id": request_id}) for _ in range(2)])
    assert {response.status_code for response in responses} == {201}
    assert len({response.json()["id"] for response in responses}) == 1


def _dsn(url: str, database: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{database}"))


async def _alembic(url: str, *command: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t22-postgres-test", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", *command], cwd=os.path.dirname(__file__) + "/..",
                                   env=env, capture_output=True, text=True, check=False)


async def test_migration_upgrade_and_downgrade():
    source = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source or not source.startswith("postgresql+"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    name = f"dentacare_t22_{uuid4().hex[:12]}"
    admin = await asyncpg.connect(_dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"

    async def tables():
        connection = await asyncpg.connect(_dsn(source, name))
        try:
            rows = await connection.fetch("SELECT tablename FROM pg_tables WHERE tablename LIKE 'cabinet_%'")
            return {row["tablename"] for row in rows}
        finally:
            await connection.close()

    try:
        upgraded = await _alembic(url, "upgrade", "t22_cabinet_ready")
        assert upgraded.returncode == 0, upgraded.stderr
        assert await tables() == TABLES
        downgraded = await _alembic(url, "downgrade", "w2_merge_a1")
        assert downgraded.returncode == 0, downgraded.stderr
        assert await tables() == set()
    finally:
        admin = await asyncpg.connect(_dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
