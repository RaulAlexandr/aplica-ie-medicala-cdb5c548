"""T15 patient communication endpoints: consent, templates, settings, history, queue, processing."""
from __future__ import annotations

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.comms import service
from app.comms.models import (
    CHANNELS,
    MESSAGE_TYPES,
    STATUSES,
    CommConsent,
    CommOutbox,
    CommOutboxEvent,
    CommSettings,
    CommTemplateVersion,
)
from app.database import Clinic, Patient, User, get_db

router = APIRouter(tags=["comms"])
MANAGERS = ("clinic_manager", "administrator")
ALL_STAFF = ("clinic_manager", "doctor", "assistant", "reception", "administrator")
RECORD_CONSENT = ("clinic_manager", "doctor", "reception", "administrator")
Channel = Literal["sms", "email"]
MessageType = Literal["confirmation", "reminder", "cancellation", "reschedule", "feedback_request", "review_invitation", "campaign"]
ConsentSource = Literal["in_person", "phone", "paper_form", "patient_reply", "other"]


class ConsentIn(BaseModel):
    channel: Channel
    granted: bool
    source: ConsentSource


class ConsentOut(BaseModel):
    id: UUID; channel: str; granted: bool; source: str; recorded_by_id: UUID; recorded_by: str | None = None; recorded_at: datetime


class ConsentsOut(BaseModel):
    current: dict[str, ConsentOut | None]
    history: list[ConsentOut]


class TemplateIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def only_allowed_variables(self) -> TemplateIn:
        problems = service.template_problems(self.subject, self.body)
        if problems:
            raise ValueError(f"Variabile nepermise în șablon: {', '.join(problems)}. Permise: {', '.join(service.ALLOWED_VARIABLES)}")
        return self


class TemplateOut(BaseModel):
    message_type: str; version: int; subject: str; body: str; created_at: datetime | None = None; created_by_id: UUID | None = None
    model_config = ConfigDict(from_attributes=True)


class SettingsIn(BaseModel):
    reminder_hours: int = Field(ge=1, le=336)
    quiet_start: time
    quiet_end: time
    clinic_phone: str | None = Field(default=None, max_length=40)


class SettingsOut(SettingsIn):
    allowed_variables: list[str] = list(service.ALLOWED_VARIABLES)


class EventOut(BaseModel):
    id: UUID; from_status: str | None; to_status: str; reason: str | None; occurred_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: UUID; patient_id: UUID; patient_name: str | None = None; appointment_id: UUID; message_type: str; channel: str; status: str
    scheduled_for: datetime; appointment_starts_at: datetime; attempts: int; reason: str | None; last_error: str | None
    subject: str | None; body: str | None; provider_ref: str | None; adapter: str | None; template_version: int | None
    sent_at: datetime | None; created_at: datetime; events: list[EventOut]


class ProcessOut(BaseModel):
    sent: int; failed: int; cancelled: int; retry: int; deferred: int


class DeliveryIn(BaseModel):
    delivered: bool
    reason: str | None = Field(default=None, max_length=300)


async def _patient_or_404(db: AsyncSession, user: User, patient_id: UUID) -> Patient:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    return patient


async def _messages(db: AsyncSession, rows: list[CommOutbox], names: dict[UUID, str]) -> list[MessageOut]:
    """One query for all the events of the page, so a listing never costs one query per message."""
    events: dict[UUID, list[CommOutboxEvent]] = {}
    if rows:
        found = (await db.scalars(select(CommOutboxEvent).where(CommOutboxEvent.outbox_id.in_([r.id for r in rows])).order_by(CommOutboxEvent.occurred_at, CommOutboxEvent.id))).all()
        for event in found:
            events.setdefault(event.outbox_id, []).append(event)
    return [MessageOut(**{c: getattr(r, c) for c in ("id", "patient_id", "appointment_id", "message_type", "channel", "status", "scheduled_for", "appointment_starts_at", "attempts", "reason", "last_error", "subject", "body", "provider_ref", "adapter", "template_version", "sent_at", "created_at")}, patient_name=names.get(r.patient_id), events=[EventOut.model_validate(e) for e in events.get(r.id, [])]) for r in rows]


# ------------------------------------------------------------------ consent and history (per patient)
@router.get("/patients/{patient_id}/comms/consents", response_model=ConsentsOut)
async def get_consents(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))):
    await _patient_or_404(db, user, patient_id)
    rows = (await db.execute(select(CommConsent, User.full_name).join(User, User.id == CommConsent.recorded_by_id).where(CommConsent.clinic_id == user.clinic_id, CommConsent.patient_id == patient_id).order_by(CommConsent.recorded_at.desc(), CommConsent.id.desc()))).all()
    history = [ConsentOut(id=c.id, channel=c.channel, granted=c.granted, source=c.source, recorded_by_id=c.recorded_by_id, recorded_by=name, recorded_at=c.recorded_at) for c, name in rows]
    current: dict[str, ConsentOut | None] = {channel: None for channel in CHANNELS}
    for item in reversed(history):  # oldest first, so the newest overwrites
        current[item.channel] = item
    return ConsentsOut(current=current, history=history)


@router.post("/patients/{patient_id}/comms/consents", response_model=ConsentOut, status_code=201)
async def record_consent(patient_id: UUID, payload: ConsentIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*RECORD_CONSENT))):
    await _patient_or_404(db, user, patient_id)
    row = CommConsent(clinic_id=user.clinic_id, patient_id=patient_id, channel=payload.channel, granted=payload.granted, source=payload.source, recorded_by_id=user.id)
    db.add(row)
    await db.commit()
    return ConsentOut(id=row.id, channel=row.channel, granted=row.granted, source=row.source, recorded_by_id=user.id, recorded_by=user.full_name, recorded_at=row.recorded_at)


@router.get("/patients/{patient_id}/comms/messages", response_model=list[MessageOut])
async def patient_messages(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))):
    patient = await _patient_or_404(db, user, patient_id)
    rows = list((await db.scalars(select(CommOutbox).where(CommOutbox.clinic_id == user.clinic_id, CommOutbox.patient_id == patient_id).order_by(CommOutbox.created_at.desc(), CommOutbox.id.desc()).limit(200))).all())
    return await _messages(db, rows, {patient.id: f"{patient.first_name} {patient.last_name}"})


# ------------------------------------------------------------------ templates and settings (manager)
@router.get("/comms/templates", response_model=list[TemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    rows = (await db.scalars(select(CommTemplateVersion).where(CommTemplateVersion.clinic_id == user.clinic_id).order_by(CommTemplateVersion.version))).all()
    latest = {row.message_type: row for row in rows}
    out = []
    for message_type in MESSAGE_TYPES:
        row = latest.get(message_type)
        out.append(TemplateOut.model_validate(row) if row else TemplateOut(message_type=message_type, version=0, subject=service.DEFAULTS[message_type][0], body=service.DEFAULTS[message_type][1]))
    return out


@router.put("/comms/templates/{message_type}", response_model=TemplateOut)
async def save_template(message_type: MessageType, payload: TemplateIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    row = await service.save_template(db, user.clinic_id, message_type, payload.subject, payload.body, user.id)
    await db.commit()
    return TemplateOut.model_validate(row)


@router.get("/comms/templates/{message_type}/versions", response_model=list[TemplateOut])
async def template_versions(message_type: MessageType, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    rows = (await db.scalars(select(CommTemplateVersion).where(CommTemplateVersion.clinic_id == user.clinic_id, CommTemplateVersion.message_type == message_type).order_by(CommTemplateVersion.version.desc()))).all()
    return [TemplateOut.model_validate(row) for row in rows]


@router.get("/comms/settings", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    cfg = await service.get_settings(db, user.clinic_id)
    return SettingsOut(reminder_hours=cfg.reminder_hours, quiet_start=cfg.quiet_start, quiet_end=cfg.quiet_end, clinic_phone=cfg.clinic_phone)


@router.put("/comms/settings", response_model=SettingsOut)
async def put_settings(payload: SettingsIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await db.execute(select(Clinic.id).where(Clinic.id == user.clinic_id).with_for_update())
    cfg = await db.get(CommSettings, user.clinic_id)
    if not cfg:
        cfg = CommSettings(clinic_id=user.clinic_id)
        db.add(cfg)
    cfg.reminder_hours, cfg.quiet_start, cfg.quiet_end, cfg.clinic_phone, cfg.updated_by_id = payload.reminder_hours, payload.quiet_start, payload.quiet_end, payload.clinic_phone, user.id
    await db.commit()
    return SettingsOut(**payload.model_dump())


# ------------------------------------------------------------------ queue (manager)
@router.get("/comms/outbox", response_model=list[MessageOut])
async def list_outbox(status: str | None = None, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    if status is not None and status not in STATUSES:
        raise HTTPException(422, "Unknown message status")
    query = select(CommOutbox, Patient.first_name, Patient.last_name).join(Patient, Patient.id == CommOutbox.patient_id).where(CommOutbox.clinic_id == user.clinic_id)
    if status:
        query = query.where(CommOutbox.status == status)
    found = (await db.execute(query.order_by(CommOutbox.scheduled_for.desc(), CommOutbox.id.desc()).limit(limit))).all()
    return await _messages(db, [r for r, _, _ in found], {r.patient_id: f"{first} {last}" for r, first, last in found})


@router.post("/comms/process", response_model=ProcessOut)
async def process(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    # Only this clinic's messages are sent from the manager button; the command line processes every clinic.
    return await service.process_due(db, clinic_id=user.clinic_id)


@router.post("/comms/outbox/{outbox_id}/delivery", response_model=MessageOut)
async def report_delivery(outbox_id: UUID, payload: DeliveryIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    row = await db.scalar(select(CommOutbox).where(CommOutbox.id == outbox_id, CommOutbox.clinic_id == user.clinic_id).with_for_update())
    if not row:
        raise HTTPException(404, "Message not found")
    if row.status != "sent":
        raise HTTPException(409, "Only a sent message can receive a delivery report")
    await service.record_delivery(db, row, payload.delivered, user.id, payload.reason)
    await db.commit()
    patient = await db.get(Patient, row.patient_id)
    return (await _messages(db, [row], {row.patient_id: f"{patient.first_name} {patient.last_name}"}))[0]
