"""T14 patient portal on SQLite. Rule -> test matrix is in reports/cpm/v4/t14/HANDOFF.md.

PostgreSQL uniqueness and concurrency live in test_postgres_patient_portal.py.
"""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.config import settings
from app.database import Appointment, Base, PatientForm, User, get_db, utcnow
from app.main import app
from app.portal.models import (
    PortalAppointmentConfirmation,
    PortalAuditEvent,
    PortalInvitation,
    PortalLoginAttempt,
    PortalRescheduleRequest,
)
from tests.test_documents import png, upload

PASSWORD = "correct horse battery staple"
PORTAL_PASSWORD = "parola-portal-fictiva"
GENERIC = "Email sau parolă incorectă."


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_storage_dir", str(tmp_path / "t14-store"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
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


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def clinic(client, email=None, name="Clinica Fictivă T14"):
    email = email or f"manager-{uuid4().hex[:8]}@example.com"
    response = await client.post("/api/auth/register", json={"clinic_name": name, "full_name": "Manager Fictiv", "email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = h(response.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": "Cabinet Intern Șapte"})
    return {"auth": auth, "me": me, "clinic_id": me["clinic_id"], "room_id": room.json()["id"]}


async def staff(sessions, ctx, role):
    user = User(id=uuid4(), clinic_id=UUID(ctx["clinic_id"]), email=f"{role}-{uuid4().hex[:6]}@example.com", password_hash="unused",
                full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return h(create_access_token(user))


async def add_patient(client, ctx, first="Ana"):
    response = await client.post("/api/patients", headers=ctx["auth"], json={"first_name": first, "last_name": "Fictivă"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def add_appointment(client, ctx, patient_id, days=5, notes="Notă internă secretă", kind="Tip intern confidențial"):
    starts = (utcnow() + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    response = await client.post("/api/appointments", headers=ctx["auth"], json={
        "patient_id": patient_id, "doctor_id": ctx["me"]["id"], "room_id": ctx["room_id"], "starts_at": starts.isoformat(),
        "duration_minutes": 30, "appointment_type": kind, "notes": notes})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def set_appointment(sessions, appointment_id, **values):
    async with sessions() as session:
        row = await session.get(Appointment, UUID(appointment_id))
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()


async def grant(client, auth, patient_id, email="ana.portal@example.com", relationship="self", name="Ana Fictivă"):
    return await client.post(f"/api/portal-staff/patients/{patient_id}/access", headers=auth,
                             json={"email": email, "full_name": name, "relationship": relationship})


def token_of(url):
    return parse_qs(urlparse(url).query)["token"][0]


async def accept(client, url, password=PORTAL_PASSWORD):
    return await client.post("/api/portal/auth/accept", json={"token": token_of(url), "password": password})


async def login(client, clinic_id, email="ana.portal@example.com", password=PORTAL_PASSWORD):
    return await client.post("/api/portal/auth/login", json={"clinic_id": clinic_id, "email": email, "password": password})


async def portal(client, ctx, patient_id, email="ana.portal@example.com", relationship="self"):
    granted = await grant(client, ctx["auth"], patient_id, email, relationship)
    assert granted.status_code == 201, granted.text
    if granted.json()["invitation_url"]:
        assert (await accept(client, granted.json()["invitation_url"])).status_code == 200
    logged = await login(client, ctx["clinic_id"], email)
    assert logged.status_code == 200, logged.text
    return {"auth": h(logged.json()["access_token"]), "refresh": logged.json()["refresh_token"], **granted.json()}


def uid():
    return str(uuid4())


# 1. Separate accounts and invitations ---------------------------------------------------------

async def test_invitation_is_hashed_single_use_and_sets_password(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    granted = await grant(client, ctx["auth"], patient_id)
    assert granted.status_code == 201 and granted.json()["invitation_url"].startswith("/portal/invitatie?token=")
    raw = token_of(granted.json()["invitation_url"])
    async with sessions() as session:
        stored = (await session.scalars(select(PortalInvitation))).all()
        assert len(stored) == 1 and stored[0].token_hash != raw and len(stored[0].token_hash) == 64
        assert raw not in stored[0].token_hash
        expires = stored[0].expires_at.replace(tzinfo=None) - stored[0].created_at.replace(tzinfo=None)
        assert timedelta(hours=71, minutes=59) < expires <= timedelta(hours=72)
        assert await session.scalar(select(func.count()).select_from(User).where(User.email == "ana.portal@example.com")) == 0
    info = await client.post("/api/portal/auth/invitation", json={"token": raw})
    assert info.status_code == 200 and info.json()["clinic_name"] == "Clinica Fictivă T14"
    assert (await login(client, ctx["clinic_id"])).status_code == 401
    accepted = await accept(client, granted.json()["invitation_url"])
    assert accepted.status_code == 200 and accepted.json()["clinic_id"] == ctx["clinic_id"]
    assert (await accept(client, granted.json()["invitation_url"], "alta-parola-lunga-1")).status_code == 404
    assert (await login(client, ctx["clinic_id"], password="alta-parola-lunga-1")).status_code == 401
    assert (await login(client, ctx["clinic_id"])).status_code == 200


async def test_password_needs_twelve_characters(env):
    client, _sessions = env
    ctx = await clinic(client)
    granted = await grant(client, ctx["auth"], await add_patient(client, ctx))
    assert (await accept(client, granted.json()["invitation_url"], "scurta-11ch")).status_code == 422
    assert (await accept(client, granted.json()["invitation_url"], "exact-12-chr")).status_code == 200


async def test_expired_reissued_and_revoked_invitations_are_refused(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    first = (await grant(client, ctx["auth"], patient_id)).json()
    async with sessions() as session:
        await session.execute(update(PortalInvitation).values(expires_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    assert (await accept(client, first["invitation_url"])).status_code == 404
    assert (await client.post("/api/portal/auth/invitation", json={"token": token_of(first["invitation_url"])})).status_code == 404
    second = await client.post(f"/api/portal-staff/accounts/{first['account_id']}/invitation", headers=ctx["auth"])
    third = await client.post(f"/api/portal-staff/accounts/{first['account_id']}/invitation", headers=ctx["auth"])
    assert second.status_code == third.status_code == 201
    assert (await accept(client, second.json()["invitation_url"])).status_code == 404  # replaced by the third
    revoked = await client.post(f"/api/portal-staff/accounts/{first['account_id']}/revoke", headers=ctx["auth"], json={"reason": "Cerere pacient"})
    assert revoked.status_code == 200
    assert (await accept(client, third.json()["invitation_url"])).status_code == 404


async def test_revocation_ends_existing_sessions_immediately(env):
    client, _sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    session = await portal(client, ctx, patient_id)
    assert (await client.get("/api/portal/me", headers=session["auth"])).status_code == 200
    revoked = await client.post(f"/api/portal-staff/accounts/{session['account_id']}/revoke", headers=ctx["auth"], json={"reason": "Acces retras"})
    assert revoked.status_code == 200
    assert (await client.get("/api/portal/me", headers=session["auth"])).status_code == 401
    assert (await client.post("/api/portal/auth/refresh", json={"refresh_token": session["refresh"]})).status_code == 401
    assert (await login(client, ctx["clinic_id"])).status_code == 401
    regranted = await grant(client, ctx["auth"], patient_id)
    assert regranted.status_code == 201 and regranted.json()["invitation_url"]
    assert (await accept(client, regranted.json()["invitation_url"], "parola-noua-portal")).status_code == 200
    assert (await login(client, ctx["clinic_id"], password="parola-noua-portal")).status_code == 200


async def test_refresh_rotates_and_logout_ends_the_access_token(env):
    client, _sessions = env
    ctx = await clinic(client)
    session = await portal(client, ctx, await add_patient(client, ctx))
    rotated = await client.post("/api/portal/auth/refresh", json={"refresh_token": session["refresh"]})
    assert rotated.status_code == 200
    assert (await client.post("/api/portal/auth/refresh", json={"refresh_token": session["refresh"]})).status_code == 401
    auth = h(rotated.json()["access_token"])
    assert (await client.post("/api/portal/auth/logout", headers=auth)).status_code == 204
    assert (await client.get("/api/portal/me", headers=auth)).status_code == 401
    assert (await client.post("/api/portal/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]})).status_code == 401


# 3. Lockout ------------------------------------------------------------------------------------

async def shift_attempts(sessions, minutes):
    async with sessions() as session:
        for row in (await session.scalars(select(PortalLoginAttempt))).all():
            row.occurred_at = row.occurred_at - timedelta(minutes=minutes)
        await session.commit()


async def test_five_failures_lock_for_fifteen_minutes_with_the_same_message(env):
    client, sessions = env
    ctx = await clinic(client)
    await portal(client, ctx, await add_patient(client, ctx))
    unknown = await login(client, ctx["clinic_id"], email="nimeni@example.com")
    assert unknown.status_code == 401 and unknown.json()["detail"] == GENERIC
    for _ in range(5):
        wrong = await login(client, ctx["clinic_id"], password="parola-gresita-123")
        assert wrong.status_code == 401 and wrong.json()["detail"] == GENERIC
    locked = await login(client, ctx["clinic_id"])
    assert locked.status_code == 401 and locked.json()["detail"] == GENERIC
    other_clinic = await login(client, str(uuid4()))
    assert other_clinic.status_code == 401 and other_clinic.json()["detail"] == GENERIC
    await shift_attempts(sessions, 14)
    assert (await login(client, ctx["clinic_id"])).status_code == 401
    await shift_attempts(sessions, 2)
    assert (await login(client, ctx["clinic_id"])).status_code == 200


async def test_unknown_email_is_locked_like_a_known_one(env):
    client, _sessions = env
    ctx = await clinic(client)
    for _ in range(5):
        await login(client, ctx["clinic_id"], email="nimeni@example.com")
    blocked = await login(client, ctx["clinic_id"], email="nimeni@example.com")
    assert blocked.status_code == 401 and blocked.json()["detail"] == GENERIC


# 2. Separate tokens, on every router -------------------------------------------------------------

PUBLIC = {
    ("post", "/api/auth/register"), ("post", "/api/auth/login"), ("post", "/api/auth/refresh"),
    ("post", "/api/staff/invitations/accept"), ("get", "/health"),
    ("get", "/api/portal/clinics/{clinic_id}"), ("post", "/api/portal/auth/login"), ("post", "/api/portal/auth/refresh"),
    ("post", "/api/portal/auth/invitation"), ("post", "/api/portal/auth/accept"),
}


def operations():
    spec = app.openapi()
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            yield method, path, operation.get("tags", ["-"])[0]


def concrete(path):
    import re
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid4()), path)


async def test_portal_token_is_refused_by_every_staff_route(env):
    client, _sessions = env
    ctx = await clinic(client)
    session = await portal(client, ctx, await add_patient(client, ctx))
    checked, tags = [], set()
    for method, path, tag in operations():
        if path.startswith("/api/portal/") or (method, path) in PUBLIC:
            continue
        response = await client.request(method.upper(), concrete(path), headers=session["auth"], json={})
        assert response.status_code == 401, (method, path, response.status_code, response.text[:200])
        checked.append(path)
        tags.add(tag)
    assert len(checked) > 150
    expected = {"auth", "patients", "appointments", "documents", "treatment_plans", "forms", "finance", "staff", "portal-staff",
                "collab", "materials", "inventory", "equipment", "team", "waitlist"}
    assert expected <= tags, expected - tags
    assert (await client.post("/api/auth/refresh", json={"refresh_token": session["refresh"]})).status_code == 401


async def test_staff_token_is_refused_by_every_portal_route(env):
    client, _sessions = env
    ctx = await clinic(client)
    checked = 0
    for method, path, _tag in operations():
        if not path.startswith("/api/portal/") or (method, path) in PUBLIC:
            continue
        response = await client.request(method.upper(), concrete(path), headers=ctx["auth"], json={})
        assert response.status_code == 401, (method, path, response.status_code)
        checked += 1
    assert checked >= 12
    staff_refresh = (await client.post("/api/auth/login", json={"email": ctx["me"]["email"], "password": PASSWORD})).json()["refresh_token"]
    assert (await client.post("/api/portal/auth/refresh", json={"refresh_token": staff_refresh})).status_code == 401


async def test_forged_portal_token_with_staff_key_is_refused(env):
    from jose import jwt
    client, _sessions = env
    ctx = await clinic(client)
    session = await portal(client, ctx, await add_patient(client, ctx))
    claims = jwt.get_unverified_claims(session["auth"]["Authorization"].split()[1])
    forged = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    assert (await client.get("/api/portal/me", headers=h(forged))).status_code == 401
    staff_claims = jwt.get_unverified_claims(ctx["auth"]["Authorization"].split()[1])
    from app.portal.security import portal_key
    relabelled = jwt.encode({**staff_claims, "typ": "portal", "aud": "dentacare-portal", "sid": str(uuid4()), "cid": ctx["clinic_id"]}, portal_key(), algorithm="HS256")
    assert (await client.get("/api/portal/me", headers=h(relabelled))).status_code == 401
    assert (await client.get("/api/patients", headers=h(relabelled))).status_code == 401


# Isolation: other clinic and unlinked patient ------------------------------------------------------

async def test_other_clinic_gets_404_on_staff_routes(env):
    client, _sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    session = await portal(client, ctx, patient_id)
    appointment_id = await add_appointment(client, ctx, patient_id)
    request = await client.post(f"/api/portal/patients/{patient_id}/appointments/{appointment_id}/reschedule-requests",
                                headers=session["auth"], json={"message": "Altă zi", "client_request_id": uid()})
    foreign = await clinic(client, name="Altă clinică")
    responses = [
        await client.get(f"/api/portal-staff/patients/{patient_id}", headers=foreign["auth"]),
        await grant(client, foreign["auth"], patient_id, "x@example.com"),
        await client.post(f"/api/portal-staff/accounts/{session['account_id']}/invitation", headers=foreign["auth"]),
        await client.post(f"/api/portal-staff/accounts/{session['account_id']}/revoke", headers=foreign["auth"], json={"reason": "x"}),
        await client.post(f"/api/portal-staff/links/{session['link_id']}/revoke", headers=foreign["auth"]),
        await client.post(f"/api/portal-staff/reschedule-requests/{request.json()['id']}/resolve", headers=foreign["auth"], json={"resolution": "rejected"}),
        await client.get(f"/api/portal-staff/patients/{patient_id}/audit", headers=foreign["auth"]),
        await client.post(f"/api/portal-staff/patients/{patient_id}/forms", headers=foreign["auth"], json={"template_version_id": uid(), "client_request_id": uid()}),
    ]
    assert [r.status_code for r in responses] == [404] * len(responses)
    assert (await client.get("/api/portal-staff/reschedule-requests", headers=foreign["auth"])).json() == []
    assert (await client.get("/api/portal/me", headers=session["auth"])).status_code == 200


async def test_account_of_other_clinic_and_unlinked_patient_get_404(env):
    client, _sessions = env
    ctx = await clinic(client)
    mine = await add_patient(client, ctx, "Ana")
    sibling = await add_patient(client, ctx, "Ion")
    sibling_visit = await add_appointment(client, ctx, sibling)
    session = await portal(client, ctx, mine)
    foreign = await clinic(client, name="Altă clinică")
    foreign_patient = await add_patient(client, foreign)
    foreign_session = await portal(client, foreign, foreign_patient, email="ana.portal@example.com")
    assert (await client.get("/api/portal/me", headers=foreign_session["auth"])).json()["patients"][0]["id"] == foreign_patient
    for target, auth in ((sibling, session["auth"]), (mine, foreign_session["auth"])):
        paths = [f"/api/portal/patients/{target}/appointments", f"/api/portal/patients/{target}/documents",
                 f"/api/portal/patients/{target}/plans", f"/api/portal/patients/{target}/forms"]
        assert [(await client.get(path, headers=auth)).status_code for path in paths] == [404] * 4
    assert (await client.post(f"/api/portal/patients/{sibling}/appointments/{sibling_visit}/confirm", headers=session["auth"])).status_code == 404
    assert (await client.post(f"/api/portal/patients/{mine}/appointments/{sibling_visit}/confirm", headers=session["auth"])).status_code == 404


# 4. Representative ----------------------------------------------------------------------------------

async def test_representative_with_two_children(env):
    client, _sessions = env
    ctx = await clinic(client)
    first, second = await add_patient(client, ctx, "Maria"), await add_patient(client, ctx, "Mihai")
    first_visit, second_visit = await add_appointment(client, ctx, first, 3), await add_appointment(client, ctx, second, 4)
    parent = await portal(client, ctx, first, email="parinte@example.com", relationship="parent")
    added = await grant(client, ctx["auth"], second, "parinte@example.com", "parent")
    assert added.status_code == 201 and added.json()["invitation_url"] is None and added.json()["account_id"] == parent["account_id"]
    me = (await client.get("/api/portal/me", headers=parent["auth"])).json()
    assert [row["id"] for row in me["patients"]] == [first, second] and {row["relationship"] for row in me["patients"]} == {"parent"}
    one = (await client.get(f"/api/portal/patients/{first}/appointments", headers=parent["auth"])).json()
    two = (await client.get(f"/api/portal/patients/{second}/appointments", headers=parent["auth"])).json()
    assert [row["id"] for row in one["upcoming"]] == [first_visit] and [row["id"] for row in two["upcoming"]] == [second_visit]
    assert (await grant(client, ctx["auth"], second, "parinte@example.com", "parent")).status_code == 409
    revoked = await client.post(f"/api/portal-staff/links/{added.json()['link_id']}/revoke", headers=ctx["auth"])
    assert revoked.status_code == 200
    assert (await client.get(f"/api/portal/patients/{second}/appointments", headers=parent["auth"])).status_code == 404
    assert (await client.get(f"/api/portal/patients/{first}/appointments", headers=parent["auth"])).status_code == 200
    section = (await client.get(f"/api/portal-staff/patients/{second}", headers=ctx["auth"])).json()
    assert section["accounts"][0]["link_active"] is False and section["accounts"][0]["relationship_label"] == "Părinte"


# 5. Appointments --------------------------------------------------------------------------------------

async def test_appointments_show_only_public_fields(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    upcoming = await add_appointment(client, ctx, patient_id)
    past = await add_appointment(client, ctx, patient_id, 6)
    await set_appointment(sessions, past, starts_at=utcnow() - timedelta(days=3), ends_at=utcnow() - timedelta(days=3) + timedelta(minutes=30))
    session = await portal(client, ctx, patient_id)
    response = await client.get(f"/api/portal/patients/{patient_id}/appointments", headers=session["auth"])
    body = response.json()
    assert [row["id"] for row in body["upcoming"]] == [upcoming] and [row["id"] for row in body["past"]] == [past]
    row = body["upcoming"][0]
    assert row["doctor_name"] == "Manager Fictiv" and row["clinic_name"] == "Clinica Fictivă T14" and len(row["local_time"]) == 5
    assert set(row) == {"id", "starts_at", "local_date", "local_time", "duration_minutes", "doctor_name", "clinic_name", "status",
                        "status_label", "confirmed_by_patient_at", "reschedule_request", "can_confirm", "can_request_reschedule"}
    for secret in ("Notă internă secretă", "Tip intern confidențial", "Cabinet Intern Șapte", ctx["room_id"]):
        assert secret not in response.text
    assert body["past"][0]["can_confirm"] is False


async def test_double_confirmation_is_one_record_and_closed_visits_refuse(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    visit = await add_appointment(client, ctx, patient_id, 3)
    cancelled = await add_appointment(client, ctx, patient_id, 4)
    past = await add_appointment(client, ctx, patient_id, 5)
    await set_appointment(sessions, cancelled, status="cancelled")
    await set_appointment(sessions, past, starts_at=utcnow() - timedelta(hours=2))
    session = await portal(client, ctx, patient_id)
    url = f"/api/portal/patients/{patient_id}/appointments/{visit}/confirm"
    first, second = await client.post(url, headers=session["auth"]), await client.post(url, headers=session["auth"])
    assert first.status_code == second.status_code == 200 and second.json()["retry"] is True
    assert first.json()["confirmed_at"] == second.json()["confirmed_at"]
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PortalAppointmentConfirmation)) == 1
        assert (await db.get(Appointment, UUID(visit))).status == "scheduled"
    for target in (cancelled, past):
        assert (await client.post(f"/api/portal/patients/{patient_id}/appointments/{target}/confirm", headers=session["auth"])).status_code == 409
    listed = (await client.get(f"/api/portal-staff/patients/{patient_id}/confirmations", headers=ctx["auth"])).json()
    assert len(listed) == 1 and listed[0]["confirmed_by"] == "Ana Fictivă"


async def test_reschedule_request_never_moves_the_appointment(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    visit = await add_appointment(client, ctx, patient_id)
    async with sessions() as db:
        before = (await db.get(Appointment, UUID(visit))).starts_at
    session = await portal(client, ctx, patient_id)
    url = f"/api/portal/patients/{patient_id}/appointments/{visit}/reschedule-requests"
    key = uid()
    first = await client.post(url, headers=session["auth"], json={"message": "Pot doar după ora 16.", "client_request_id": key})
    retry = await client.post(url, headers=session["auth"], json={"message": "Pot doar după ora 16.", "client_request_id": key})
    assert first.status_code == retry.status_code == 201 and retry.json()["retry"] is True and retry.json()["id"] == first.json()["id"]
    assert (await client.post(url, headers=session["auth"], json={"message": "Alt mesaj", "client_request_id": key})).status_code == 409
    assert (await client.post(url, headers=session["auth"], json={"message": "Încă una", "client_request_id": uid()})).status_code == 409
    assert (await client.post(url, headers=session["auth"], json={"message": "   ", "client_request_id": uid()})).status_code == 422
    async with sessions() as db:
        appointment = await db.get(Appointment, UUID(visit))
        assert appointment.starts_at == before and appointment.status == "scheduled"
    reception = await staff(sessions, ctx, "reception")
    listed = (await client.get("/api/portal-staff/reschedule-requests", headers=reception)).json()
    assert len(listed) == 1 and listed[0]["message"] == "Pot doar după ora 16." and listed[0]["patient_name"] == "Ana Fictivă"
    shown = (await client.get(f"/api/portal/patients/{patient_id}/appointments", headers=session["auth"])).json()["upcoming"][0]
    assert shown["reschedule_request"]["status"] == "open" and shown["can_request_reschedule"] is False
    resolved = await client.post(f"/api/portal-staff/reschedule-requests/{first.json()['id']}/resolve", headers=reception,
                                 json={"resolution": "rescheduled", "note": "Mutată telefonic"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "rescheduled"
    assert (await client.post(f"/api/portal-staff/reschedule-requests/{first.json()['id']}/resolve", headers=reception,
                              json={"resolution": "rejected"})).status_code == 409
    assert (await client.get("/api/portal-staff/reschedule-requests", headers=reception)).json() == []
    assert len((await client.get("/api/portal-staff/reschedule-requests?status=resolved", headers=reception)).json()) == 1
    second = await client.post(url, headers=session["auth"], json={"message": "Încă o dată", "client_request_id": uid()})
    assert second.status_code == 201


# 6. Documents and plans -------------------------------------------------------------------------------

async def test_document_published_then_withdrawn(env):
    client, _sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    published = await upload(client, ctx["auth"], patient_id, png(), "radiografie.png", category="radiograph")
    hidden = await upload(client, ctx["auth"], patient_id, png(5, 5), "intern.png", category="photo")
    assert published.status_code == hidden.status_code == 201
    session = await portal(client, ctx, patient_id)
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents", headers=session["auth"])).json() == []
    publication = await client.post(f"/api/portal-staff/patients/{patient_id}/documents", headers=ctx["auth"], json={"document_id": published.json()["id"]})
    again = await client.post(f"/api/portal-staff/patients/{patient_id}/documents", headers=ctx["auth"], json={"document_id": published.json()["id"]})
    assert publication.status_code == again.status_code == 201 and again.json()["retry"] is True and again.json()["id"] == publication.json()["id"]
    listed = (await client.get(f"/api/portal/patients/{patient_id}/documents", headers=session["auth"])).json()
    assert [row["filename"] for row in listed] == ["radiografie.png"] and "intern.png" not in str(listed)
    content = await client.get(f"/api/portal/patients/{patient_id}/documents/{listed[0]['id']}/content", headers=session["auth"])
    assert content.status_code == 200 and content.content == png() and content.headers["content-disposition"].startswith("attachment")
    assert content.headers["cache-control"] == "private, no-store"
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents/{hidden.json()['id']}/content", headers=session["auth"])).status_code == 404
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents/{listed[0]['id']}/content")).status_code == 401
    withdrawn = await client.post(f"/api/portal-staff/document-publications/{publication.json()['id']}/withdraw", headers=ctx["auth"])
    assert withdrawn.status_code == 200
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents/{listed[0]['id']}/content", headers=session["auth"])).status_code == 404
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents", headers=session["auth"])).json() == []
    republished = await client.post(f"/api/portal-staff/patients/{patient_id}/documents", headers=ctx["auth"], json={"document_id": published.json()["id"]})
    assert republished.status_code == 201 and republished.json()["id"] != publication.json()["id"]
    archived = await client.post(f"/api/documents/{published.json()['id']}/archive", headers=ctx["auth"], json={"reason": "Greșit"})
    assert archived.status_code == 200
    assert (await client.get(f"/api/portal/patients/{patient_id}/documents/{republished.json()['id']}/content", headers=session["auth"])).status_code == 404


async def test_document_of_another_patient_cannot_be_published(env):
    client, _sessions = env
    ctx = await clinic(client)
    first, second = await add_patient(client, ctx), await add_patient(client, ctx, "Ion")
    document = (await upload(client, ctx["auth"], second, png(), "ion.png")).json()
    response = await client.post(f"/api/portal-staff/patients/{first}/documents", headers=ctx["auth"], json={"document_id": document["id"]})
    assert response.status_code == 404


async def make_plan(client, ctx, patient_id):
    procedure = await client.post("/api/procedures", headers=ctx["auth"], json={
        "code": f"P{uuid4().hex[:6]}", "name": "Obturație fictivă", "base_price": "150.00", "default_duration_minutes": 40})
    assert procedure.status_code == 201, procedure.text
    plan = await client.post(f"/api/treatment-plans/patients/{patient_id}", headers=ctx["auth"], json={
        "title": "Plan de tratament fictiv", "notes": "NOTĂ INTERNĂ PLAN",
        "stages": [{"title": "Etapa 1", "notes": "NOTĂ INTERNĂ ETAPĂ", "items": [
            {"procedure_catalog_id": procedure.json()["id"], "tooth_fdi": "16", "notes": "NOTĂ INTERNĂ MANOPERĂ"},
            {"procedure_catalog_id": procedure.json()["id"], "tooth_fdi": "26"}]}]})
    assert plan.status_code == 201, plan.text
    return plan.json(), procedure.json()


async def test_published_plan_is_a_frozen_copy_without_internal_notes(env):
    client, _sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    plan, procedure = await make_plan(client, ctx, patient_id)
    session = await portal(client, ctx, patient_id)
    assert (await client.get(f"/api/portal/patients/{patient_id}/plans", headers=session["auth"])).json() == []
    published = await client.post(f"/api/portal-staff/patients/{patient_id}/plans", headers=ctx["auth"], json={"plan_id": plan["id"]})
    assert published.status_code == 201
    response = await client.get(f"/api/portal/patients/{patient_id}/plans", headers=session["auth"])
    shown = response.json()[0]
    assert shown["title"] == "Plan de tratament fictiv" and shown["version"] == 1
    assert [item["tooth_fdi"] for item in shown["stages"][0]["items"]] == ["16", "26"]
    assert shown["totals"] == [{"currency": "RON", "estimated_total": "300.00", "estimated_minutes": 80}]
    assert "NOTĂ INTERNĂ" not in response.text and "notes" not in response.text and procedure["id"] not in response.text
    stage_id = plan["stages"][0]["id"]
    added = await client.post(f"/api/treatment-plans/{plan['id']}/stages/{stage_id}/items", headers=ctx["auth"],
                              json={"procedure_catalog_id": procedure["id"], "tooth_fdi": "36"})
    assert added.status_code == 201, added.text
    frozen = (await client.get(f"/api/portal/patients/{patient_id}/plans", headers=session["auth"])).json()[0]
    assert len(frozen["stages"][0]["items"]) == 2
    republished = await client.post(f"/api/portal-staff/patients/{patient_id}/plans", headers=ctx["auth"], json={"plan_id": plan["id"]})
    assert republished.status_code == 201 and republished.json()["retry"] is False and republished.json()["id"] != published.json()["id"]
    same = await client.post(f"/api/portal-staff/patients/{patient_id}/plans", headers=ctx["auth"], json={"plan_id": plan["id"]})
    assert same.json()["retry"] is True and same.json()["id"] == republished.json()["id"]
    current = (await client.get(f"/api/portal/patients/{patient_id}/plans", headers=session["auth"])).json()
    assert len(current) == 1 and len(current[0]["stages"][0]["items"]) == 3
    withdrawn = await client.post(f"/api/portal-staff/plan-publications/{republished.json()['id']}/withdraw", headers=ctx["auth"])
    assert withdrawn.status_code == 200
    assert (await client.get(f"/api/portal/patients/{patient_id}/plans", headers=session["auth"])).json() == []


# 7. Forms -----------------------------------------------------------------------------------------------

async def published_form(client, ctx):
    template = await client.post("/api/forms/templates", headers=ctx["auth"], json={"name": "Anamneză fictivă"})
    version = await client.post(f"/api/forms/templates/{template.json()['id']}/versions", headers=ctx["auth"], json={"definition": {"sections": [
        {"id": "s1", "title": "Sănătate", "questions": [
            {"id": "alergii", "type": "yes_no", "label": "Aveți alergii?", "required": True},
            {"id": "detalii", "type": "short_text", "label": "Care?", "required": True,
             "condition": {"question_id": "alergii", "operator": "equals", "value": "yes"}}]}]}})
    assert version.status_code == 201, version.text
    assert (await client.post(f"/api/forms/versions/{version.json()['id']}/publish", headers=ctx["auth"])).status_code == 200
    return version.json()["id"]


async def test_patient_fills_form_and_staff_sees_a_patient_revision(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    version_id = await published_form(client, ctx)
    session = await portal(client, ctx, patient_id)
    internal = await client.post(f"/api/forms/patients/{patient_id}/{version_id}", headers=ctx["auth"])
    key = uid()
    sent = await client.post(f"/api/portal-staff/patients/{patient_id}/forms", headers=ctx["auth"], json={"template_version_id": version_id, "client_request_id": key})
    retry = await client.post(f"/api/portal-staff/patients/{patient_id}/forms", headers=ctx["auth"], json={"template_version_id": version_id, "client_request_id": key})
    assert sent.status_code == retry.status_code == 201 and retry.json()["id"] == sent.json()["id"]
    listed = (await client.get(f"/api/portal/patients/{patient_id}/forms", headers=session["auth"])).json()
    assert [row["id"] for row in listed] == [sent.json()["id"]]
    assert (await client.get(f"/api/portal/patients/{patient_id}/forms/{internal.json()['id']}", headers=session["auth"])).status_code == 404
    base = f"/api/portal/patients/{patient_id}/forms/{sent.json()['id']}"
    assert (await client.post(f"{base}/submit", headers=session["auth"], json={"answers": {"alergii": True}})).status_code == 422
    assert (await client.put(f"{base}/answers", headers=session["auth"], json={"answers": {"alergii": True}})).status_code == 200
    submitted = await client.post(f"{base}/submit", headers=session["auth"], json={"answers": {"alergii": True, "detalii": "Penicilină"}})
    assert submitted.status_code == 200 and submitted.json()["revision_number"] == 1
    assert (await client.post(f"{base}/submit", headers=session["auth"], json={"answers": {"alergii": False}})).status_code == 409
    t12 = (await client.get(f"/api/forms/{sent.json()['id']}", headers=ctx["auth"])).json()
    assert t12["source"] == "patient" and t12["status"] == "submitted" and t12["answers"]["detalii"] == "Penicilină"
    review = await client.post(f"/api/forms/{sent.json()['id']}/review", headers=ctx["auth"], json={"decision": "needs_clarification", "reason": "Doza?"})
    assert review.status_code == 200
    resubmitted = await client.post(f"{base}/submit", headers=session["auth"], json={"answers": {"alergii": True, "detalii": "Penicilină, urticarie"}})
    assert resubmitted.status_code == 200 and resubmitted.json()["revision_number"] == 2
    section = (await client.get(f"/api/portal-staff/patients/{patient_id}", headers=ctx["auth"])).json()
    form = next(row for row in section["forms"] if row["id"] == sent.json()["id"])
    assert [row["revision_number"] for row in form["submissions"]] == [1, 2]
    assert {row["label"] for row in form["submissions"]} == {"Completat de pacient"} and form["submissions"][0]["completed_by"] == "Ana Fictivă"
    assert form["submissions"][0]["answers"]["detalii"] == "Penicilină"
    async with sessions() as db:
        assert (await db.get(PatientForm, UUID(sent.json()["id"]))).source == "patient"
    detail = (await client.get(base, headers=session["auth"])).json()
    assert "Doza?" not in str(detail)


# Roles ---------------------------------------------------------------------------------------------------

async def test_staff_roles(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    document = (await upload(client, ctx["auth"], patient_id, png(), "rx.png")).json()
    roles = {role: await staff(sessions, ctx, role) for role in ("doctor", "assistant", "reception", "administrator")}
    expected_access = {"doctor": 201, "assistant": 403, "reception": 201, "administrator": 201}
    for index, (role, auth) in enumerate(roles.items()):
        response = await grant(client, auth, patient_id, f"rol{index}@example.com")
        assert response.status_code == expected_access[role], role
    for role, auth in roles.items():
        publish = await client.post(f"/api/portal-staff/patients/{patient_id}/documents", headers=auth, json={"document_id": document["id"]})
        assert publish.status_code == (201 if role in ("doctor", "administrator") else 403), role
        requests = await client.get("/api/portal-staff/reschedule-requests", headers=auth)
        assert requests.status_code == (200 if role in ("reception", "administrator") else 403), role
        audit = await client.get(f"/api/portal-staff/patients/{patient_id}/audit", headers=auth)
        assert audit.status_code == (200 if role == "administrator" else 403), role
        section = (await client.get(f"/api/portal-staff/patients/{patient_id}", headers=auth)).json()
        assert (section["documents"] is None) == (role in ("assistant", "reception")), role
    assert (await client.get("/api/portal-staff/reschedule-requests", headers=ctx["auth"])).status_code == 200


# 8. Audit ------------------------------------------------------------------------------------------------

async def test_audit_records_login_document_confirmation_and_request(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    visit = await add_appointment(client, ctx, patient_id)
    document = (await upload(client, ctx["auth"], patient_id, png(), "rx.png")).json()
    publication = (await client.post(f"/api/portal-staff/patients/{patient_id}/documents", headers=ctx["auth"], json={"document_id": document["id"]})).json()
    session = await portal(client, ctx, patient_id)
    await login(client, ctx["clinic_id"], password="parola-gresita-123")
    await client.get(f"/api/portal/patients/{patient_id}/documents/{publication['id']}/content", headers=session["auth"])
    await client.post(f"/api/portal/patients/{patient_id}/appointments/{visit}/confirm", headers=session["auth"])
    await client.post(f"/api/portal/patients/{patient_id}/appointments/{visit}/reschedule-requests", headers=session["auth"],
                      json={"message": "Altă oră", "client_request_id": uid()})
    async with sessions() as db:
        events = (await db.scalars(select(PortalAuditEvent).order_by(PortalAuditEvent.occurred_at))).all()
    by_action = {event.action: event for event in events}
    for action in ("invitation_accepted", "login", "login_failed", "document_downloaded", "appointment_confirmed", "reschedule_requested"):
        assert action in by_action, action
        assert str(by_action[action].account_id) == session["account_id"] and by_action[action].occurred_at is not None
    for action in ("document_downloaded", "appointment_confirmed", "reschedule_requested"):
        assert str(by_action[action].patient_id) == patient_id
    assert {"access_granted", "invitation_created", "document_published"} <= set(by_action)
    listed = (await client.get(f"/api/portal-staff/patients/{patient_id}/audit", headers=ctx["auth"])).json()
    assert {"login", "document_downloaded", "appointment_confirmed", "reschedule_requested"} <= {row["action"] for row in listed}
    assert any(row["account"] == "Ana Fictivă" for row in listed)


# Query counts ------------------------------------------------------------------------------------------

async def count_statements(sessions, call):
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = await call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    return len(statements), response.json()


async def test_lists_do_not_grow_queries_with_rows(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    session = await portal(client, ctx, patient_id)

    async def visit_with_request(days):
        visit = await add_appointment(client, ctx, patient_id, days)
        await client.post(f"/api/portal/patients/{patient_id}/appointments/{visit}/confirm", headers=session["auth"])
        await client.post(f"/api/portal/patients/{patient_id}/appointments/{visit}/reschedule-requests", headers=session["auth"],
                          json={"message": "Altă zi", "client_request_id": uid()})

    await visit_with_request(2)
    appointments = lambda: client.get(f"/api/portal/patients/{patient_id}/appointments", headers=session["auth"])
    requests = lambda: client.get("/api/portal-staff/reschedule-requests", headers=ctx["auth"])
    section = lambda: client.get(f"/api/portal-staff/patients/{patient_id}", headers=ctx["auth"])
    base = [(await count_statements(sessions, call))[0] for call in (appointments, requests, section)]
    for days in range(3, 8):
        await visit_with_request(days)
    after = [await count_statements(sessions, call) for call in (appointments, requests, section)]
    assert [count for count, _body in after] == base
    assert len(after[0][1]["upcoming"]) == 6 and len(after[1][1]) == 6


async def test_open_request_index_blocks_a_second_open_request(env):
    client, sessions = env
    ctx = await clinic(client)
    patient_id = await add_patient(client, ctx)
    visit = await add_appointment(client, ctx, patient_id)
    session = await portal(client, ctx, patient_id)
    await client.post(f"/api/portal/patients/{patient_id}/appointments/{visit}/reschedule-requests", headers=session["auth"],
                      json={"message": "Altă zi", "client_request_id": uid()})
    from sqlalchemy.exc import IntegrityError
    async with sessions() as db:
        existing = (await db.scalars(select(PortalRescheduleRequest))).one()
        db.add(PortalRescheduleRequest(clinic_id=existing.clinic_id, appointment_id=existing.appointment_id, patient_id=existing.patient_id,
                                       account_id=existing.account_id, message="dublură", client_request_id=uuid4(), status="open"))
        with pytest.raises(IntegrityError):
            await db.commit()
