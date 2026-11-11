from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.config import settings
from app.database import (
    Base,
    OdontogramFinding,
    OdontogramFindingRevision,
    OdontogramToothRevision,
    User,
    get_db,
)
from app.main import app


@pytest.fixture
async def odontogram_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client: AsyncClient, email: str, clinic: str = "Pilot Clinic") -> dict:
    response = await client.post("/api/auth/register", json={
        "clinic_name": clinic, "full_name": email.split("@")[0],
        "email": email, "password": "correct horse battery staple",
    })
    assert response.status_code == 201, response.text
    return response.json()


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def add_user(sessions, clinic_id, email: str, role: str) -> User:
    user = User(id=uuid4(), clinic_id=clinic_id, email=email, password_hash="unused",
                full_name=email.split("@")[0], role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return user


@pytest.mark.asyncio
async def test_tooth_state_and_finding_round_trip_correction_and_idempotent_retry(odontogram_client):
    client, sessions = odontogram_client
    owner = await register(client, "doctor@example.com")
    auth = headers(owner["access_token"])
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Fictiv", "last_name": "Pilot"})
    patient_id = patient.json()["id"]

    changed = await client.put(f"/api/odontogram/patients/{patient_id}/teeth/17", headers=auth, json={"status": "missing"})
    assert changed.status_code == 200 and changed.json()["previousStatus"] == "unevaluated"
    repeated = await client.put(f"/api/odontogram/patients/{patient_id}/teeth/17", headers=auth, json={"status": "missing"})
    assert repeated.status_code == 200

    key = str(uuid4())
    payload = {"toothId": "17", "kind": "caries", "description": "Suspiciune fictivă", "surfaces": ["occlusal"]}
    created = await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers={**auth, "Idempotency-Key": key}, json=payload)
    assert created.status_code == 201 and created.json()["status"] == "draft"
    retried = await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers={**auth, "Idempotency-Key": key}, json=payload)
    assert retried.status_code == 201 and retried.json()["id"] == created.json()["id"]
    assert (await client.post(f"/api/odontogram/findings/{created.json()['id']}/finalize", headers=auth)).status_code == 200
    correction = await client.post(f"/api/odontogram/findings/{created.json()['id']}/corrections", headers=auth, json={
        "description": "Constatare corectată fictiv", "surfaces": ["mesial"], "reason": "Reevaluare fictivă",
    })
    assert correction.status_code == 200
    item = correction.json()
    assert item["description"] == "Constatare corectată fictiv"
    assert len(item["revisions"]) == 1
    assert item["revisions"][0]["previousValue"]["description"] == "Suspiciune fictivă"
    assert item["revisions"][0]["actorId"]

    loaded = await client.get(f"/api/odontogram/patients/{patient_id}", headers=auth)
    assert loaded.status_code == 200 and len(loaded.json()["teeth"]) == 32
    assert next(row for row in loaded.json()["teeth"] if row["toothId"] == "17")["status"] == "missing"
    assert loaded.json()["findings"][0]["id"] == item["id"]
    assert {event["kind"] for event in loaded.json()["history"]} == {"status_changed", "finding_added", "finding_corrected"}
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(OdontogramToothRevision)) == 1
        assert await session.scalar(select(func.count()).select_from(OdontogramFindingRevision)) == 1
        assert await session.scalar(select(func.count()).select_from(OdontogramFinding)) == 1


@pytest.mark.asyncio
async def test_invalid_fdi_foreign_patient_and_draft_visibility(odontogram_client):
    client, sessions = odontogram_client
    owner = await register(client, "owner@example.com")
    owner_auth = headers(owner["access_token"])
    patient = await client.post("/api/patients", headers=owner_auth, json={"first_name": "Fictiv", "last_name": "One"})
    patient_id = patient.json()["id"]
    bad_tooth = await client.put(f"/api/odontogram/patients/{patient_id}/teeth/19", headers=owner_auth, json={"status": "missing"})
    assert bad_tooth.status_code == 422
    bad_surface = await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers=owner_auth, json={
        "toothId": "16", "kind": "caries", "description": "Fictiv", "surfaces": ["root"],
    })
    assert bad_surface.status_code == 422

    owner_user_id = jwt.decode(owner["access_token"], settings.jwt_secret, algorithms=["HS256"])["sub"]
    async with sessions() as session:
        author = await session.get(User, owner_user_id)
        author.role = "doctor"
        clinic_id = author.clinic_id
        await session.commit()
    draft = await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers=owner_auth, json={
        "toothId": "16", "kind": "observation", "description": "Ciornă fictivă",
    })
    peer = await add_user(sessions, clinic_id, "peer@example.com", "doctor")
    peer_token = create_access_token(peer)
    assert (await client.get(f"/api/odontogram/patients/{patient_id}", headers=headers(peer_token))).json()["findings"] == []
    assert (await client.patch(f"/api/odontogram/findings/{draft.json()['id']}", headers=headers(peer_token), json={"description": "Intrare nepermisă"})).status_code == 404
    manager = await add_user(sessions, clinic_id, "manager@example.com", "clinic_manager")
    manager_token = create_access_token(manager)
    assert (await client.get(f"/api/odontogram/patients/{patient_id}", headers=headers(manager_token))).json()["findings"] == []

    other = await register(client, "other@example.net", clinic="Other Clinic")
    foreign_read = await client.get(f"/api/odontogram/patients/{patient_id}", headers=headers(other["access_token"]))
    foreign_write = await client.post(f"/api/odontogram/patients/{patient_id}/findings", headers=headers(other["access_token"]), json={
        "toothId": "16", "kind": "observation", "description": "Clinic mismatch",
    })
    assert foreign_read.status_code == 404 and foreign_write.status_code == 404


@pytest.mark.asyncio
async def test_reception_role_cannot_access_odontogram(odontogram_client):
    client, sessions = odontogram_client
    manager = await register(client, "manager@example.com")
    auth = headers(manager["access_token"])
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Fictiv", "last_name": "Role"})
    manager_id = jwt.decode(manager["access_token"], settings.jwt_secret, algorithms=["HS256"])["sub"]
    async with sessions() as session:
        user = await session.get(User, manager_id)
        clinic_id = user.clinic_id
    reception = await add_user(sessions, clinic_id, "reception@example.com", "reception")
    response = await client.get(f"/api/odontogram/patients/{patient.json()['id']}", headers=headers(create_access_token(reception)))
    assert response.status_code == 403
