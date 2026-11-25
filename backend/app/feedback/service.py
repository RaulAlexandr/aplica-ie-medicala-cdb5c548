from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.comms.models import CHANNELS, CommConsent, CommOutbox
from app.comms.service import defer_quiet, enqueue, utc
from app.config import settings as app_settings
from app.database import Appointment, Clinic, Patient, utcnow
from app.feedback.models import CampaignPreference, FeedbackRequest, FeedbackSettings


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_settings(db: AsyncSession, clinic_id: UUID) -> FeedbackSettings:
    row = await db.get(FeedbackSettings, clinic_id)
    return row or FeedbackSettings(clinic_id=clinic_id, delay_minutes=120, negative_threshold=3, review_enabled=False, review_url=None, hourly_limit=100)


async def _eligible_channels(db: AsyncSession, clinic_id: UUID, patient_id: UUID) -> list[str]:
    rows = (await db.scalars(select(CommConsent).where(CommConsent.clinic_id == clinic_id, CommConsent.patient_id == patient_id).order_by(CommConsent.recorded_at.desc(), CommConsent.id.desc()))).all()
    current = {}
    for row in rows:
        current.setdefault(row.channel, row)
    return [channel for channel in CHANNELS if current.get(channel) and current[channel].granted]


async def schedule_visit_messages(db: AsyncSession, appt: Appointment) -> None:
    """Queue feedback and review messages through the T15 outbox when a visit closes."""
    if appt.status != "completed":
        return
    # The visit row is the serialization point for the first close and any repeated close hook.
    await db.execute(select(Appointment.id).where(Appointment.id == appt.id, Appointment.clinic_id == appt.clinic_id).with_for_update())
    cfg = await get_settings(db, appt.clinic_id)
    clinic = await db.get(Clinic, appt.clinic_id)
    patient = await db.get(Patient, appt.patient_id)
    channels = await _eligible_channels(db, appt.clinic_id, appt.patient_id)
    channels = [channel for channel in ("email", "sms") if channel in channels and patient and (patient.email if channel == "email" else patient.phone)]
    if not channels:
        return
    # One feedback request per visit: choose one consented channel with a valid destination.
    channel = channels[0]
    req = await db.scalar(select(FeedbackRequest).where(FeedbackRequest.clinic_id == appt.clinic_id, FeedbackRequest.appointment_id == appt.id).with_for_update())
    if req is None:
        token = secrets.token_urlsafe(32)
        req = FeedbackRequest(clinic_id=appt.clinic_id, appointment_id=appt.id, patient_id=appt.patient_id, token_hash=token_digest(token), expires_at=utcnow() + timedelta(days=14))
        db.add(req)
        await db.flush()
    else:
        token = None
    zone = ZoneInfo(clinic.timezone)
    scheduled = defer_quiet(utcnow() + timedelta(minutes=cfg.delay_minutes), zone, (await _comms_settings(db, appt.clinic_id)).quiet_start, (await _comms_settings(db, appt.clinic_id)).quiet_end)
    if token:
        new = await enqueue(db, appt, "feedback_request", channel, scheduled, None)
        row = await db.scalar(select(CommOutbox).where(CommOutbox.idempotency_key == f"{appt.id}:feedback_request:{channel}:{utc(appt.starts_at).strftime('%Y%m%dT%H%M%SZ')}"))
        if row:
            public_origin = app_settings.cors_origins.split(",")[0].strip().rstrip("/")
            row.body = f"{public_origin}/feedback/{token}"
            if new:
                req.outbox_id = row.id
    if cfg.review_enabled and cfg.review_url:
        await enqueue(db, appt, "review_invitation", channel, scheduled, None)
        row = await db.scalar(select(CommOutbox).where(CommOutbox.idempotency_key == f"{appt.id}:review_invitation:{channel}:{utc(appt.starts_at).strftime('%Y%m%dT%H%M%SZ')}"))
        if row:
            row.body = cfg.review_url


async def _comms_settings(db: AsyncSession, clinic_id: UUID):
    from app.comms.service import get_settings as comm_settings
    return await comm_settings(db, clinic_id)


async def can_send_campaign(db: AsyncSession, clinic_id: UUID, patient_id: UUID) -> bool:
    preference = await db.get(CampaignPreference, (clinic_id, patient_id))
    return not preference or not preference.opted_out


async def campaign_targets(db: AsyncSession, clinic_id: UUID, filter_type: str, filter_date=None, period_start=None, period_end=None):
    """Resolve a fixed campaign audience in a bounded query; results include the appointment used by the outbox."""
    from app.database import Appointment, PerformedProcedure, Recall

    latest = select(Appointment.id.label("appt_id"), Appointment.patient_id.label("pid"), func.row_number().over(partition_by=Appointment.patient_id, order_by=(Appointment.starts_at.desc(), Appointment.id.desc())).label("rn")).where(Appointment.clinic_id == clinic_id, Appointment.status == "completed").subquery()
    query = select(Patient, Appointment).join(latest, latest.c.pid == Patient.id).join(Appointment, Appointment.id == latest.c.appt_id).where(Patient.clinic_id == clinic_id, latest.c.rn == 1)
    if filter_type == "last_visit_before":
        cutoff = datetime.combine(filter_date, time.min, UTC) if isinstance(filter_date, date) and not isinstance(filter_date, datetime) else filter_date
        query = query.where(Appointment.starts_at < cutoff)
    elif filter_type == "recall_due":
        query = query.where(Patient.id.in_(select(Recall.patient_id).where(Recall.clinic_id == clinic_id, Recall.status == "pending", Recall.due_date <= filter_date)))
    elif filter_type == "procedure_in_period":
        query = query.where(Patient.id.in_(select(PerformedProcedure.patient_id).where(PerformedProcedure.clinic_id == clinic_id, PerformedProcedure.status == "completed", PerformedProcedure.updated_at >= period_start, PerformedProcedure.updated_at < period_end)))
    elif filter_type == "no_future_appointment":
        future = select(Appointment.id).where(Appointment.clinic_id == clinic_id, Appointment.patient_id == Patient.id, Appointment.status.in_(("scheduled", "confirmed")), Appointment.starts_at > utcnow()).correlate(Patient)
        query = query.where(~future.exists())
    else:
        return []
    return list((await db.execute(query.order_by(Patient.last_name, Patient.first_name, Patient.id))).all())
