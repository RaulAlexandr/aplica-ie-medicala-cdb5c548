from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comms.models import CommOutbox
from app.comms.service import process_due
from app.database import Appointment, Base, Patient, User, get_db
from app.feedback.models import FeedbackRequest, PatientFeedback
from app.feedback.router import CampaignFilter, _preview
from app.feedback.service import schedule_visit_messages
from app.main import app


@pytest.fixture
async def ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


async def setup(client, email):
    response = await client.post("/api/auth/register", json={"clinic_name": "Clinică test", "full_name": "Manager test", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    manager = (await client.get("/api/auth/me", headers=headers)).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet"})).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Ana", "last_name": "Test", "phone": "+40700000001", "email": "ana@example.com"})).json()
    return headers, manager, room, patient


async def book(client, headers, doctor, room, patient, days=3):
    start = (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None, hour=10, minute=0, second=0, microsecond=0)
    response = await client.post("/api/appointments", headers=headers, json={"patient_id": patient["id"], "doctor_id": doctor["id"], "room_id": room["id"], "local_start": start.isoformat(), "timezone_fold": 0, "duration_minutes": 30, "appointment_type": "Control"})
    assert response.status_code == 201, response.text
    return response.json()


async def close_and_schedule(sessions, appointment_id):
    async with sessions() as db:
        appointment = await db.get(Appointment, appointment_id)
        appointment.status = "completed"
        await schedule_visit_messages(db, appointment)
        await db.commit()


async def grant(client, headers, patient_id, channel="sms"):
    response = await client.post(f"/api/patients/{patient_id}/comms/consents", headers=headers, json={"channel": channel, "granted": True, "source": "in_person"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_visit_feedback_is_only_scheduled_with_consent_and_once(ctx):
    client, sessions = ctx
    headers, manager, room, patient = await setup(client, "t26-schedule@example.com")
    appt = await book(client, headers, manager, room, patient)
    await close_and_schedule(sessions, appt["id"])
    async with sessions() as db:
        assert await db.scalar(select(FeedbackRequest.id)) is None
    await grant(client, headers, patient["id"])
    await grant(client, headers, patient["id"], "email")
    await close_and_schedule(sessions, appt["id"])
    await close_and_schedule(sessions, appt["id"])
    async with sessions() as db:
        requests = list((await db.scalars(select(FeedbackRequest))).all())
        outbox = list((await db.scalars(select(CommOutbox).where(CommOutbox.message_type == "feedback_request"))).all())
        assert len(requests) == len(outbox) == 1
        assert requests[0].token_hash != outbox[0].body.rsplit("/feedback/", 1)[-1]


@pytest.mark.asyncio
async def test_public_feedback_token_is_scoped_limited_and_creates_one_task(ctx):
    client, sessions = ctx
    headers, manager, room, patient = await setup(client, "t26-public@example.com")
    appt = await book(client, headers, manager, room, patient)
    await grant(client, headers, patient["id"])
    await close_and_schedule(sessions, appt["id"])
    async with sessions() as db:
        message = await db.scalar(select(CommOutbox).where(CommOutbox.message_type == "feedback_request"))
        token = message.body.rsplit("/feedback/", 1)[-1]
    public = await client.get(f"/api/feedback/public/{token}")
    assert public.status_code == 200
    assert set(public.json()) == {"valid", "message", "first_name", "clinic_name", "visit_date"}
    assert public.json()["first_name"] == "Ana"
    assert "last_name" not in public.json()
    submitted = await client.post(f"/api/feedback/public/{token}", json={"rating": 2, "comment": "Așteptare lungă"})
    assert "Mulțumim" in submitted.json()["message"]
    used = await client.post(f"/api/feedback/public/{token}", json={"rating": 5})
    invalid = await client.get("/api/feedback/public/invalid-token")
    assert used.json() == invalid.json()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(PatientFeedback)) == 1
        assert await db.scalar(select(func.count()).select_from(FeedbackRequest).where(FeedbackRequest.used_at.is_not(None))) == 1


@pytest.mark.asyncio
async def test_review_invitation_is_scheduled_independently_of_rating(ctx):
    client, sessions = ctx
    headers, manager, room, patient = await setup(client, "t26-review@example.com")
    settings = await client.put("/api/feedback/settings", headers=headers, json={"delay_minutes": 0, "negative_threshold": 3, "review_enabled": True, "review_url": "https://example.test/reviews", "hourly_limit": 10})
    assert settings.status_code == 200, settings.text
    await grant(client, headers, patient["id"])
    second_patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Mihai", "last_name": "Test", "phone": "+40700000002", "email": "mihai@example.com"})).json()
    await grant(client, headers, second_patient["id"])
    appt = await book(client, headers, manager, room, patient)
    second_appt = await book(client, headers, manager, room, second_patient, days=4)
    await close_and_schedule(sessions, appt["id"])
    await close_and_schedule(sessions, second_appt["id"])
    async with sessions() as db:
        requests = (await db.scalars(select(CommOutbox).where(CommOutbox.message_type == "feedback_request"))).all()
        assert len(requests) == 2
        tokens = {str(row.patient_id): row.body.rsplit("/feedback/", 1)[-1] for row in requests}
    low = await client.post(f"/api/feedback/public/{tokens[patient['id']]}", json={"rating": 1})
    high = await client.post(f"/api/feedback/public/{tokens[second_patient['id']]}", json={"rating": 5})
    assert "Mulțumim" in low.json()["message"] and "Mulțumim" in high.json()["message"]
    async with sessions() as db:
        reviews = list((await db.scalars(select(CommOutbox).where(CommOutbox.message_type == "review_invitation"))).all())
        assert {str(row.patient_id) for row in reviews} == {patient["id"], second_patient["id"]}


@pytest.mark.asyncio
async def test_campaign_launch_is_idempotent_and_unsubscribe_does_not_revoke_appointment_consent(ctx):
    client, sessions = ctx
    headers, manager, room, patient = await setup(client, "t26-campaign@example.com")
    await grant(client, headers, patient["id"], "email")
    appt = await book(client, headers, manager, room, patient)
    await close_and_schedule(sessions, appt["id"])
    response = await client.post("/api/comms/campaigns", headers=headers, json={"name": "Control", "filter_type": "last_visit_before", "filter_date": (datetime.now(UTC) + timedelta(days=4)).date().isoformat(), "scheduled_for": datetime.now(UTC).isoformat(), "template_type": "campaign"})
    assert response.status_code == 201, response.text
    campaign_id = response.json()["id"]
    first = await client.post(f"/api/comms/campaigns/{campaign_id}/launch", headers=headers)
    again = await client.post(f"/api/comms/campaigns/{campaign_id}/launch", headers=headers)
    assert first.json()["queued"] == 1
    assert again.json()["queued"] == 0
    async with sessions() as db:
        row = await db.scalar(select(CommOutbox).where(CommOutbox.message_type == "campaign"))
        token = row.body.rsplit("/campaigns/unsubscribe/", 1)[-1]
    unsubscribed = await client.post(f"/api/comms/campaign-unsubscribe/{token}")
    assert "înregistrată" in unsubscribed.json()["message"]
    consent = await client.get(f"/api/patients/{patient['id']}/comms/consents", headers=headers)
    assert consent.json()["current"]["email"]["granted"] is True
    async with sessions() as db:
        await process_due(db, now=datetime.now(UTC) + timedelta(minutes=1))
        rows = list((await db.scalars(select(CommOutbox).where(CommOutbox.message_type == "campaign"))).all())
        assert len(rows) == 1 and rows[0].status == "cancelled" and rows[0].reason == "campaign_unsubscribed"


@pytest.mark.asyncio
async def test_preview_for_200_recipients_uses_a_bounded_number_of_queries(ctx):
    client, sessions = ctx
    headers, manager, room, patient = await setup(client, "t26-preview@example.com")
    clinic_id = manager["clinic_id"]
    base_appt = await book(client, headers, manager, room, patient)
    await close_and_schedule(sessions, base_appt["id"])
    async with sessions() as db:
        doctor = await db.get(User, manager["id"])
        room_id = __import__("uuid").UUID(room["id"])
        for index in range(199):
            recipient = Patient(clinic_id=clinic_id, first_name=f"Pacient{index}", last_name="Test", phone=f"+4071{index:07d}", email=f"patient{index}@example.com")
            db.add(recipient)
            await db.flush()
            visit = Appointment(clinic_id=clinic_id, patient_id=recipient.id, doctor_id=doctor.id, room_id=room_id, starts_at=datetime.now(UTC) - timedelta(days=2), ends_at=datetime.now(UTC) - timedelta(days=2) + timedelta(minutes=30), duration_minutes=30, status="completed", appointment_type="Control")
            db.add(visit)
        await db.commit()
        count = 0

        def counted(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal count
            if statement.lstrip().upper().startswith("SELECT"):
                count += 1

        event.listen(db.bind.sync_engine, "before_cursor_execute", counted)
        try:
            preview = await _preview(db, doctor, CampaignFilter(filter_type="no_future_appointment"))
        finally:
            event.remove(db.bind.sync_engine, "before_cursor_execute", counted)
        assert preview["count"] == 200
        assert count <= 4


@pytest.mark.asyncio
async def test_feedback_roles_and_foreign_clinic_are_scoped(ctx):
    client, sessions = ctx
    headers, manager, _room, patient = await setup(client, "t26-roles@example.com")
    other_headers, _other_manager, _other_room, _other_patient = await setup(client, "t26-foreign@example.com")
    foreign = await client.get(f"/api/comms/campaign-preferences/{patient['id']}", headers=other_headers)
    assert foreign.status_code == 404
    async with sessions() as db:
        user = await db.get(User, manager["id"])
        user.role = "reception"
        await db.commit()
    denied = await client.get("/api/feedback", headers=headers)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_campaign_hourly_limit_defers_excess_messages(ctx):
    client, sessions = ctx
    headers, manager, room, first_patient = await setup(client, "t26-limit@example.com")
    second_patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Mihai", "last_name": "Test", "email": "limit2@example.com"})).json()
    for patient in (first_patient, second_patient):
        await grant(client, headers, patient["id"], "email")
    first_visit = await book(client, headers, manager, room, first_patient, days=3)
    second_visit = await book(client, headers, manager, room, second_patient, days=4)
    await close_and_schedule(sessions, first_visit["id"])
    await close_and_schedule(sessions, second_visit["id"])
    configured = await client.put("/api/feedback/settings", headers=headers, json={"delay_minutes": 0, "negative_threshold": 3, "review_enabled": False, "review_url": None, "hourly_limit": 1})
    assert configured.status_code == 200
    campaign = await client.post("/api/comms/campaigns", headers=headers, json={"name": "Limită", "filter_type": "last_visit_before", "filter_date": (datetime.now(UTC) + timedelta(days=5)).date().isoformat(), "scheduled_for": datetime.now(UTC).isoformat(), "template_type": "campaign"})
    assert campaign.status_code == 201
    launched = await client.post(f"/api/comms/campaigns/{campaign.json()['id']}/launch", headers=headers)
    assert launched.json()["queued"] == 2
    async with sessions() as db:
        result = await process_due(db, now=datetime.now(UTC) + timedelta(minutes=1))
        messages = list((await db.scalars(select(CommOutbox).where(CommOutbox.message_type == "campaign"))).all())
        assert result["sent"] == 1 and result["deferred"] == 1
        assert sorted(row.status for row in messages) == ["pending", "sent"]
