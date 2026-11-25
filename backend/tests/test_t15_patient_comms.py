"""T15 patient communication v1 (simulated adapter): consent, templates, outbox rules, queue, roles, isolation."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comms import adapters, service
from app.comms.adapters import AdapterError
from app.comms.models import CommOutbox, CommOutboxEvent
from app.database import Appointment, Base, User, get_db
from app.main import app

ZONE = ZoneInfo("Europe/Bucharest")


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
            yield client, sessions, engine
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def register(client, email):
    response = await client.post("/api/auth/register", json={"clinic_name": "Clinică fictivă", "full_name": "Manager Fictiv", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def env(client, email, *, phone="+40700000001", mail="ana@example.com"):
    headers = await register(client, email)
    me = (await client.get("/api/auth/me", headers=headers)).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet fictiv"})).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Ana", "last_name": "Fictiv", "phone": phone, "email": mail})).json()
    await client.put("/api/comms/settings", headers=headers, json={"reminder_hours": 24, "quiet_start": "20:00:00", "quiet_end": "08:00:00", "clinic_phone": "0200 000 000"})
    return {"h": headers, "me": me, "room": room, "patient": patient}


def local(day: date, hour=10, minute=0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE)


def in_days(n: int) -> date:
    return (datetime.now(ZONE) + timedelta(days=n)).date()


async def book(client, e, when: datetime, duration=30):
    response = await client.post("/api/appointments", headers=e["h"], json={"patient_id": e["patient"]["id"], "doctor_id": e["me"]["id"], "room_id": e["room"]["id"], "local_start": when.replace(tzinfo=None).isoformat(), "timezone_fold": 0, "duration_minutes": duration, "appointment_type": "Control fictiv"})
    assert response.status_code == 201, response.text
    return response.json()


async def rows(sessions, appointment_id=None, **filters):
    async with sessions() as s:
        query = select(CommOutbox).order_by(CommOutbox.created_at, CommOutbox.id)
        if appointment_id:
            query = query.where(CommOutbox.appointment_id == appointment_id)
        return [r for r in (await s.scalars(query)).all() if all(getattr(r, k) == v for k, v in filters.items())]


async def consent(client, e, channel="sms", granted=True):
    r = await client.post(f"/api/patients/{e['patient']['id']}/comms/consents", headers=e["h"], json={"channel": channel, "granted": granted, "source": "in_person"})
    assert r.status_code == 201, r.text
    return r.json()


async def run(sessions, now, **kw):
    async with sessions() as s:
        return await service.process_due(s, now=now, **kw)


def utc(dt):
    return dt.astimezone(UTC)


# ------------------------------------------------------------------ confirmation, idempotency
@pytest.mark.asyncio
async def test_confirmation_and_reminder_are_queued_once_even_if_the_event_repeats(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-once@example.com")
    appt = await book(client, e, local(in_days(10)))
    async with sessions() as s:
        item = await s.get(Appointment, appt["id"])
        for _ in range(3):  # a double click / retry of the same event
            await service.sync_appointment(s, item, e["me"]["id"])
        await s.commit()
    found = await rows(sessions, appt["id"])
    assert sorted((r.message_type, r.channel) for r in found) == [("confirmation", "email"), ("confirmation", "sms"), ("reminder", "email"), ("reminder", "sms")]
    assert len({r.idempotency_key for r in found}) == 4


@pytest.mark.asyncio
async def test_double_click_create_is_rejected_by_agenda_and_queues_one_confirmation(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-double@example.com")
    when = local(in_days(11))
    first = await book(client, e, when)
    second = await client.post("/api/appointments", headers=e["h"], json={"patient_id": e["patient"]["id"], "doctor_id": e["me"]["id"], "room_id": e["room"]["id"], "local_start": when.replace(tzinfo=None).isoformat(), "timezone_fold": 0, "duration_minutes": 30, "appointment_type": "Control fictiv"})
    assert second.status_code == 409
    assert len([r for r in await rows(sessions) if r.message_type == "confirmation" and r.channel == "sms"]) == 1
    assert (await rows(sessions, first["id"]))


# ------------------------------------------------------------------ reminder time, DST, quiet window
def transition_day(start: date, spring: bool) -> date:
    day, previous = start, ZONE.utcoffset(datetime.combine(start, time(12), tzinfo=ZONE))
    for _ in range(400):
        day += timedelta(days=1)
        offset = ZONE.utcoffset(datetime.combine(day, time(12), tzinfo=ZONE))
        if offset != previous and (offset > previous) == spring:
            return day
        previous = offset
    raise AssertionError("no DST transition found")


@pytest.mark.asyncio
@pytest.mark.parametrize("spring", [True, False])
async def test_reminder_is_24h_before_in_clinic_zone_across_dst_and_text_shows_clinic_time(ctx, spring):
    client, sessions, _ = ctx
    e = await env(client, f"t15-dst-{spring}@example.com")
    await consent(client, e, "sms")
    day = transition_day(in_days(5), spring)
    start = local(day, 12)  # after the 03:00 change, so the previous day has the other UTC offset
    appt = await book(client, e, start)
    reminder = next(r for r in await rows(sessions, appt["id"]) if r.message_type == "reminder" and r.channel == "sms")
    assert utc(reminder.scheduled_for) == utc(start) - timedelta(hours=24)
    assert utc(reminder.scheduled_for).astimezone(ZONE).hour != 12  # a wall-clock "same hour" rule would give 12:00
    await run(sessions, utc(start) - timedelta(hours=23, minutes=59))  # reminder is due, appointment not yet
    sent = next(r for r in await rows(sessions, appt["id"]) if r.message_type == "reminder" and r.channel == "sms")
    assert sent.status == "sent" and f"{day.day:02d}.{day.month:02d}.{day.year}" in sent.body and "12:00" in sent.body and sent.adapter == "simulated"


def test_quiet_window_defers_never_earlier_and_handles_midnight_wrap():
    q1, q2 = time(20, 0), time(8, 0)
    at = lambda h, m=0, d=15: datetime(2031, 6, d, h, m, tzinfo=ZONE)
    assert service.defer_quiet(utc(at(12)), ZONE, q1, q2) == utc(at(12))
    assert service.defer_quiet(utc(at(20)), ZONE, q1, q2) == utc(at(8, d=16))
    assert service.defer_quiet(utc(at(23, 30)), ZONE, q1, q2) == utc(at(8, d=16))
    assert service.defer_quiet(utc(at(3)), ZONE, q1, q2) == utc(at(8))
    assert service.defer_quiet(utc(at(8)), ZONE, q1, q2) == utc(at(8))
    assert service.defer_quiet(utc(at(21)), ZONE, time(9), time(9)) == utc(at(21))  # equal bounds: no window
    assert service.defer_quiet(utc(at(12)), ZONE, time(12), time(14)) == utc(at(14))  # same-day window


@pytest.mark.asyncio
async def test_reminder_inside_quiet_window_is_postponed_not_lost(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-quiet@example.com")
    await consent(client, e, "sms")
    start = local(in_days(20), 21)  # reminder instant = 21:00 the day before: inside 20:00-08:00
    appt = await book(client, e, start)
    reminder = next(r for r in await rows(sessions, appt["id"]) if r.message_type == "reminder" and r.channel == "sms")
    assert utc(reminder.scheduled_for) == utc(local(in_days(20), 8))
    # a run inside the window postpones; the message stays pending
    inside = utc(local(in_days(19), 22))
    async with sessions() as s:
        row = await s.get(CommOutbox, reminder.id)
        row.scheduled_for = inside - timedelta(minutes=1)
        await s.commit()
    counts = await run(sessions, inside)
    after = next(r for r in await rows(sessions, appt["id"]) if r.id == reminder.id)
    assert counts["deferred"] == 2 and after.status == "pending" and utc(after.scheduled_for) == utc(local(in_days(19) + timedelta(days=1), 8))


@pytest.mark.asyncio
async def test_settings_change_reminder_lead_and_validate(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-settings@example.com")
    bad = await client.put("/api/comms/settings", headers=e["h"], json={"reminder_hours": 0, "quiet_start": "20:00:00", "quiet_end": "08:00:00"})
    assert bad.status_code == 422
    ok = await client.put("/api/comms/settings", headers=e["h"], json={"reminder_hours": 48, "quiet_start": "20:00:00", "quiet_end": "08:00:00", "clinic_phone": "0200"})
    assert ok.status_code == 200 and (await client.get("/api/comms/settings", headers=e["h"])).json()["reminder_hours"] == 48
    start = local(in_days(12), 11)
    appt = await book(client, e, start)
    reminder = next(r for r in await rows(sessions, appt["id"]) if r.message_type == "reminder")
    assert utc(reminder.scheduled_for) == utc(start) - timedelta(hours=48)


# ------------------------------------------------------------------ consent
@pytest.mark.asyncio
async def test_no_consent_sends_nothing_and_history_shows_the_reason(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-noconsent@example.com")
    appt = await book(client, e, local(in_days(8)))
    counts = await run(sessions, utc(local(in_days(8))) - timedelta(minutes=1))
    assert counts["sent"] == 0 and counts["cancelled"] == 4
    history = (await client.get(f"/api/patients/{e['patient']['id']}/comms/messages", headers=e["h"])).json()
    assert {m["status"] for m in history} == {"cancelled"} and {m["reason"] for m in history} == {"no_consent"} and all(m["body"] is None for m in history)
    assert [ev["to_status"] for ev in history[0]["events"]] == ["pending", "cancelled"] and history[0]["events"][-1]["reason"] == "no_consent"
    assert appt


@pytest.mark.asyncio
async def test_consent_is_per_channel_and_unsubscribe_is_a_new_record(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-channels@example.com")
    first = await consent(client, e, "sms", True)
    await consent(client, e, "email", True)
    withdrawn = await consent(client, e, "email", False)
    assert first["recorded_by"] == "Manager Fictiv" and first["source"] == "in_person" and first["recorded_at"]
    state = (await client.get(f"/api/patients/{e['patient']['id']}/comms/consents", headers=e["h"])).json()
    assert state["current"]["sms"]["granted"] is True and state["current"]["email"]["granted"] is False and state["current"]["email"]["id"] == withdrawn["id"]
    assert len(state["history"]) == 3  # nothing was overwritten
    start = local(in_days(9))
    appt = await book(client, e, start)
    await run(sessions, utc(start) - timedelta(minutes=1))
    by = {(r.message_type, r.channel): r for r in await rows(sessions, appt["id"])}
    assert by[("confirmation", "sms")].status == "sent" and by[("reminder", "sms")].status == "sent"
    assert by[("confirmation", "email")].status == "cancelled" and by[("confirmation", "email")].reason == "no_consent"
    assert by[("confirmation", "sms")].recipient == "+40700000001" and "Ana" in by[("confirmation", "sms")].body


@pytest.mark.asyncio
async def test_missing_phone_is_cancelled_with_reason(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-nophone@example.com", phone=None)
    await consent(client, e, "sms")
    start = local(in_days(8))
    await book(client, e, start)
    await run(sessions, utc(start) - timedelta(minutes=1))
    assert {r.reason for r in await rows(sessions, channel="sms")} == {"no_contact"}


# ------------------------------------------------------------------ cancel / reschedule / past
@pytest.mark.asyncio
async def test_cancellation_stops_pending_messages_and_sends_a_cancellation_message(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-cancel@example.com")
    await consent(client, e, "sms")
    start = local(in_days(14))
    appt = await book(client, e, start)
    cancelled = await client.patch(f"/api/appointments/{appt['id']}/status", headers=e["h"], json={"status": "cancelled"})
    assert cancelled.status_code == 200
    by = {(r.message_type, r.channel): r for r in await rows(sessions, appt["id"])}
    assert by[("reminder", "sms")].status == "cancelled" and by[("reminder", "sms")].reason == "appointment_cancelled"
    assert by[("confirmation", "sms")].status == "cancelled"
    assert by[("cancellation", "sms")].status == "pending"
    await run(sessions, utc(local(in_days(13), 12)))
    by = {(r.message_type, r.channel): r for r in await rows(sessions, appt["id"])}
    assert by[("cancellation", "sms")].status == "sent" and "anulat" in by[("cancellation", "sms")].body
    assert by[("reminder", "sms")].status == "cancelled"
    again = await client.patch(f"/api/appointments/{appt['id']}/status", headers=e["h"], json={"status": "cancelled"})
    assert again.status_code == 200 and len([r for r in await rows(sessions, appt["id"]) if r.message_type == "cancellation"]) == 2  # still one per channel


@pytest.mark.asyncio
async def test_reinstated_appointment_gets_its_reminder_back(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-reinstate@example.com")
    appt = await book(client, e, local(in_days(14)))
    await client.patch(f"/api/appointments/{appt['id']}/status", headers=e["h"], json={"status": "cancelled"})
    back = await client.patch(f"/api/appointments/{appt['id']}/status", headers=e["h"], json={"status": "scheduled"})
    assert back.status_code == 200
    by = {(r.message_type, r.channel): r.status for r in await rows(sessions, appt["id"])}
    assert by[("reminder", "sms")] == "pending" and by[("confirmation", "sms")] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["reschedule", "patch"])
async def test_reschedule_replaces_the_old_reminder_and_sends_a_reschedule_message(ctx, path):
    client, sessions, _ = ctx
    e = await env(client, f"t15-resched-{path}@example.com")
    await consent(client, e, "sms")
    old, new = local(in_days(14)), local(in_days(16), 15)
    appt = await book(client, e, old)
    body = {"local_start": new.replace(tzinfo=None).isoformat(), "timezone_fold": 0}
    response = await (client.post(f"/api/appointments/{appt['id']}/reschedule", headers=e["h"], json=body) if path == "reschedule" else client.patch(f"/api/appointments/{appt['id']}", headers=e["h"], json=body))
    assert response.status_code == 200, response.text
    found = await rows(sessions, appt["id"], channel="sms")
    old_ones = [r for r in found if utc(r.appointment_starts_at) == utc(old)]
    new_ones = {r.message_type: r for r in found if utc(r.appointment_starts_at) == utc(new)}
    assert {r.status for r in old_ones} == {"cancelled"} and {r.reason for r in old_ones} == {"rescheduled"}
    assert set(new_ones) == {"reschedule", "reminder"} and {r.status for r in new_ones.values()} == {"pending"}
    assert utc(new_ones["reminder"].scheduled_for) == utc(new) - timedelta(hours=24)
    await run(sessions, utc(old) - timedelta(minutes=1))  # the old reminder time arrives: nothing goes out for it
    assert not [r for r in await rows(sessions, appt["id"], channel="sms") if r.status == "sent" and r.message_type == "reminder"]
    again = await client.post(f"/api/appointments/{appt['id']}/reschedule", headers=e["h"], json=body)
    assert again.status_code == 200 and len(await rows(sessions, appt["id"], channel="sms")) == len(found)  # same time: nothing new


@pytest.mark.asyncio
async def test_moving_only_the_room_or_duration_sends_nothing_new(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-samtime@example.com")
    appt = await book(client, e, local(in_days(14)))
    before = len(await rows(sessions, appt["id"]))
    r = await client.patch(f"/api/appointments/{appt['id']}", headers=e["h"], json={"duration_minutes": 45})
    assert r.status_code == 200 and len(await rows(sessions, appt["id"])) == before


@pytest.mark.asyncio
async def test_past_appointment_gets_no_messages(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-past@example.com")
    appt = await book(client, e, local(in_days(-3)))
    assert await rows(sessions, appt["id"]) == []


@pytest.mark.asyncio
async def test_a_pending_reminder_is_dropped_when_the_appointment_has_started(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-expired@example.com")
    await consent(client, e, "sms")
    start = local(in_days(8))
    appt = await book(client, e, start)
    await run(sessions, utc(start) + timedelta(minutes=5))
    assert {r.reason for r in await rows(sessions, appt["id"], channel="sms")} == {"expired"}


@pytest.mark.asyncio
async def test_waitlist_confirmation_queues_messages(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-waitlist@example.com")
    slot = in_days(10)
    entry = (await client.post("/api/waitlist/entries", headers=e["h"], json={"patient_id": e["patient"]["id"], "desired_service": "Control fictiv", "desired_duration_minutes": 30, "windows": [{"weekday": slot.weekday(), "start": "10:00:00", "end": "12:00:00"}], "valid_from": slot.isoformat(), "valid_until": slot.isoformat(), "priority": 4})).json()
    params = {"doctor_id": e["me"]["id"], "room_id": e["room"]["id"], "starts_at": local(slot, 10, 30).isoformat(), "duration_minutes": 30}
    offer = await client.post(f"/api/waitlist/entries/{entry['id']}/offers", headers=e["h"], json=params)
    assert offer.status_code == 201, offer.text
    confirmed = await client.post(f"/api/waitlist/offers/{offer.json()['id']}/confirm", headers=e["h"])
    assert confirmed.status_code == 201, confirmed.text
    assert {(r.message_type, r.channel) for r in await rows(sessions, confirmed.json()["id"])} >= {("confirmation", "sms"), ("confirmation", "email")}


# ------------------------------------------------------------------ templates
@pytest.mark.asyncio
async def test_templates_are_versioned_validated_and_used_in_the_message(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-templates@example.com")
    default = {t["message_type"]: t for t in (await client.get("/api/comms/templates", headers=e["h"])).json()}
    assert set(default) == {"confirmation", "reminder", "cancellation", "reschedule"} and default["reminder"]["version"] == 0
    for bad in ("Salut {diagnostic}", "Salut {prenume", "Alergie {alergii}", "x}"):
        r = await client.put("/api/comms/templates/reminder", headers=e["h"], json={"subject": "Reminder", "body": bad})
        assert r.status_code == 422, bad
    assert (await client.put("/api/comms/templates/reminder", headers=e["h"], json={"subject": "{medic}?", "body": "ok"})).status_code == 200  # allowed variable in subject
    v1 = await client.put("/api/comms/templates/confirmation", headers=e["h"], json={"subject": "Confirmare {clinica}", "body": "Salut {prenume}, {data} la {ora}, {medic}, {clinica}, {telefon_clinica}"})
    v2 = await client.put("/api/comms/templates/confirmation", headers=e["h"], json={"subject": "Confirmare {clinica}", "body": "Hei {prenume}, ne vedem pe {data} la {ora}"})
    assert v1.json()["version"] == 1 and v2.json()["version"] == 2
    versions = (await client.get("/api/comms/templates/confirmation/versions", headers=e["h"])).json()
    assert [v["version"] for v in versions] == [2, 1] and "{telefon_clinica}" in versions[1]["body"]  # v1 kept as written
    await consent(client, e, "sms")
    start = local(in_days(7), 14, 30)
    appt = await book(client, e, start)
    await run(sessions, utc(start) - timedelta(minutes=1))
    sent = next(r for r in await rows(sessions, appt["id"], channel="sms") if r.message_type == "confirmation")
    assert sent.template_version == 2 and sent.body == f"Hei Ana, ne vedem pe {start.day:02d}.{start.month:02d}.{start.year} la 14:30"
    assert "{" not in sent.body and "diagnostic" not in sent.body


# ------------------------------------------------------------------ retry / failure / delivery
class Failing:
    name = "failing"

    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, 0

    async def send(self, channel, recipient, subject, body):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise AdapterError("provider indisponibil")
        return adapters.SendResult(provider_ref="ok-1")


@pytest.mark.asyncio
async def test_failure_retries_with_a_limit_then_ends_failed_with_the_reason(ctx, monkeypatch):
    client, sessions, _ = ctx
    e = await env(client, "t15-fail@example.com")
    await consent(client, e, "sms")
    start = local(in_days(8))
    appt = await book(client, e, start)
    fake = Failing(fail_times=99)
    monkeypatch.setattr(adapters, "get_adapter", lambda: fake)
    base = utc(start) - timedelta(hours=1)
    results = [await run(sessions, base + timedelta(minutes=20 * i)) for i in range(5)]
    row = next(r for r in await rows(sessions, appt["id"], channel="sms") if r.message_type == "confirmation")
    assert row.status == "failed" and row.attempts == 3 and row.reason == "attempts_exhausted" and "provider indisponibil" in row.last_error
    assert fake.calls == 6  # confirmation and reminder: 3 attempts each, then neither is tried again
    assert sum(r["retry"] for r in results) == 4 and sum(r["failed"] for r in results) == 2
    async with sessions() as s:
        events = (await s.scalars(select(CommOutboxEvent).where(CommOutboxEvent.outbox_id == row.id).order_by(CommOutboxEvent.occurred_at, CommOutboxEvent.id))).all()
    assert [(x.from_status, x.to_status) for x in events] == [(None, "pending"), ("pending", "pending"), ("pending", "pending"), ("pending", "failed")]
    assert events[1].reason.startswith("retry 1/3") and events[-1].reason.startswith("attempts_exhausted")


@pytest.mark.asyncio
async def test_retry_that_succeeds_is_sent_once(ctx, monkeypatch):
    client, sessions, _ = ctx
    e = await env(client, "t15-retryok@example.com")
    await consent(client, e, "sms")
    start = local(in_days(8))
    appt = await book(client, e, start)
    fake = Failing(fail_times=1)
    monkeypatch.setattr(adapters, "get_adapter", lambda: fake)
    base = utc(start) - timedelta(hours=1)
    await run(sessions, base)
    await run(sessions, base + timedelta(minutes=20))
    await run(sessions, base + timedelta(minutes=40))
    sent = [r for r in await rows(sessions, appt["id"], channel="sms") if r.status == "sent"]
    assert len(sent) == 2 and sorted(r.attempts for r in sent) == [1, 2] and fake.calls == 3
    assert (await run(sessions, base + timedelta(hours=1)))["sent"] == 0


@pytest.mark.asyncio
async def test_unknown_adapter_name_is_refused(monkeypatch):
    monkeypatch.setattr("app.comms.adapters.settings.comms_adapter", "nu-exista")
    with pytest.raises(RuntimeError):
        adapters.get_adapter()
    monkeypatch.setattr("app.comms.adapters.settings.comms_adapter", "simulated")
    assert adapters.get_adapter().name == "simulated"


@pytest.mark.asyncio
async def test_delivery_report_moves_sent_to_delivered_and_keeps_every_transition(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-delivery@example.com")
    await consent(client, e, "sms")
    start = local(in_days(8))
    appt = await book(client, e, start)
    pending = next(r for r in await rows(sessions, appt["id"], channel="sms") if r.message_type == "confirmation")
    assert (await client.post(f"/api/comms/outbox/{pending.id}/delivery", headers=e["h"], json={"delivered": True})).status_code == 409
    await run(sessions, utc(start) - timedelta(minutes=1))
    done = await client.post(f"/api/comms/outbox/{pending.id}/delivery", headers=e["h"], json={"delivered": True})
    assert done.status_code == 200 and done.json()["status"] == "delivered"
    assert [x["to_status"] for x in done.json()["events"]] == ["pending", "sent", "delivered"]


# ------------------------------------------------------------------ queue, process endpoint, isolation, roles
@pytest.mark.asyncio
async def test_manager_queue_filters_by_status_and_process_endpoint_sends_due_messages(ctx):
    client, _sessions, _ = ctx
    e = await env(client, "t15-queue@example.com")
    await client.put("/api/comms/settings", headers=e["h"], json={"reminder_hours": 24, "quiet_start": "00:00:00", "quiet_end": "00:00:00", "clinic_phone": None})  # equal bounds: no quiet window
    await consent(client, e, "sms")
    appt = await book(client, e, local(in_days(6)))
    pending = (await client.get("/api/comms/outbox?status=pending", headers=e["h"])).json()
    assert len(pending) == 4 and pending[0]["patient_name"] == "Ana Fictiv"
    assert (await client.get("/api/comms/outbox?status=bogus", headers=e["h"])).status_code == 422
    done = await client.post("/api/comms/process", headers=e["h"])
    assert done.status_code == 200 and done.json()["sent"] == 1 and done.json()["cancelled"] == 1  # confirmation: sms sent, email without consent
    sent = (await client.get("/api/comms/outbox?status=sent", headers=e["h"])).json()
    assert [m["message_type"] for m in sent] == ["confirmation"] and sent[0]["provider_ref"].startswith("sim-")
    assert appt and len((await client.get("/api/comms/outbox?status=cancelled", headers=e["h"])).json()) == 1


@pytest.mark.asyncio
async def test_other_clinic_gets_404_and_never_sees_or_sends_foreign_messages(ctx):
    client, sessions, _ = ctx
    a = await env(client, "t15-a@example.com")
    b = await env(client, "t15-b@example.com")
    await consent(client, a, "sms")
    start = local(in_days(8))
    appt = await book(client, a, start)
    pid = a["patient"]["id"]
    for method, url, body in (("get", f"/api/patients/{pid}/comms/consents", None), ("get", f"/api/patients/{pid}/comms/messages", None), ("post", f"/api/patients/{pid}/comms/consents", {"channel": "sms", "granted": True, "source": "phone"})):
        r = await getattr(client, method)(url, headers=b["h"], **({"json": body} if body else {}))
        assert r.status_code == 404, url
    mine = await rows(sessions, appt["id"], channel="sms")
    assert (await client.post(f"/api/comms/outbox/{mine[0].id}/delivery", headers=b["h"], json={"delivered": True})).status_code == 404
    assert (await client.get("/api/comms/outbox", headers=b["h"])).json() == []
    assert (await client.post("/api/comms/process", headers=b["h"])).json()["sent"] == 0
    assert all(r.status == "pending" for r in await rows(sessions, appt["id"], channel="sms"))


@pytest.mark.asyncio
async def test_roles(ctx):
    client, sessions, _ = ctx
    e = await env(client, "t15-roles@example.com")
    await consent(client, e, "sms")
    pid, start = e["patient"]["id"], local(in_days(8))
    await book(client, e, start)
    template = {"subject": "S {clinica}", "body": "B {prenume}"}

    async def as_role(role):
        async with sessions() as s:
            (await s.get(User, e["me"]["id"])).role = role
            await s.commit()

    expect = {  # role -> (templates, settings, queue, process, history, record consent)
        "reception": (403, 403, 403, 403, 200, 201), "doctor": (403, 403, 403, 403, 200, 201),
        "assistant": (403, 403, 403, 403, 200, 403), "clinic_manager": (200, 200, 200, 200, 200, 201), "administrator": (200, 200, 200, 200, 200, 201)}
    for role, codes in expect.items():
        await as_role(role)
        got = (
            (await client.put("/api/comms/templates/reminder", headers=e["h"], json=template)).status_code,
            (await client.put("/api/comms/settings", headers=e["h"], json={"reminder_hours": 24, "quiet_start": "20:00:00", "quiet_end": "08:00:00"})).status_code,
            (await client.get("/api/comms/outbox", headers=e["h"])).status_code,
            (await client.post("/api/comms/process", headers=e["h"])).status_code,
            (await client.get(f"/api/patients/{pid}/comms/messages", headers=e["h"])).status_code,
            (await client.post(f"/api/patients/{pid}/comms/consents", headers=e["h"], json={"channel": "email", "granted": False, "source": "phone"})).status_code,
        )
        assert got == codes, role
        if role == "reception":
            assert (await client.get("/api/comms/templates", headers=e["h"])).status_code == 403


@pytest.mark.asyncio
async def test_listing_query_count_does_not_grow_with_messages(ctx):
    client, sessions, engine = ctx
    e = await env(client, "t15-n1@example.com")
    await consent(client, e, "sms")
    counts = []
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    for batch in (1, 4):
        for i in range(batch):
            await book(client, e, local(in_days(20 + len(counts) * 10 + i), 9 + i % 3))
        statements.clear()
        for url in (f"/api/patients/{e['patient']['id']}/comms/messages", "/api/comms/outbox"):
            assert (await client.get(url, headers=e["h"])).status_code == 200
        counts.append(len(statements))
    assert counts[0] == counts[1], counts
    async with sessions() as s:
        assert (await s.scalar(select(func.count(CommOutbox.id)))) >= 20
