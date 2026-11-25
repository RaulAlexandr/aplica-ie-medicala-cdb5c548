"""T15 patient communication: templates, consent, outbox scheduling and processing (no real provider)."""
from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.comms import adapters
from app.comms.models import (
    CHANNELS,
    CommConsent,
    CommOutbox,
    CommOutboxEvent,
    CommSettings,
    CommTemplateVersion,
)
from app.config import settings as app_settings
from app.database import Appointment, Clinic, Patient, User, utcnow

ALLOWED_VARIABLES = ("prenume", "data", "ora", "medic", "clinica", "telefon_clinica", "link")
ACTIVE_FOR_MESSAGES = {"scheduled", "confirmed"}
CLOSING_STATUSES = {"arrived", "in_progress", "completed", "no_show"}
# A cancelled message with one of these reasons comes back if the appointment becomes active again unchanged.
REVIVABLE_REASONS = {"appointment_cancelled", "appointment_closed"}
PENDING_TYPES = ("confirmation", "reminder", "reschedule")
DEFAULTS: dict[str, tuple[str, str]] = {
    "confirmation": ("Programare confirmată – {clinica}", "Bună, {prenume}! Programarea ta la {clinica} este confirmată pentru {data}, ora {ora}, cu {medic}. Telefon clinică: {telefon_clinica}."),
    "reminder": ("Reminder programare – {clinica}", "Bună, {prenume}! Te așteptăm la {clinica} pe {data}, la ora {ora}, cu {medic}. Dacă nu poți ajunge, sună-ne la {telefon_clinica}."),
    "cancellation": ("Programare anulată – {clinica}", "Bună, {prenume}! Programarea ta din {data}, ora {ora}, la {clinica} a fost anulată. Pentru o nouă programare sună la {telefon_clinica}."),
    "reschedule": ("Programare mutată – {clinica}", "Bună, {prenume}! Programarea ta la {clinica} a fost mutată pe {data}, la ora {ora}, cu {medic}. Telefon clinică: {telefon_clinica}."),
    "feedback_request": ("Cum a fost vizita ta la {clinica}?", "Bună, {prenume}! Te rugăm să ne spui cum a fost vizita ta: {link}"),
    "review_invitation": ("Lasă-ne o recenzie", "Bună, {prenume}! Dacă dorești, poți lăsa o recenzie aici: {link}"),
    "campaign": ("Noutăți de la {clinica}", "Bună, {prenume}! {data} Pentru dezabonare: {link}"),
}
_TOKEN = re.compile(r"\{([^{}]*)\}")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# ---------------------------------------------------------------- templates
def template_problems(*texts: str) -> list[str]:
    """Unknown variables and unpaired braces; empty when the text only uses the allowed list."""
    problems: list[str] = []
    for text in texts:
        for name in _TOKEN.findall(text):
            if name not in ALLOWED_VARIABLES and name not in problems:
                problems.append(name)
        if "{" in _TOKEN.sub("", text) or "}" in _TOKEN.sub("", text):
            problems.append("acoladă nepereche")
    return problems


def render(text: str, values: dict[str, str]) -> str:
    return _TOKEN.sub(lambda m: values.get(m.group(1), ""), text)


async def current_template(db: AsyncSession, clinic_id: UUID, message_type: str) -> tuple[int, str, str]:
    row = await db.scalar(select(CommTemplateVersion).where(CommTemplateVersion.clinic_id == clinic_id, CommTemplateVersion.message_type == message_type).order_by(CommTemplateVersion.version.desc()).limit(1))
    if row:
        return row.version, row.subject, row.body
    return (0, *DEFAULTS[message_type])


async def save_template(db: AsyncSession, clinic_id: UUID, message_type: str, subject: str, body: str, actor_id: UUID) -> CommTemplateVersion:
    # Serialise concurrent editors on the clinic row, then take the next version number.
    await db.execute(select(Clinic.id).where(Clinic.id == clinic_id).with_for_update())
    latest = await db.scalar(select(func.max(CommTemplateVersion.version)).where(CommTemplateVersion.clinic_id == clinic_id, CommTemplateVersion.message_type == message_type))
    row = CommTemplateVersion(clinic_id=clinic_id, message_type=message_type, version=(latest or 0) + 1, subject=subject, body=body, created_by_id=actor_id)
    db.add(row)
    await db.flush()
    return row


# ---------------------------------------------------------------- settings, consent
async def get_settings(db: AsyncSession, clinic_id: UUID) -> CommSettings:
    row = await db.get(CommSettings, clinic_id)
    if row:
        return row
    return CommSettings(clinic_id=clinic_id, reminder_hours=24, quiet_start=time(20, 0), quiet_end=time(8, 0), clinic_phone=None)


async def current_consents(db: AsyncSession, clinic_id: UUID, patient_id: UUID) -> dict[str, CommConsent]:
    rows = (await db.scalars(select(CommConsent).where(CommConsent.clinic_id == clinic_id, CommConsent.patient_id == patient_id).order_by(CommConsent.recorded_at, CommConsent.id))).all()
    return {row.channel: row for row in rows}  # the newest row wins


def defer_quiet(when: datetime, zone: ZoneInfo, quiet_start: time, quiet_end: time) -> datetime:
    """Move an instant out of the clinic-local quiet window to the end of the window; never earlier."""
    if quiet_start == quiet_end:
        return when
    local = when.astimezone(zone)
    clock = local.time().replace(tzinfo=None)
    if quiet_start < quiet_end:
        inside, end_day = quiet_start <= clock < quiet_end, local.date()
    else:
        inside = clock >= quiet_start or clock < quiet_end
        end_day = local.date() + timedelta(days=1) if clock >= quiet_start else local.date()
    if not inside:
        return when
    return datetime.combine(end_day, quiet_end, tzinfo=zone).astimezone(UTC)


# ---------------------------------------------------------------- outbox
def idempotency_key(appointment_id: UUID, message_type: str, channel: str, starts_at: datetime) -> str:
    return f"{appointment_id}:{message_type}:{channel}:{utc(starts_at).strftime('%Y%m%dT%H%M%SZ')}"


def _event(db: AsyncSession, row_id: UUID, clinic_id: UUID, previous: str | None, new: str, reason: str | None, actor_id: UUID | None) -> None:
    db.add(CommOutboxEvent(clinic_id=clinic_id, outbox_id=row_id, from_status=previous, to_status=new, reason=reason, actor_id=actor_id))


async def enqueue(db: AsyncSession, appt: Appointment, message_type: str, channel: str, scheduled_for: datetime, actor_id: UUID | None) -> bool:
    """Idempotent insert keyed on (appointment, type, channel, start). True when a new/revived message was queued."""
    starts = utc(appt.starts_at)
    key = idempotency_key(appt.id, message_type, channel, starts)
    row_id, now = uuid4(), utcnow()
    values = {"id": row_id, "clinic_id": appt.clinic_id, "patient_id": appt.patient_id, "appointment_id": appt.id, "message_type": message_type, "channel": channel, "appointment_starts_at": starts, "idempotency_key": key, "status": "pending", "scheduled_for": scheduled_for, "attempts": 0, "created_at": now, "updated_at": now}
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    result = await db.execute(insert(CommOutbox).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"]))
    if result.rowcount == 1:
        _event(db, row_id, appt.clinic_id, None, "pending", "queued", actor_id)
        return True
    existing = await db.scalar(select(CommOutbox).where(CommOutbox.idempotency_key == key).with_for_update())
    if existing and existing.status == "cancelled" and existing.reason in REVIVABLE_REASONS:
        existing.status, existing.reason, existing.scheduled_for, existing.attempts = "pending", None, scheduled_for, 0
        _event(db, existing.id, appt.clinic_id, "cancelled", "pending", "revived", actor_id)
        return True
    return False


async def _cancel_pending(db: AsyncSession, appt: Appointment, reason: str, actor_id: UUID | None, keep_start: datetime | None = None) -> int:
    rows = (await db.scalars(select(CommOutbox).where(CommOutbox.appointment_id == appt.id, CommOutbox.clinic_id == appt.clinic_id, CommOutbox.status == "pending", CommOutbox.message_type.in_(PENDING_TYPES)).with_for_update())).all()
    count = 0
    for row in rows:
        if keep_start is not None and utc(row.appointment_starts_at) == keep_start:
            continue
        row.status, row.reason = "cancelled", reason
        _event(db, row.id, row.clinic_id, "pending", "cancelled", reason, actor_id)
        count += 1
    return count


async def _schedule(db: AsyncSession, appt: Appointment, lead_type: str, actor_id: UUID | None) -> None:
    """Queue the lead message (confirmation or reschedule) and the reminder for an active, future appointment."""
    if appt.status not in ACTIVE_FOR_MESSAGES:
        return
    clinic = await db.get(Clinic, appt.clinic_id)
    zone, cfg, now, starts = ZoneInfo(clinic.timezone), await get_settings(db, appt.clinic_id), utcnow(), utc(appt.starts_at)
    if starts <= now:
        return  # past appointments get nothing
    reminder_at = starts - timedelta(hours=cfg.reminder_hours)
    for channel in CHANNELS:
        await enqueue(db, appt, lead_type, channel, defer_quiet(now, zone, cfg.quiet_start, cfg.quiet_end), actor_id)
        if reminder_at > now:  # a reminder time already gone is skipped, not sent late next to the confirmation
            await enqueue(db, appt, "reminder", channel, defer_quiet(reminder_at, zone, cfg.quiet_start, cfg.quiet_end), actor_id)


async def sync_appointment(db: AsyncSession, appt: Appointment, actor_id: UUID | None, previous: dict[str, Any] | None = None) -> None:
    """Update the outbox for a create (previous None), cancel, reinstate, close or time change. Runs in the caller's
    transaction (before its commit) and is idempotent, so repeating it queues nothing twice."""
    if previous is None:
        await _schedule(db, appt, "confirmation", actor_id)
        return
    old_status, old_start = previous.get("status"), utc(datetime.fromisoformat(previous["starts_at"]))
    new_start = utc(appt.starts_at)
    if appt.status != old_status:
        if appt.status == "cancelled":
            await _cancel_pending(db, appt, "appointment_cancelled", actor_id)
            if new_start > utcnow():
                clinic = await db.get(Clinic, appt.clinic_id)
                zone, cfg = ZoneInfo(clinic.timezone), await get_settings(db, appt.clinic_id)
                for channel in CHANNELS:
                    await enqueue(db, appt, "cancellation", channel, defer_quiet(utcnow(), zone, cfg.quiet_start, cfg.quiet_end), actor_id)
            return
        if appt.status in CLOSING_STATUSES:
            await _cancel_pending(db, appt, "appointment_closed", actor_id)
            if appt.status == "completed":
                from app.feedback.service import schedule_visit_messages
                await schedule_visit_messages(db, appt)
            return
        if old_status in {"cancelled", "no_show"} and appt.status in ACTIVE_FOR_MESSAGES:
            await _schedule(db, appt, "confirmation", actor_id)
            return
    if new_start != old_start and appt.status in ACTIVE_FOR_MESSAGES:
        await _cancel_pending(db, appt, "rescheduled", actor_id, keep_start=new_start)
        await _schedule(db, appt, "reschedule", actor_id)


async def record_transition(db: AsyncSession, row: CommOutbox, new: str, reason: str | None, actor_id: UUID | None = None) -> None:
    previous = row.status
    row.status = new
    _event(db, row.id, row.clinic_id, previous, new, reason, actor_id)


async def _cancel_row(db: AsyncSession, row: CommOutbox, reason: str) -> str:
    row.reason = reason
    await record_transition(db, row, "cancelled", reason)
    return "cancelled"


async def _process_row(db: AsyncSession, row: CommOutbox, now: datetime) -> str:
    appt = await db.get(Appointment, row.appointment_id)
    patient = await db.get(Patient, row.patient_id)
    if row.message_type in PENDING_TYPES:
        if not appt or appt.status not in ACTIVE_FOR_MESSAGES:
            return await _cancel_row(db, row, "appointment_inactive")
        if utc(appt.starts_at) != utc(row.appointment_starts_at):
            return await _cancel_row(db, row, "superseded")
        if utc(appt.starts_at) <= now:
            return await _cancel_row(db, row, "expired")
    if row.message_type == "feedback_request":
        from app.feedback.models import FeedbackRequest
        request = await db.scalar(select(FeedbackRequest).where(FeedbackRequest.outbox_id == row.id))
        if not request or request.used_at or utc(request.expires_at) <= now:
            return await _cancel_row(db, row, "feedback_link_expired")
    if row.message_type == "campaign":
        from app.feedback.service import can_send_campaign
        if not await can_send_campaign(db, row.clinic_id, row.patient_id):
            return await _cancel_row(db, row, "campaign_unsubscribed")
    consent = (await current_consents(db, row.clinic_id, row.patient_id)).get(row.channel)
    if not consent or not consent.granted:
        return await _cancel_row(db, row, "no_consent")
    recipient = (patient.phone if row.channel == "sms" else patient.email) if patient else None
    if not recipient:
        return await _cancel_row(db, row, "no_contact")
    clinic, cfg = await db.get(Clinic, row.clinic_id), await get_settings(db, row.clinic_id)
    if row.message_type == "campaign":
        from app.feedback.service import get_settings as feedback_settings
        await db.execute(select(Clinic.id).where(Clinic.id == row.clinic_id).with_for_update())
        limit = (await feedback_settings(db, row.clinic_id)).hourly_limit
        sent_last_hour = await db.scalar(select(func.count()).select_from(CommOutbox).where(CommOutbox.clinic_id == row.clinic_id, CommOutbox.message_type == "campaign", CommOutbox.status.in_(("sent", "delivered")), CommOutbox.sent_at >= now - timedelta(hours=1)))
        if sent_last_hour >= limit:
            row.scheduled_for = now + timedelta(minutes=1)
            _event(db, row.id, row.clinic_id, "pending", "pending", "hourly_limit", None)
            return "deferred"
    zone = ZoneInfo(clinic.timezone)
    later = defer_quiet(now, zone, cfg.quiet_start, cfg.quiet_end)
    if later > now:  # still inside the quiet window (late run or changed settings): postpone, never drop
        row.scheduled_for = later
        _event(db, row.id, row.clinic_id, "pending", "pending", "quiet_window", None)
        return "deferred"
    version, subject_tpl, body_tpl = await current_template(db, row.clinic_id, row.message_type)
    doctor = await db.get(User, appt.doctor_id) if appt else None
    local = utc(row.appointment_starts_at).astimezone(zone)
    values = {"prenume": patient.first_name, "data": local.strftime("%d.%m.%Y"), "ora": local.strftime("%H:%M"), "medic": doctor.full_name if doctor else "", "clinica": clinic.name, "telefon_clinica": cfg.clinic_phone or "", "link": row.body or ""}
    subject, body = render(subject_tpl, values), render(body_tpl, values)
    adapter = adapters.get_adapter()
    row.attempts += 1
    try:
        result = await adapter.send(row.channel, recipient, subject, body)
    except Exception as exc:  # noqa: BLE001 - any provider failure counts as an attempt
        row.last_error = str(exc)[:300] or exc.__class__.__name__
        if row.attempts >= app_settings.comms_max_attempts:
            row.reason = "attempts_exhausted"
            await record_transition(db, row, "failed", f"attempts_exhausted {row.attempts}/{app_settings.comms_max_attempts}: {row.last_error}")
            return "failed"
        row.scheduled_for = now + timedelta(minutes=app_settings.comms_retry_minutes)
        _event(db, row.id, row.clinic_id, "pending", "pending", f"retry {row.attempts}/{app_settings.comms_max_attempts}: {row.last_error}", None)
        return "retry"
    row.recipient, row.subject, row.body, row.template_version = recipient, subject, body, version
    row.provider_ref, row.adapter, row.sent_at, row.last_error = result.provider_ref, adapter.name, now, None
    await record_transition(db, row, "sent", None)
    return "sent"


async def process_due(db: AsyncSession, now: datetime | None = None, limit: int = 200, clinic_id: UUID | None = None) -> dict[str, int]:
    """Send the messages that are due. One row per transaction, taken with SKIP LOCKED, so two runs at once never
    take the same message."""
    now = now or utcnow()
    counts = {"sent": 0, "failed": 0, "cancelled": 0, "retry": 0, "deferred": 0}
    for _ in range(limit):
        query = select(CommOutbox).where(CommOutbox.status == "pending", CommOutbox.scheduled_for <= now)
        if clinic_id is not None:
            query = query.where(CommOutbox.clinic_id == clinic_id)
        row = await db.scalar(query.order_by(CommOutbox.scheduled_for, CommOutbox.id).limit(1).with_for_update(skip_locked=True))
        if not row:
            break
        result = await _process_row(db, row, now)
        counts[result] += 1
        if row.campaign_id and result in {"sent", "failed", "cancelled"}:
            from app.feedback.models import Campaign
            campaign = await db.get(Campaign, row.campaign_id)
            pending = await db.scalar(select(func.count()).select_from(CommOutbox).where(CommOutbox.campaign_id == row.campaign_id, CommOutbox.status == "pending"))
            sent = await db.scalar(select(func.count()).select_from(CommOutbox).where(CommOutbox.campaign_id == row.campaign_id, CommOutbox.status.in_(("sent", "delivered"))))
            failed = await db.scalar(select(func.count()).select_from(CommOutbox).where(CommOutbox.campaign_id == row.campaign_id, CommOutbox.status == "failed"))
            if campaign:
                campaign.status = "sending" if pending and sent else "scheduled" if pending else "failed" if failed else "sent"
        await db.commit()
    return counts


async def record_delivery(db: AsyncSession, row: CommOutbox, delivered: bool, actor_id: UUID | None, reason: str | None = None) -> None:
    await record_transition(db, row, "delivered" if delivered else "failed", None if delivered else (reason or "delivery_failed"), actor_id)
    if not delivered:
        row.reason = "delivery_failed"
