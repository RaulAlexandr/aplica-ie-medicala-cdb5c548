"""A1 appointment procedure replacement, isolation, roles and append-only history."""
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.appointment_procedures.models import AppointmentProcedureRevision
from app.auth.service import create_access_token
from app.database import (
    Appointment,
    Base,
    TreatmentPlan,
    TreatmentPlanItem,
    TreatmentPlanStage,
    User,
    get_db,
)
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def env():
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


def headers(token):
    return {"Authorization": f"Bearer {token}"}


async def clinic(client, name="A1 Fictivă"):
    response = await client.post("/api/auth/register", json={"clinic_name": name, "full_name": "Doctor A1",
        "email": f"{name.replace(' ', '').lower()}@example.com", "password": PASSWORD})
    assert response.status_code == 201, response.text
    auth = headers(response.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=auth)).json()
    room = await client.post("/api/rooms", headers=auth, json={"name": "Cabinet A1"})
    patient = await client.post("/api/patients", headers=auth, json={"first_name": "Ana", "last_name": "Fictivă"})
    appointment = await client.post("/api/appointments", headers=auth, json={"patient_id": patient.json()["id"],
        "doctor_id": me["id"], "room_id": room.json()["id"], "starts_at": "2026-12-01T09:00:00+00:00",
        "duration_minutes": 30, "appointment_type": "Consultație"})
    catalog = await client.post("/api/procedures", headers=auth, json={"code": "obt", "name": "Obturație",
        "base_price": "100.00", "default_duration_minutes": 30})
    assert appointment.status_code == 201 and catalog.status_code == 201
    return {"auth": auth, "user": me, "patient": patient.json()["id"], "room": room.json()["id"],
            "appointment": appointment.json()["id"], "procedure": catalog.json()["id"]}


async def staff_token(sessions, ctx, role):
    user = User(id=__import__("uuid").uuid4(), clinic_id=UUID(ctx["user"]["clinic_id"]), email=f"{role}@a1.test",
                password_hash="unused", full_name=role, role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return headers(create_access_token(user))


async def test_replacement_revision_history_role_and_closed_states(env):
    client, sessions = env
    ctx = await clinic(client)
    async with sessions() as session:
        from uuid import uuid4

        from app.database import utcnow
        clinic_id, user_id = UUID(ctx["user"]["clinic_id"]), UUID(ctx["user"]["id"])
        plan_id, stage_id = uuid4(), uuid4()
        session.add(TreatmentPlan(id=plan_id, clinic_id=clinic_id, patient_id=UUID(ctx["patient"]), title="Plan A1",
            status="accepted", version=1, accepted_version=1, created_by_id=user_id))
        session.add(TreatmentPlanStage(id=stage_id, clinic_id=clinic_id, plan_id=plan_id, position=1, title="Etapa 1"))
        for position in (1, 2):
            session.add(TreatmentPlanItem(clinic_id=clinic_id, plan_id=plan_id, stage_id=stage_id, position=position,
                procedure_catalog_id=UUID(ctx["procedure"]), procedure_code="obt", procedure_name="Obturație",
                currency="RON", estimated_price=Decimal("100.00"), estimated_duration_minutes=30, status="active",
                appointment_id=UUID(ctx["appointment"]), appointment_linked_by_id=user_id, appointment_linked_at=utcnow()))
        await session.commit()
    url = f"/api/appointment-procedures/{ctx['appointment']}"
    read = await client.get(url, headers=ctx["auth"])
    assert read.status_code == 200 and read.json()["revision"] == 0
    receptionist = await staff_token(sessions, ctx, "reception")
    saved = await client.put(url, headers=receptionist, json={"expected_revision": 0,
        "procedures": [{"procedure_catalog_id": ctx["procedure"], "quantity": 1}]})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert saved.json()["procedures"][0]["source"] == "both" and saved.json()["procedures"][0]["quantity"] == 2
    same = await client.put(url, headers=receptionist, json={"expected_revision": 1,
        "procedures": [{"procedure_catalog_id": ctx["procedure"], "quantity": 1}]})
    assert same.status_code == 200 and same.json()["revision"] == 1
    stale = await client.put(url, headers=receptionist, json={"expected_revision": 0, "procedures": []})
    assert stale.status_code == 409
    changed = await client.put(url, headers=receptionist, json={"expected_revision": 1,
        "procedures": [{"procedure_catalog_id": ctx["procedure"], "quantity": 2}]})
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    history = await client.get(url + "/history", headers=ctx["auth"])
    assert history.status_code == 200 and len(history.json()) == 2
    assert history.json()[0]["removed_at"] and history.json()[1]["removed_at"] is None
    assistant = await staff_token(sessions, ctx, "assistant")
    forbidden = await client.put(url, headers=assistant, json={"expected_revision": 2, "procedures": []})
    assert forbidden.status_code == 403
    async with sessions() as session:
        appointment = await session.get(Appointment, UUID(ctx["appointment"]))
        appointment.status = "completed"
        await session.commit()
    closed = await client.put(url, headers=receptionist, json={"expected_revision": 2, "procedures": []})
    assert closed.status_code == 409


async def test_duplicate_bounds_and_clinic_isolation(env):
    client, _ = env
    first, second = await clinic(client, "Clinica unu A1"), await clinic(client, "Clinica doi A1")
    url = f"/api/appointment-procedures/{first['appointment']}"
    dup = await client.put(url, headers=first["auth"], json={"expected_revision": 0, "procedures":[
        {"procedure_catalog_id": first["procedure"], "quantity": 1}, {"procedure_catalog_id": first["procedure"], "quantity": 2}]})
    assert dup.status_code == 422 and "Value error" not in dup.text
    bad_quantity = await client.put(url, headers=first["auth"], json={"expected_revision": 0,
        "procedures": [{"procedure_catalog_id": first["procedure"], "quantity": 33}]})
    assert bad_quantity.status_code == 422 and "Value error" not in bad_quantity.text
    other = await client.get(url, headers=second["auth"])
    assert other.status_code == 404
    cross_clinic_procedure = await client.put(url, headers=first["auth"], json={"expected_revision": 0,
        "procedures": [{"procedure_catalog_id": second["procedure"], "quantity": 1}]})
    assert cross_clinic_procedure.status_code == 404
    inactive = await client.post("/api/procedures", headers=first["auth"], json={"code": "inactiv", "name": "Procedură inactivă",
        "base_price": "10.00", "default_duration_minutes": 10})
    inactive_id = inactive.json()["id"]
    assert (await client.post(f"/api/procedures/{inactive_id}/deactivate", headers=first["auth"])).status_code == 200
    rejected = await client.put(url, headers=first["auth"], json={"expected_revision": 0,
        "procedures": [{"procedure_catalog_id": inactive_id, "quantity": 1}]})
    assert rejected.status_code == 422 and "inactivă" in rejected.json()["detail"]


async def test_interval_endpoint_excludes_cancelled_and_no_show(env):
    client, sessions = env
    ctx = await clinic(client)
    response = await client.get("/api/appointment-procedures", headers=ctx["auth"], params={
        "start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z"})
    assert response.status_code == 200 and [item["appointment_id"] for item in response.json()] == [ctx["appointment"]]
    second = await client.post("/api/appointments", headers=ctx["auth"], json={"patient_id": ctx["patient"],
        "doctor_id": ctx["user"]["id"], "room_id": (await client.get("/api/rooms", headers=ctx["auth"])).json()[0]["id"],
        "starts_at": "2026-12-01T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Consultație"})
    assert second.status_code == 201
    async with sessions() as session:
        first_row = await session.get(Appointment, UUID(ctx["appointment"]))
        second_row = await session.get(Appointment, UUID(second.json()["id"]))
        first_row.status, second_row.status = "cancelled", "no_show"
        await session.commit()
    excluded = await client.get("/api/appointment-procedures", headers=ctx["auth"], params={
        "start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z"})
    assert excluded.status_code == 200 and excluded.json() == []
    too_long = await client.get("/api/appointment-procedures", headers=ctx["auth"], params={
        "start": "2026-01-01T00:00:00Z", "end": "2026-04-05T00:00:00Z"})
    assert too_long.status_code == 422 and "Value error" not in too_long.text


async def test_effective_lines_follow_real_treatment_plan_lifecycle(env):
    client, _ = env
    ctx = await clinic(client)
    auth = ctx["auth"]

    async def visit(day):
        response = await client.post("/api/appointments", headers=auth, json={"patient_id": ctx["patient"],
            "doctor_id": ctx["user"]["id"], "room_id": ctx["room"], "starts_at": f"2026-12-{day:02d}T09:00:00+00:00",
            "duration_minutes": 30, "appointment_type": "Consultație"})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    async def plan_for(appointment_id, count=1, day=1):
        created = await client.post(f"/api/treatment-plans/patients/{ctx['patient']}", headers=auth, json={
            "title": f"Plan {day}", "stages": [{"title": "Etapa 1", "items": [
                {"procedure_catalog_id": ctx["procedure"]} for _ in range(count)]}]})
        assert created.status_code == 201, created.text
        plan = created.json()
        for item in plan["stages"][0]["items"]:
            linked = await client.put(f"/api/treatment-plans/items/{item['id']}/appointment", headers=auth,
                                      json={"appointment_id": appointment_id})
            assert linked.status_code == 200, linked.text
        return plan

    def effective_rows(response):
        assert response.status_code == 200, response.text
        return response.json()["procedures"]

    draft_visit = await visit(2)
    await plan_for(draft_visit, day=2)
    assert effective_rows(await client.get(f"/api/appointment-procedures/{draft_visit}", headers=auth)) == []

    accepted_visit = await visit(3)
    accepted = await plan_for(accepted_visit, day=3)
    assert (await client.post(f"/api/treatment-plans/{accepted['id']}/accept", headers=auth, json={"note": "Acord"})).status_code == 200
    assert effective_rows(await client.get(f"/api/appointment-procedures/{accepted_visit}", headers=auth))[0]["source"] == "plan"

    progress_visit = await visit(4)
    progressing = await plan_for(progress_visit, count=2, day=4)
    assert (await client.post(f"/api/treatment-plans/{progressing['id']}/accept", headers=auth, json={"note": "Acord"})).status_code == 200
    performed = await client.post("/api/performed-procedures", headers=auth, json={"appointment_id": progress_visit,
        "procedure_catalog_id": ctx["procedure"]})
    assert performed.status_code == 201, performed.text
    items = progressing["stages"][0]["items"]
    linked = await client.put(f"/api/treatment-plans/items/{items[0]['id']}/performed-procedure", headers=auth,
                              json={"performed_procedure_id": performed.json()["id"]})
    assert linked.status_code == 200, linked.text
    progress_lines = effective_rows(await client.get(f"/api/appointment-procedures/{progress_visit}", headers=auth))
    assert len(progress_lines) == 1 and progress_lines[0]["plan_quantity"] == 1

    cancelled_visit = await visit(5)
    cancelled = await plan_for(cancelled_visit, day=5)
    cancelled_result = await client.post(f"/api/treatment-plans/{cancelled['id']}/cancel", headers=auth,
                                         json={"reason": "Plan retras fictiv"})
    assert cancelled_result.status_code == 200 and cancelled_result.json()["status"] == "cancelled"
    assert effective_rows(await client.get(f"/api/appointment-procedures/{cancelled_visit}", headers=auth)) == []

    completed_visit = await visit(6)
    completed = await plan_for(completed_visit, day=6)
    assert (await client.post(f"/api/treatment-plans/{completed['id']}/accept", headers=auth, json={"note": "Acord"})).status_code == 200
    done = await client.post("/api/performed-procedures", headers=auth, json={"appointment_id": completed_visit,
        "procedure_catalog_id": ctx["procedure"]})
    item_id = completed["stages"][0]["items"][0]["id"]
    assert (await client.put(f"/api/treatment-plans/items/{item_id}/performed-procedure", headers=auth,
        json={"performed_procedure_id": done.json()["id"]})).status_code == 200
    for action in ("start", "complete"):
        response = await client.post(f"/api/performed-procedures/{done.json()['id']}/{action}", headers=auth)
        assert response.status_code == 200, response.text
    finished = await client.post(f"/api/treatment-plans/{completed['id']}/complete", headers=auth)
    assert finished.status_code == 200 and finished.json()["status"] == "completed"
    assert effective_rows(await client.get(f"/api/appointment-procedures/{completed_visit}", headers=auth)) == []


async def test_get_without_revision_row_is_read_only(env):
    client, sessions = env
    ctx = await clinic(client)
    engine = sessions.kw["bind"]
    revision_inserts = []
    def record_revision_insert(conn, cursor, statement, parameters, context, executemany):
        if "insert into appointment_procedure_revisions" in statement.lower():
            revision_inserts.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", record_revision_insert)
    async with sessions() as session:
        before = await session.scalar(select(func.count()).select_from(AppointmentProcedureRevision))
    assert before == 0
    response = await client.get(f"/api/appointment-procedures/{ctx['appointment']}", headers=ctx["auth"])
    assert response.status_code == 200 and response.json()["revision"] == 0
    async with sessions() as session:
        after_get = await session.scalar(select(func.count()).select_from(AppointmentProcedureRevision))
    assert after_get == 0 and revision_inserts == []
    put = await client.put(f"/api/appointment-procedures/{ctx['appointment']}", headers=ctx["auth"], json={
        "expected_revision": 0, "procedures": []})
    assert put.status_code == 200
    async with sessions() as session:
        after_put = await session.scalar(select(func.count()).select_from(AppointmentProcedureRevision))
    assert after_put == 1 and len(revision_inserts) == 1
    event.remove(engine.sync_engine, "before_cursor_execute", record_revision_insert)
