"""T14 on PostgreSQL: migration on a populated database, account uniqueness and real concurrency."""

import asyncio
import os
import subprocess
from datetime import timedelta
from urllib.parse import parse_qs, urlparse, urlunparse
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db, utcnow
from app.main import app
from app.portal.models import (
    PortalAccount,
    PortalAppointmentConfirmation,
    PortalAuditEvent,
    PortalInvitation,
)
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark = pytest.mark.postgres
PASSWORD = "correct horse battery staple"


def source_url() -> str:
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    return url


def dsn(url: str, name: str) -> str:
    parsed = urlparse(url.replace("+asyncpg", ""))
    return urlunparse(parsed._replace(scheme="postgresql", path=f"/{name}"))


async def alembic(url: str, target: str, command: str = "upgrade"):
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "t14-ci", "ENVIRONMENT": "test"}
    return await asyncio.to_thread(subprocess.run, ["alembic", command, target], cwd=os.path.dirname(__file__) + "/..", env=env,
                                   capture_output=True, text=True)


@pytest.fixture
async def ctx():
    engine = create_async_engine(source_url())
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


async def clinic(client):
    registered = await client.post("/api/auth/register", json={"clinic_name": "T14 fictiv", "full_name": "Manager Fictiv",
                                                               "email": f"{uuid4().hex}@example.com", "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = (await client.post("/api/rooms", headers=auth, json={"name": f"Cabinet {uuid4().hex[:4]}"})).json()
    patient = (await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Fictivă"})).json()
    return {"auth": auth, "me": me, "room_id": room["id"], "patient_id": patient["id"]}


async def grant(client, ctx, email):
    return await client.post(f"/api/portal-staff/patients/{ctx['patient_id']}/access", headers=ctx["auth"],
                             json={"email": email, "full_name": "Ana Fictivă", "relationship": "self"})


@pytest.mark.asyncio
async def test_t14_migration_on_populated_database_and_downgrade():
    source = source_url()
    name = "t14_" + uuid4().hex[:12]
    admin = await asyncpg.connect(dsn(source, "postgres"))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = source.rsplit("/", 1)[0] + f"/{name}"
    try:
        base = await alembic(url, "w2_merge_a1")
        assert base.returncode == 0, base.stdout + base.stderr
        clinic_id, user_id, patient_id = str(uuid4()), str(uuid4()), str(uuid4())
        connection = await asyncpg.connect(dsn(url, name))
        try:
            await connection.execute("INSERT INTO clinics(id,name,timezone,created_at,updated_at) VALUES($1,'T14 fictiv','Europe/Bucharest',now(),now())", clinic_id)
            await connection.execute("INSERT INTO users(id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES($1,$2,$3,'x','Manager fictiv','clinic_manager',true,now(),now())", user_id, clinic_id, f"{user_id}@example.com")
            await connection.execute("INSERT INTO patients(id,clinic_id,first_name,last_name,created_at,updated_at) VALUES($1,$2,'Ana','Fictivă',now(),now())", patient_id, clinic_id)
        finally:
            await connection.close()
        upgraded = await alembic(url, "head")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        assert revision_is_in_head_lineage("t14_patient_portal")
        connection = await asyncpg.connect(dsn(url, name))
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == configured_alembic_head()
            assert await connection.fetchval("SELECT count(*) FROM patients") == 1
            constraints = {row[0] for row in await connection.fetch("SELECT conname FROM pg_constraint WHERE contype = 'u'")}
            assert {"uq_portal_account_email", "uq_portal_invitation_token", "uq_portal_confirmation_appointment",
                    "uq_portal_reschedule_retry", "uq_portal_form_request_retry"} <= constraints
            indexes = {row[0] for row in await connection.fetch("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'portal_%'")}
            assert {"uq_portal_reschedule_open", "uq_portal_document_active", "uq_portal_plan_active"} <= indexes
            account_id = str(uuid4())
            await connection.execute("INSERT INTO portal_accounts(id,clinic_id,email,full_name,status,created_by_id,created_at) VALUES($1,$2,'a@example.com','Ana','invited',$3,now())", account_id, clinic_id, user_id)
            with pytest.raises(asyncpg.UniqueViolationError):
                await connection.execute("INSERT INTO portal_accounts(id,clinic_id,email,full_name,status,created_by_id,created_at) VALUES($1,$2,'a@example.com','Ana 2','invited',$3,now())", str(uuid4()), clinic_id, user_id)
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute("UPDATE portal_accounts SET status='active' WHERE id=$1", account_id)
            await connection.execute("DELETE FROM portal_accounts")
        finally:
            await connection.close()
        if configured_alembic_head() == "t14_patient_portal":
            downgraded = await alembic(url, "w2_merge_a1", "downgrade")
            assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
            connection = await asyncpg.connect(dsn(url, name))
            try:
                assert await connection.fetchval("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'portal_%'") == 0
                assert await connection.fetchval("SELECT count(*) FROM patients") == 1
            finally:
                await connection.close()
    finally:
        admin = await asyncpg.connect(dsn(source, "postgres"))
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.asyncio
async def test_simultaneous_invitations_for_one_email_create_one_account(ctx):
    client, sessions = ctx
    world = await clinic(client)
    email = f"{uuid4().hex[:8]}@example.com"
    results = await asyncio.wait_for(asyncio.gather(*(grant(client, world, email) for _ in range(6))), 30)
    assert {response.status_code for response in results} <= {201, 409}
    assert any(response.status_code == 201 for response in results)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PortalAccount).where(PortalAccount.email == email)) == 1
        account = await session.scalar(select(PortalAccount).where(PortalAccount.email == email))
        pending = await session.scalar(select(func.count()).select_from(PortalInvitation).where(
            PortalInvitation.account_id == account.id, PortalInvitation.used_at.is_(None), PortalInvitation.revoked_at.is_(None)))
        assert pending == 1


@pytest.mark.asyncio
async def test_account_email_is_unique_per_clinic(ctx):
    client, sessions = ctx
    world = await clinic(client)
    async with sessions() as session:
        for _ in range(2):
            session.add(PortalAccount(clinic_id=UUID(world["me"]["clinic_id"]), email="dublura@example.com", full_name="Ana",
                                      status="invited", created_by_id=UUID(world["me"]["id"])))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_simultaneous_consumption_of_one_invitation_succeeds_once(ctx):
    client, sessions = ctx
    world = await clinic(client)
    granted = await grant(client, world, f"{uuid4().hex[:8]}@example.com")
    token = parse_qs(urlparse(granted.json()["invitation_url"]).query)["token"][0]
    calls = [client.post("/api/portal/auth/accept", json={"token": token, "password": f"parola-portal-{index:02d}"}) for index in range(6)]
    results = await asyncio.wait_for(asyncio.gather(*calls), 30)
    assert sorted(response.status_code for response in results) == [200] + [404] * 5
    async with sessions() as session:
        accepted = await session.scalar(select(func.count()).select_from(PortalAuditEvent).where(PortalAuditEvent.action == "invitation_accepted"))
        assert accepted == 1


@pytest.mark.asyncio
async def test_simultaneous_confirmations_store_one_record(ctx):
    client, sessions = ctx
    world = await clinic(client)
    email = f"{uuid4().hex[:8]}@example.com"
    granted = await grant(client, world, email)
    token = parse_qs(urlparse(granted.json()["invitation_url"]).query)["token"][0]
    assert (await client.post("/api/portal/auth/accept", json={"token": token, "password": "parola-portal-fictiva"})).status_code == 200
    logged = await client.post("/api/portal/auth/login", json={"clinic_id": world["me"]["clinic_id"], "email": email, "password": "parola-portal-fictiva"})
    auth = {"Authorization": f"Bearer {logged.json()['access_token']}"}
    starts = (utcnow() + timedelta(days=4)).replace(hour=9, minute=0, second=0, microsecond=0)
    visit = await client.post("/api/appointments", headers=world["auth"], json={
        "patient_id": world["patient_id"], "doctor_id": world["me"]["id"], "room_id": world["room_id"], "starts_at": starts.isoformat(),
        "duration_minutes": 30, "appointment_type": "Control"})
    url = f"/api/portal/patients/{world['patient_id']}/appointments/{visit.json()['id']}/confirm"
    results = await asyncio.wait_for(asyncio.gather(*(client.post(url, headers=auth) for _ in range(8))), 30)
    assert {response.status_code for response in results} == {200}
    assert len({response.json()["confirmed_at"] for response in results}) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PortalAppointmentConfirmation).where(
            PortalAppointmentConfirmation.appointment_id == UUID(visit.json()["id"]))) == 1


@pytest.mark.asyncio
async def test_simultaneous_reschedule_requests_leave_one_open(ctx):
    client, sessions = ctx
    world = await clinic(client)
    email = f"{uuid4().hex[:8]}@example.com"
    granted = await grant(client, world, email)
    token = parse_qs(urlparse(granted.json()["invitation_url"]).query)["token"][0]
    await client.post("/api/portal/auth/accept", json={"token": token, "password": "parola-portal-fictiva"})
    logged = await client.post("/api/portal/auth/login", json={"clinic_id": world["me"]["clinic_id"], "email": email, "password": "parola-portal-fictiva"})
    auth = {"Authorization": f"Bearer {logged.json()['access_token']}"}
    starts = (utcnow() + timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0)
    visit = await client.post("/api/appointments", headers=world["auth"], json={
        "patient_id": world["patient_id"], "doctor_id": world["me"]["id"], "room_id": world["room_id"], "starts_at": starts.isoformat(),
        "duration_minutes": 30, "appointment_type": "Control"})
    url = f"/api/portal/patients/{world['patient_id']}/appointments/{visit.json()['id']}/reschedule-requests"
    results = await asyncio.wait_for(asyncio.gather(*(client.post(url, headers=auth, json={"message": f"Mesaj {i}", "client_request_id": str(uuid4())})
                                                      for i in range(6))), 30)
    assert sorted(response.status_code for response in results) == [201] + [409] * 5
    from app.portal.models import PortalRescheduleRequest
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PortalRescheduleRequest).where(
            PortalRescheduleRequest.appointment_id == UUID(visit.json()["id"]), PortalRescheduleRequest.status == "open")) == 1
