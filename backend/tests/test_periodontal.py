from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import (
    Base,
    OdontogramToothState,
    PerformedProcedure,
    PeriodontalAssessment,
    PeriodontalAssessmentTooth,
    PeriodontalSiteMeasurement,
    ProcedureExecutionSegment,
    User,
    get_db,
)
from app.main import app

SITES = ["buccal_mesial", "buccal_mid", "buccal_distal", "oral_mesial", "oral_mid", "oral_distal"]

@pytest.fixture
async def paro_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()

async def register(client, email, clinic="Paro fictional"):
    response = await client.post("/api/auth/register", json={"clinic_name": clinic, "full_name": email.split("@")[0], "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

async def add_user(sessions, clinic_id, email, role):
    user = User(id=uuid4(), clinic_id=clinic_id, email=email, password_hash="unused", full_name=email, role=role, is_active=True)
    async with sessions() as db:
        db.add(user)
        await db.commit()
    return user

def tooth(fdi="16", first_depth=0):
    return {"tooth_fdi": fdi, "is_excluded": False, "mobility": "0", "furcation": None,
        "sites": [{"site": site, "probing_depth_mm": first_depth if index == 0 else None,
            "recession_mm": 0 if index == 0 else None, "bop": False if index == 0 else None,
            "plaque": None, "suppuration": None} for index, site in enumerate(SITES)]}

async def create_patient(client, auth, first="Fictiv"):
    response = await client.post("/api/patients", headers=auth, json={"first_name": first, "last_name": "Periodontal"})
    assert response.status_code == 201, response.text
    return response.json()["id"]

@pytest.mark.asyncio
async def test_periodontal_validation_draft_privacy_finalize_history_and_partial_comparison(paro_client):
    client, sessions = paro_client
    auth = await register(client, "paro-doctor@example.com")
    me = (await client.get("/api/auth/me", headers=auth)).json()
    patient = await create_patient(client, auth)
    created = await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-11-19"})
    assert created.status_code == 201, created.text
    assessment_id = created.json()["id"]

    excluded = {**tooth(), "is_excluded": True, "mobility": None, "furcation": None}
    excluded["sites"] = [{"site": name, "probing_depth_mm": None, "recession_mm": None, "bop": None, "plaque": None, "suppuration": None} for name in SITES]
    exclusion_saved = await client.put(f"/api/periodontal/assessments/{assessment_id}/measurements", headers=auth, json={"teeth": [excluded]})
    assert exclusion_saved.status_code == 200 and exclusion_saved.json()["teeth"][0]["is_excluded"] is True
    assert (await client.post(f"/api/periodontal/assessments/{assessment_id}/finalize", headers=auth)).status_code == 422
    invalids = [
        {"teeth": [{**tooth("19")}]},
        {"teeth": [tooth(), tooth()]},
        {"teeth": [{**tooth(), "sites": tooth()["sites"][:-1]}]},
        {"teeth": [{**tooth(), "sites": [*tooth()["sites"][:-1], tooth()["sites"][0]]}]},
        {"teeth": [{**tooth(), "mobility": "4"}]},
        {"teeth": [{**tooth(), "sites": [{**tooth()["sites"][0], "probing_depth_mm": True}, *tooth()["sites"][1:]]}]},
        {"teeth": [{**tooth(), "sites": [{**tooth()["sites"][0], "probing_depth_mm": "1.25"}, *tooth()["sites"][1:]]}]},
        {"teeth": [{**tooth(), "sites": [{**tooth()["sites"][0], "bop": 0}, *tooth()["sites"][1:]]}]},
    ]
    for payload in invalids:
        response = await client.put(f"/api/periodontal/assessments/{assessment_id}/measurements", headers=auth, json=payload)
        assert response.status_code == 422, response.text
    still_saved = await client.get(f"/api/periodontal/assessments/{assessment_id}", headers=auth)
    assert still_saved.json()["teeth"] == exclusion_saved.json()["teeth"]
    assert (await client.post(f"/api/periodontal/assessments/{assessment_id}/finalize", headers=auth)).status_code == 422
    assert (await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-11-19", "previous_assessment_id": assessment_id, "relationship_kind": "correction"})).status_code == 422

    peer = await add_user(sessions, me["clinic_id"], "paro-peer@example.com", "clinic_manager")
    peer_auth = {"Authorization": f"Bearer {create_access_token(peer)}"}
    assert (await client.get(f"/api/periodontal/assessments/{assessment_id}", headers=peer_auth)).status_code == 404
    assert (await client.get(f"/api/periodontal/patients/{patient}/assessments", headers=peer_auth)).json() == []
    assert (await client.get(f"/api/periodontal/patients/{patient}/compare", headers=peer_auth, params={"first_id": assessment_id, "second_id": assessment_id})).status_code == 404
    assert (await client.put(f"/api/periodontal/assessments/{assessment_id}/measurements", headers=peer_auth, json={"teeth": [tooth()]})).status_code == 404
    assert (await client.post(f"/api/periodontal/assessments/{assessment_id}/finalize", headers=peer_auth)).status_code == 404

    baseline_teeth = [tooth(), tooth("17", 2.5)]
    baseline_teeth[0]["sites"][0]["plaque"] = False
    saved = await client.put(f"/api/periodontal/assessments/{assessment_id}/measurements", headers=auth, json={"teeth": baseline_teeth})
    assert saved.status_code == 200, saved.text
    assert saved.json()["teeth"][0]["sites"][0]["probing_depth_mm"] == 0
    assert saved.json()["teeth"][0]["sites"][0]["bop"] is False
    assert saved.json()["teeth"][0]["sites"][1]["bop"] is None
    assert (await client.get(f"/api/periodontal/assessments/{assessment_id}", headers=auth)).json()["teeth"] == saved.json()["teeth"]
    done = await client.post(f"/api/periodontal/assessments/{assessment_id}/finalize", headers=auth)
    assert done.status_code == 200 and done.json()["status"] == "finalized"
    assert (await client.put(f"/api/periodontal/assessments/{assessment_id}/measurements", headers=auth, json={"teeth": [tooth()]})).status_code == 409

    second = await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-12-19", "previous_assessment_id": assessment_id, "relationship_kind": "follow_up"})
    assert second.status_code == 201, second.text
    followup_teeth = [tooth(first_depth=1), tooth("17", 2.5)]
    followup_teeth[0]["sites"][0]["bop"] = True
    followup_teeth[0]["sites"][0]["plaque"] = True
    missing_bop_tooth = tooth("18")
    missing_bop_tooth["sites"][0]["recession_mm"] = 0
    for site_row in missing_bop_tooth["sites"]:
        site_row["bop"] = None
    followup_teeth.append(missing_bop_tooth)
    saved2 = await client.put(f"/api/periodontal/assessments/{second.json()['id']}/measurements", headers=auth, json={"teeth": followup_teeth})
    assert saved2.status_code == 200, saved2.text
    assert (await client.post(f"/api/periodontal/assessments/{second.json()['id']}/finalize", headers=auth)).status_code == 200
    comparison = await client.get(f"/api/periodontal/patients/{patient}/compare", headers=auth, params={"first_id": assessment_id, "second_id": second.json()["id"]})
    assert comparison.status_code == 200, comparison.text
    tooth16 = next(row for row in comparison.json()["teeth"] if row["tooth_fdi"] == "16")
    site = tooth16["sites"][0]
    assert site["differences"]["probing_depth_mm"] == 1
    assert site["differences"]["recession_mm"] == 0
    assert site["differences"]["bop"] is True
    assert site["differences"]["plaque"] is True
    assert tooth16["second_bop_positive"] == 1
    assert tooth16["second_bop_known_denominator"] == 1
    assert tooth16["second_bop_percent"] == 100
    assert tooth16["sites"][1]["first"]["bop"] is None
    assert tooth16["sites"][1]["differences"]["bop"] is None
    tooth18 = next(row for row in comparison.json()["teeth"] if row["tooth_fdi"] == "18")
    assert tooth18["second_bop_positive"] == 0
    assert tooth18["second_bop_known_denominator"] == 0
    assert tooth18["second_bop_percent"] is None
    assert len((await client.get(f"/api/periodontal/patients/{patient}/assessments", headers=auth)).json()) == 2

    # Corrections preserve the finalized source and record a required reason.
    no_reason = await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={
        "clinical_date": "2026-12-20", "previous_assessment_id": assessment_id, "relationship_kind": "correction",
    })
    assert no_reason.status_code == 422
    correction = await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={
        "clinical_date": "2026-12-20", "previous_assessment_id": assessment_id,
        "relationship_kind": "correction", "change_reason": "Corectare de măsurare fictivă",
    })
    assert correction.status_code == 201 and correction.json()["change_reason"] == "Corectare de măsurare fictivă"
    correction_save = await client.put(f"/api/periodontal/assessments/{correction.json()['id']}/measurements", headers=auth, json={"teeth": [tooth(first_depth=0)]})
    assert correction_save.status_code == 200
    assert (await client.post(f"/api/periodontal/assessments/{correction.json()['id']}/finalize", headers=auth)).status_code == 200
    corrected_source = await client.get(f"/api/periodontal/assessments/{assessment_id}", headers=auth)
    assert corrected_source.json()["status"] == "finalized"
    assert corrected_source.json()["teeth"] == saved.json()["teeth"]
    history = await client.get(f"/api/periodontal/patients/{patient}/assessments", headers=auth)
    assert len(history.json()) == 3
    assert next(item for item in history.json() if item["id"] == correction.json()["id"])["relationship_kind"] == "correction"
    another_patient = await create_patient(client, auth, first="Alt pacient")
    wrong_patient_comparison = await client.get(
        f"/api/periodontal/patients/{another_patient}/compare", headers=auth,
        params={"first_id": assessment_id, "second_id": second.json()["id"]},
    )
    assert wrong_patient_comparison.status_code == 404
    wrong_patient_link = await client.post(
        f"/api/periodontal/patients/{another_patient}/assessments", headers=auth, json={
            "clinical_date": "2026-12-21", "previous_assessment_id": assessment_id,
            "relationship_kind": "follow_up",
        },
    )
    assert wrong_patient_link.status_code == 422
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OdontogramToothState)) == 0
        assert await db.scalar(select(func.count()).select_from(PeriodontalAssessment)) == 3
        assert await db.scalar(select(func.count()).select_from(PeriodontalAssessmentTooth)) == 6
        assert await db.scalar(select(func.count()).select_from(PeriodontalSiteMeasurement)) == 36
        assert await db.scalar(select(func.count()).select_from(PerformedProcedure)) == 0
        assert await db.scalar(select(func.count()).select_from(ProcedureExecutionSegment)) == 0

@pytest.mark.asyncio
async def test_periodontal_role_tenant_and_patient_isolation(paro_client):
    client, sessions = paro_client
    auth = await register(client, "paro-owner@example.com")
    owner_data = (await client.get("/api/auth/me", headers=auth)).json()
    patient = await create_patient(client, auth)
    draft = await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=auth, json={"clinical_date": "2026-11-19"})
    outsider = await register(client, "paro-outside@example.com", clinic="Different fictional clinic")
    assert (await client.get(f"/api/periodontal/patients/{patient}/assessments", headers=outsider)).status_code == 404
    assert (await client.get(f"/api/periodontal/assessments/{draft.json()['id']}", headers=outsider)).status_code == 404
    assistant = await register(client, "paro-assistant@example.com")
    assistant_data = (await client.get("/api/auth/me", headers=assistant)).json()
    async with sessions() as db:
        assistant_row = await db.get(User, assistant_data["id"])
        owner_row = await db.get(User, owner_data["id"])
        assistant_row.role = "assistant"
        owner_row.role = "doctor"
        await db.commit()
    assert draft.status_code == 201
    assert (await client.post(f"/api/periodontal/patients/{patient}/assessments", headers=assistant, json={"clinical_date": "2026-11-20"})).status_code == 403
    assert (await client.get(f"/api/periodontal/patients/{patient}/assessments", headers=assistant)).status_code == 403
