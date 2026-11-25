import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.collab.models import CollabTask, CollabTaskEvent
from app.comms.models import CommConsent, CommOutbox
from app.comms.service import utc
from app.config import settings as app_settings
from app.database import Appointment, Clinic, Patient, User, get_db, utcnow
from app.feedback.models import (
    Campaign,
    CampaignPreference,
    CampaignUnsubscribeToken,
    FeedbackRequest,
    FeedbackSettings,
    FeedbackTaskLink,
    PatientFeedback,
)
from app.feedback.service import campaign_targets, get_settings, token_digest

router = APIRouter(tags=["feedback"])
MANAGERS = ("clinic_manager", "administrator")
DOCTORS = ("doctor",)
FeedbackFilter = Literal["last_visit_before", "recall_due", "procedure_in_period", "no_future_appointment"]


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class PublicFeedback(BaseModel):
    valid: bool
    message: str
    first_name: str | None = None
    clinic_name: str | None = None
    visit_date: date | None = None


class SettingsIn(BaseModel):
    delay_minutes: int = Field(ge=0, le=10080)
    negative_threshold: int = Field(ge=1, le=5)
    review_enabled: bool
    review_url: HttpUrl | None = None
    hourly_limit: int = Field(ge=1, le=10000)


class CampaignFilter(BaseModel):
    filter_type: FeedbackFilter
    filter_date: date | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


class CampaignIn(CampaignFilter):
    name: str = Field(min_length=1, max_length=160)
    template_type: Literal["campaign"] = "campaign"
    scheduled_for: datetime


def _public_neutral() -> PublicFeedback:
    return PublicFeedback(valid=False, message="Linkul nu este disponibil. Dacă ai trimis deja răspunsul, îți mulțumim.")


async def _request(db: AsyncSession, token: str, lock: bool = False):
    stmt = select(FeedbackRequest).where(FeedbackRequest.token_hash == token_digest(token))
    req = await db.scalar(stmt.with_for_update() if lock else stmt)
    if not req or req.used_at or utc(req.expires_at) <= utcnow():
        return None
    return req


@router.get("/feedback/public/{token}", response_model=PublicFeedback)
async def public_feedback(token: str, db: AsyncSession = Depends(get_db)):
    req = await _request(db, token)
    if not req:
        return _public_neutral()
    patient = await db.get(Patient, req.patient_id)
    clinic = await db.get(Clinic, req.clinic_id)
    appt = await db.get(Appointment, req.appointment_id)
    if not patient or not clinic or not appt or patient.clinic_id != req.clinic_id:
        return _public_neutral()
    return PublicFeedback(valid=True, message="Cum a fost vizita ta?", first_name=patient.first_name, clinic_name=clinic.name, visit_date=utc(appt.starts_at).astimezone(__import__("zoneinfo").ZoneInfo(clinic.timezone)).date())


@router.post("/feedback/public/{token}", response_model=PublicFeedback)
async def submit_public_feedback(token: str, payload: FeedbackIn, db: AsyncSession = Depends(get_db)):
    req = await _request(db, token, lock=True)
    if not req:
        return _public_neutral()
    appt = await db.scalar(select(Appointment).where(Appointment.id == req.appointment_id, Appointment.clinic_id == req.clinic_id))
    cfg = await get_settings(db, req.clinic_id)
    if not appt:
        return _public_neutral()
    comment = payload.comment.strip() if payload.comment else None
    row = PatientFeedback(clinic_id=req.clinic_id, request_id=req.id, appointment_id=req.appointment_id, patient_id=req.patient_id, doctor_id=appt.doctor_id, rating=payload.rating, comment=comment or None)
    req.used_at = utcnow()
    db.add(row)
    await db.flush()
    if payload.rating <= cfg.negative_threshold:
        manager = await db.scalar(select(User).where(User.clinic_id == req.clinic_id, User.role.in_(MANAGERS), User.is_active.is_(True)).order_by(User.created_at, User.id).limit(1))
        if manager:
            task = CollabTask(clinic_id=req.clinic_id, title="Urmărire feedback după vizită", description=f"Feedback cu nota {payload.rating}. Vizita: {req.appointment_id}. Feedback: {row.id}.", assignee_id=manager.id, status="open", version=1, created_by_id=manager.id)
            db.add(task)
            await db.flush()
            db.add(CollabTaskEvent(clinic_id=req.clinic_id, task_id=task.id, version=1, from_status=None, to_status="open", from_assignee_id=None, to_assignee_id=manager.id, reason="Feedback pacient cu notă sub prag", actor_id=manager.id))
            db.add(FeedbackTaskLink(feedback_id=row.id, task_id=task.id, clinic_id=req.clinic_id))
    await db.commit()
    return PublicFeedback(valid=False, message="Mulțumim pentru răspuns.")


@router.get("/feedback/settings")
async def read_settings(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    cfg = await get_settings(db, user.clinic_id)
    return {"delay_minutes": cfg.delay_minutes, "negative_threshold": cfg.negative_threshold, "review_enabled": cfg.review_enabled, "review_url": cfg.review_url, "hourly_limit": cfg.hourly_limit}


@router.put("/feedback/settings")
async def write_settings(payload: SettingsIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    cfg = await db.get(FeedbackSettings, user.clinic_id)
    if not cfg:
        cfg = FeedbackSettings(clinic_id=user.clinic_id)
        db.add(cfg)
    cfg.delay_minutes, cfg.negative_threshold = payload.delay_minutes, payload.negative_threshold
    cfg.review_enabled, cfg.review_url = payload.review_enabled, str(payload.review_url) if payload.review_url else None
    cfg.hourly_limit, cfg.updated_by_id, cfg.updated_at = payload.hourly_limit, user.id, utcnow()
    await db.commit()
    return await read_settings(db, user)


@router.get("/feedback")
async def list_feedback(start: date | None = None, end: date | None = None, doctor_id: UUID | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS, *DOCTORS))):
    query = select(PatientFeedback, Patient.first_name, Patient.last_name, User.full_name).join(Patient, Patient.id == PatientFeedback.patient_id).join(User, User.id == PatientFeedback.doctor_id).where(PatientFeedback.clinic_id == user.clinic_id)
    if user.role == "doctor":
        query = query.where(PatientFeedback.doctor_id == user.id)
    elif doctor_id:
        query = query.where(PatientFeedback.doctor_id == doctor_id)
    if start:
        query = query.where(PatientFeedback.created_at >= datetime.combine(start, time.min, UTC))
    if end:
        query = query.where(PatientFeedback.created_at < datetime.combine(end + timedelta(days=1), time.min, UTC))
    rows = (await db.execute(query.order_by(PatientFeedback.created_at.desc(), PatientFeedback.id.desc()).limit(500))).all()
    tasks = (await db.execute(select(FeedbackTaskLink.feedback_id, CollabTask.id, CollabTask.status).join(CollabTask, CollabTask.id == FeedbackTaskLink.task_id).where(FeedbackTaskLink.clinic_id == user.clinic_id))).all()
    task_map = {fid: {"id": str(tid), "status": status} for fid, tid, status in tasks}
    return [{"id": str(f.id), "appointment_id": str(f.appointment_id), "patient_id": str(f.patient_id), "patient_name": f"{first} {last}", "doctor_id": str(f.doctor_id), "doctor_name": doctor, "rating": f.rating, "comment": f.comment, "created_at": f.created_at, "task": task_map.get(f.id)} for f, first, last, doctor in rows]


@router.get("/comms/campaign-preferences/{patient_id}")
async def read_campaign_preference(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    patient = await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    preference = await db.get(CampaignPreference, (user.clinic_id, patient_id))
    return {"opted_out": preference.opted_out if preference else False}


@router.put("/comms/campaign-preferences/{patient_id}")
async def write_campaign_preference(patient_id: UUID, opted_out: bool, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    patient = await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    preference = await db.get(CampaignPreference, (user.clinic_id, patient_id))
    if preference is None:
        preference = CampaignPreference(clinic_id=user.clinic_id, patient_id=patient_id, opted_out=opted_out, updated_by_id=user.id)
        db.add(preference)
    else:
        preference.opted_out, preference.updated_by_id, preference.updated_at = opted_out, user.id, utcnow()
    await db.commit()
    return {"opted_out": opted_out}


@router.get("/comms/campaign-unsubscribe/{token}")
async def check_campaign_unsubscribe(token: str, db: AsyncSession = Depends(get_db)):
    saved = await db.get(CampaignUnsubscribeToken, token_digest(token))
    return {"valid": saved is not None, "message": "Poți opri mesajele de campanie pentru această clinică." if saved else "Linkul nu este disponibil."}


@router.post("/comms/campaign-unsubscribe/{token}")
async def unsubscribe_campaigns(token: str, db: AsyncSession = Depends(get_db)):
    saved = await db.get(CampaignUnsubscribeToken, token_digest(token))
    if not saved:
        return {"message": "Linkul nu este disponibil."}
    preference = await db.scalar(select(CampaignPreference).where(CampaignPreference.clinic_id == saved.clinic_id, CampaignPreference.patient_id == saved.patient_id).with_for_update())
    if preference is None:
        preference = CampaignPreference(clinic_id=saved.clinic_id, patient_id=saved.patient_id, opted_out=True, updated_by_id=(await db.scalar(select(User.id).where(User.clinic_id == saved.clinic_id, User.role.in_(MANAGERS)).order_by(User.created_at, User.id).limit(1))))
        db.add(preference)
    preference.opted_out, preference.updated_at = True, utcnow()
    await db.commit()
    return {"message": "Dezabonarea de la campanii a fost înregistrată."}


async def _preview(db: AsyncSession, user: User, data: CampaignFilter):
    rows = await campaign_targets(db, user.clinic_id, data.filter_type, data.filter_date, data.period_start, data.period_end)
    ids = [patient.id for patient, _ in rows]
    preferences = {}
    consents = {}
    if ids:
        preferences = {(p.clinic_id, p.patient_id): p.opted_out for p in (await db.scalars(select(CampaignPreference).where(CampaignPreference.clinic_id == user.clinic_id, CampaignPreference.patient_id.in_(ids)))).all()}
        consent_rows = (await db.scalars(select(CommConsent).where(CommConsent.clinic_id == user.clinic_id, CommConsent.patient_id.in_(ids)).order_by(CommConsent.recorded_at.desc(), CommConsent.id.desc()))).all()
        for row in consent_rows:
            consents.setdefault((row.patient_id, row.channel), row.granted)
    recipients = []
    for patient, appt in rows:
        channel = next((c for c in ("email", "sms") if consents.get((patient.id, c)) and (patient.email if c == "email" else patient.phone)), None)
        recipients.append({"id": str(patient.id), "name": f"{patient.first_name} {patient.last_name}", "channel": channel, "eligible": not preferences.get((user.clinic_id, patient.id), False) and channel is not None})
    return {"count": len(recipients), "eligible_count": sum(1 for item in recipients if item["eligible"]), "recipients": recipients}


@router.post("/comms/campaigns/preview")
async def preview_campaign(payload: CampaignFilter, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    _validate_filter(payload)
    return await _preview(db, user, payload)


def _validate_filter(data: CampaignFilter):
    if data.filter_type in ("last_visit_before", "recall_due") and data.filter_date is None:
        raise HTTPException(422, "Alege data de referință")
    if data.filter_type == "procedure_in_period" and (not data.period_start or not data.period_end or data.period_start >= data.period_end):
        raise HTTPException(422, "Perioada procedurii nu este validă")


@router.get("/comms/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    rows = (await db.scalars(select(Campaign).where(Campaign.clinic_id == user.clinic_id).order_by(Campaign.created_at.desc()).limit(100))).all()
    return [{"id": str(c.id), "name": c.name, "filter_type": c.filter_type, "status": c.status, "scheduled_for": c.scheduled_for, "launched_at": c.launched_at} for c in rows]


@router.post("/comms/campaigns", status_code=201)
async def create_campaign(payload: CampaignIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    _validate_filter(payload)
    if payload.scheduled_for.tzinfo is None:
        raise HTTPException(422, "Momentul trebuie să includă fusul orar")
    row = Campaign(clinic_id=user.clinic_id, name=payload.name.strip(), filter_type=payload.filter_type, filter_date=datetime.combine(payload.filter_date, time.min, UTC) if payload.filter_date else None, period_start=payload.period_start, period_end=payload.period_end, template_type=payload.template_type, scheduled_for=payload.scheduled_for, status="draft", created_by_id=user.id)
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "name": row.name, "status": row.status}


@router.post("/comms/campaigns/{campaign_id}/launch")
async def launch_campaign(campaign_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    campaign = await db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.clinic_id == user.clinic_id).with_for_update())
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status != "draft":
        return {"id": str(campaign.id), "status": campaign.status, "queued": 0}
    data = CampaignFilter(filter_type=campaign.filter_type, filter_date=campaign.filter_date.date() if campaign.filter_date else None, period_start=campaign.period_start, period_end=campaign.period_end)
    preview = await _preview(db, user, data)
    by_patient = {UUID(p["id"]): p for p in preview["recipients"]}
    targets = await campaign_targets(db, user.clinic_id, campaign.filter_type, data.filter_date, data.period_start, data.period_end)
    from app.comms.service import _event
    queued = 0
    for patient, appt in targets:
        target = by_patient[patient.id]
        if not target["eligible"]:
            continue
        channel = target["channel"]
        key = f"campaign:{campaign.id}:{patient.id}"
        existing = await db.scalar(select(CommOutbox.id).where(CommOutbox.idempotency_key == key))
        if existing:
            continue
        preference = await db.get(CampaignPreference, (user.clinic_id, patient.id))
        unsubscribe_token = secrets.token_urlsafe(32)
        if preference is None:
            preference = CampaignPreference(clinic_id=user.clinic_id, patient_id=patient.id, opted_out=False, updated_by_id=user.id)
            db.add(preference)
        if preference.opted_out:
            continue
        db.add(CampaignUnsubscribeToken(token_hash=token_digest(unsubscribe_token), clinic_id=user.clinic_id, patient_id=patient.id))
        public_origin = app_settings.cors_origins.split(",")[0].strip().rstrip("/")
        link = f"{public_origin}/campaigns/unsubscribe/{unsubscribe_token}"
        row = CommOutbox(clinic_id=user.clinic_id, patient_id=patient.id, appointment_id=appt.id, campaign_id=campaign.id, message_type="campaign", channel=channel, appointment_starts_at=appt.starts_at, idempotency_key=key, status="pending", scheduled_for=campaign.scheduled_for, attempts=0)
        row.body = link
        db.add(row)
        await db.flush()
        _event(db, row.id, user.clinic_id, None, "pending", "campaign_launched", user.id)
        queued += 1
    campaign.status, campaign.launched_at = "scheduled", utcnow()
    await db.commit()
    return {"id": str(campaign.id), "status": campaign.status, "queued": queued}


@router.get("/feedback/report")
async def feedback_report(start: date | None = None, end: date | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    q = select(PatientFeedback).where(PatientFeedback.clinic_id == user.clinic_id)
    request_q = select(FeedbackRequest).where(FeedbackRequest.clinic_id == user.clinic_id)
    if start:
        start_at = datetime.combine(start, time.min, UTC)
        q = q.where(PatientFeedback.created_at >= start_at)
        request_q = request_q.where(FeedbackRequest.created_at >= start_at)
    if end:
        end_at = datetime.combine(end + timedelta(days=1), time.min, UTC)
        q = q.where(PatientFeedback.created_at < end_at)
        request_q = request_q.where(FeedbackRequest.created_at < end_at)
    rows = list((await db.scalars(q.order_by(PatientFeedback.created_at, PatientFeedback.id))).all())
    requests = await db.scalar(select(func.count()).select_from(request_q.subquery()))
    ratings = {n: sum(1 for row in rows if row.rating == n) for n in range(1, 6)}
    doctors = {}
    for doctor_id, doctor_name, rating in (await db.execute(select(PatientFeedback.doctor_id, User.full_name, PatientFeedback.rating).join(User, User.id == PatientFeedback.doctor_id).where(PatientFeedback.id.in_([row.id for row in rows])))).all() if rows else []:
        summary = doctors.setdefault(str(doctor_id), {"name": doctor_name, "responses": 0, "ratings": {n: 0 for n in range(1, 6)}})
        summary["responses"] += 1
        summary["ratings"][rating] += 1
    return {"requests": requests or 0, "responses": len(rows), "response_rate": round(len(rows) / requests, 4) if requests else 0, "ratings": ratings, "by_doctor": doctors, "records": [{"id": str(r.id), "appointment_id": str(r.appointment_id), "doctor_id": str(r.doctor_id), "rating": r.rating, "created_at": r.created_at} for r in rows]}
