from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.router import appointment_with_names, refs, validate_slot
from app.appointments.service import add_history, appointment_end, utc
from app.auth.service import require_roles
from app.database import (
    Appointment,
    AuditEvent,
    Clinic,
    Patient,
    Recall,
    RecallHistory,
    User,
    WaitlistEntry,
    WaitlistOffer,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])
SCHEDULERS = ("reception", "clinic_manager", "administrator")
RECALL_CREATORS = ("doctor", *SCHEDULERS)
READERS = (*SCHEDULERS, "assistant")
RECALL_READERS = (*RECALL_CREATORS, "assistant")
ACTIVE_APPOINTMENTS = ("scheduled", "confirmed", "arrived", "in_progress", "completed")

class Window(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: time
    end: time
    @field_validator("end")
    @classmethod
    def positive(cls, v, info):
        if info.data.get("start") and v <= info.data["start"]: raise ValueError("window end must follow start")
        return v

class EntryCreate(BaseModel):
    patient_id: UUID
    desired_service: str | None = Field(default=None, max_length=120)
    desired_duration_minutes: int = Field(ge=5, le=1440)
    preferred_doctor_id: UUID | None = None
    windows: list[Window] = Field(min_length=1)
    valid_from: date
    valid_until: date
    priority: int = Field(default=0, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=2000)

class OfferCreate(BaseModel):
    doctor_id: UUID
    room_id: UUID
    starts_at: datetime
    duration_minutes: int = Field(ge=5, le=1440)
    expires_in_minutes: int = Field(default=15, ge=1, le=60)

class RecallCreate(BaseModel):
    patient_id: UUID
    due_date: date
    reason: str = Field(min_length=1, max_length=240)
    service: str | None = Field(default=None, max_length=120)

class RecallAction(BaseModel):
    action: str
    @field_validator("action")
    @classmethod
    def valid(cls, value):
        if value not in {"contacted", "scheduled", "refused"}: raise ValueError("invalid recall action")
        return value

async def scoped_entry(db, user, entry_id, lock=False):
    stmt = select(WaitlistEntry).where(WaitlistEntry.id == entry_id, WaitlistEntry.clinic_id == user.clinic_id)
    item = await db.scalar(stmt.with_for_update() if lock else stmt)
    if not item: raise HTTPException(404, "Waitlist entry not found")
    return item

def entry_out(e):
    return {"id":str(e.id),"patient_id":str(e.patient_id),"desired_service":e.desired_service,"desired_duration_minutes":e.desired_duration_minutes,"preferred_doctor_id":str(e.preferred_doctor_id) if e.preferred_doctor_id else None,"windows":e.windows,"valid_from":e.valid_from,"valid_until":e.valid_until,"priority":e.priority,"notes":e.notes,"status":e.status,"created_at":e.created_at}

def matches_window(entry, start, end, zone):
    local = start.astimezone(zone); local_end = end.astimezone(zone)
    if local.date() != local_end.date() or not (entry.valid_from <= local.date() <= entry.valid_until): return False
    return any(w["weekday"] == local.weekday() and time.fromisoformat(w["start"]) <= local.timetz().replace(tzinfo=None) and time.fromisoformat(w["end"]) >= local_end.timetz().replace(tzinfo=None) for w in entry.windows)

@router.get("/entries")
async def list_entries(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    clinic=await db.get(Clinic,user.clinic_id);today=utcnow().astimezone(ZoneInfo(clinic.timezone)).date()
    if user.role in SCHEDULERS:
        await db.execute(update(WaitlistEntry).where(WaitlistEntry.clinic_id==user.clinic_id,WaitlistEntry.status=="active",WaitlistEntry.valid_until<today).values(status="expired"))
        await db.commit()
    rows=(await db.scalars(select(WaitlistEntry).where(WaitlistEntry.clinic_id==user.clinic_id).order_by(WaitlistEntry.priority.desc(),WaitlistEntry.created_at))).all()
    return [entry_out(e) for e in rows]

@router.post("/entries",status_code=201)
async def create_entry(data:EntryCreate,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*SCHEDULERS))):
    patient=await db.scalar(select(Patient).where(Patient.id==data.patient_id,Patient.clinic_id==user.clinic_id))
    if not patient: raise HTTPException(404,"Patient not found")
    if data.valid_until<data.valid_from: raise HTTPException(422,"Invalid validity period")
    if data.preferred_doctor_id and not await db.scalar(select(User.id).where(User.id==data.preferred_doctor_id,User.clinic_id==user.clinic_id,User.role.in_(("doctor","clinic_manager","administrator")),User.is_active.is_(True))): raise HTTPException(404,"Doctor not found")
    e=WaitlistEntry(clinic_id=user.clinic_id,patient_id=data.patient_id,desired_service=data.desired_service,desired_duration_minutes=data.desired_duration_minutes,preferred_doctor_id=data.preferred_doctor_id,windows=[w.model_dump(mode="json") for w in data.windows],valid_from=data.valid_from,valid_until=data.valid_until,priority=data.priority,notes=data.notes,status="active",created_by_id=user.id)
    db.add(e);await db.commit();return entry_out(e)

@router.post("/slots/candidates")
async def candidates(data:OfferCreate,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*SCHEDULERS))):
    if data.starts_at.tzinfo is None: raise HTTPException(422,"starts_at must include timezone")
    start=utc(data.starts_at);end=appointment_end(start,data.duration_minutes);clinic=await db.get(Clinic,user.clinic_id)
    if not clinic: raise HTTPException(404,"Clinic not found")
    zone=ZoneInfo(clinic.timezone)
    await db.execute(update(WaitlistEntry).where(WaitlistEntry.clinic_id==user.clinic_id,WaitlistEntry.status=="active",WaitlistEntry.valid_until<start.astimezone(zone).date()).values(status="expired"));await db.commit()
    patient_conflicts=set((await db.scalars(select(Appointment.patient_id).where(Appointment.clinic_id==user.clinic_id,Appointment.status.in_(ACTIVE_APPOINTMENTS),Appointment.starts_at<end,Appointment.ends_at>start))).all())
    rows=(await db.scalars(select(WaitlistEntry).where(WaitlistEntry.clinic_id==user.clinic_id,WaitlistEntry.status=="active",WaitlistEntry.desired_duration_minutes<=data.duration_minutes,WaitlistEntry.valid_from<=end.astimezone(zone).date(),WaitlistEntry.valid_until>=start.astimezone(zone).date(),(WaitlistEntry.preferred_doctor_id.is_(None))|(WaitlistEntry.preferred_doctor_id==data.doctor_id)).order_by(WaitlistEntry.priority.desc(),WaitlistEntry.created_at))).all()
    return [entry_out(e) for e in rows if e.patient_id not in patient_conflicts and matches_window(e,start,end,zone)]

@router.post("/entries/{entry_id}/offers",status_code=201)
async def create_offer(entry_id:UUID,data:OfferCreate,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*SCHEDULERS))):
    await db.execute(select(Clinic.id).where(Clinic.id==user.clinic_id).with_for_update())
    now=utcnow(); expired=(await db.scalars(select(WaitlistOffer).where(WaitlistOffer.clinic_id==user.clinic_id,WaitlistOffer.status=="active",WaitlistOffer.expires_at<=now).with_for_update())).all()
    for old in expired:
        old.status="expired";ent=await scoped_entry(db,user,old.entry_id,True);ent.status="active" if ent.valid_until>=now.date() else "expired"
    e=await scoped_entry(db,user,entry_id,True)
    if e.status!="active": raise HTTPException(409,"Entry is not active")
    if data.starts_at.tzinfo is None: raise HTTPException(422,"starts_at must include timezone")
    start=utc(data.starts_at);end=appointment_end(start,data.duration_minutes);clinic=await db.get(Clinic,user.clinic_id);zone=ZoneInfo(clinic.timezone)
    if data.duration_minutes<e.desired_duration_minutes or (e.preferred_doctor_id and e.preferred_doctor_id!=data.doctor_id) or not matches_window(e,start,end,zone): raise HTTPException(422,"Entry does not match this slot")
    await refs(db,user.clinic_id,e.patient_id,data.doctor_id,None,data.room_id)
    await validate_slot(db,user.clinic_id,start,data.duration_minutes,data.doctor_id,data.room_id,None)
    conflict=await db.scalar(select(Appointment.id).where(Appointment.clinic_id==user.clinic_id,Appointment.patient_id==e.patient_id,Appointment.status.in_(ACTIVE_APPOINTMENTS),Appointment.starts_at<end,Appointment.ends_at>start))
    if conflict: raise HTTPException(409,"Patient already has an overlapping appointment")
    offer=WaitlistOffer(clinic_id=user.clinic_id,entry_id=e.id,doctor_id=data.doctor_id,room_id=data.room_id,starts_at=start,duration_minutes=data.duration_minutes,expires_at=now+timedelta(minutes=data.expires_in_minutes),status="active",created_by_id=user.id)
    db.add(offer);e.status="offered"
    try: await db.commit()
    except IntegrityError as exc: await db.rollback();raise HTTPException(409,"An active offer already reserves this interval") from exc
    return {"id":str(offer.id),"entry_id":str(e.id),"starts_at":start,"duration_minutes":data.duration_minutes,"expires_at":offer.expires_at,"status":offer.status}

@router.post("/offers/{offer_id}/confirm",status_code=201)
async def confirm_offer(offer_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*SCHEDULERS))):
    offer=await db.scalar(select(WaitlistOffer).where(WaitlistOffer.id==offer_id,WaitlistOffer.clinic_id==user.clinic_id).with_for_update())
    if not offer: raise HTTPException(404,"Offer not found")
    now=utcnow()
    if offer.status!="active" or offer.expires_at<=now:
        if offer.status=="active": offer.status="expired";e=await scoped_entry(db,user,offer.entry_id,True);e.status="expired" if e.valid_until<now.date() else "active";await db.commit()
        raise HTTPException(409,"Offer expired or already resolved")
    e=await scoped_entry(db,user,offer.entry_id,True);start=offer.starts_at
    await refs(db,user.clinic_id,e.patient_id,offer.doctor_id,None,offer.room_id)
    await validate_slot(db,user.clinic_id,start,offer.duration_minutes,offer.doctor_id,offer.room_id,None)
    appt=Appointment(clinic_id=user.clinic_id,patient_id=e.patient_id,doctor_id=offer.doctor_id,room_id=offer.room_id,starts_at=start,ends_at=appointment_end(start,offer.duration_minutes),duration_minutes=offer.duration_minutes,status="scheduled",appointment_type=e.desired_service or "Waitlist offer")
    db.add(appt)
    try:
        await db.flush();add_history(db,appt,user.id,"created");db.add(AuditEvent(clinic_id=user.clinic_id,actor_id=user.id,entity_type="appointment",entity_id=appt.id,action="created"));offer.status="confirmed";offer.appointment_id=appt.id;e.status="booked";await db.commit()
    except IntegrityError as exc: await db.rollback();raise HTTPException(409,"Slot was booked by another request") from exc
    return await appointment_with_names(db,appt)

@router.post("/offers/{offer_id}/cancel")
async def cancel_offer(offer_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*SCHEDULERS))):
    offer = await db.scalar(select(WaitlistOffer).where(WaitlistOffer.id == offer_id, WaitlistOffer.clinic_id == user.clinic_id).with_for_update())
    if not offer:
        raise HTTPException(404, "Offer not found")
    if offer.status != "active":
        raise HTTPException(409, "Offer is already resolved")
    entry = await scoped_entry(db, user, offer.entry_id, True)
    clinic = await db.get(Clinic, user.clinic_id)
    today = utcnow().astimezone(ZoneInfo(clinic.timezone)).date()
    offer.status = "cancelled"
    entry.status = "active" if entry.valid_until >= today else "expired"
    await db.commit()
    return {"id": str(offer.id), "entry_id": str(entry.id), "status": offer.status, "entry_status": entry.status}


@router.get("/offers")
async def list_offers(db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*READERS))):
    rows=(await db.scalars(select(WaitlistOffer).where(WaitlistOffer.clinic_id==user.clinic_id).order_by(WaitlistOffer.created_at.desc()))).all()
    return [{"id":str(o.id),"entry_id":str(o.entry_id),"doctor_id":str(o.doctor_id),"room_id":str(o.room_id),"starts_at":o.starts_at,"duration_minutes":o.duration_minutes,"expires_at":o.expires_at,"status":o.status,"appointment_id":str(o.appointment_id) if o.appointment_id else None} for o in rows]

@router.post("/entries/{entry_id}/cancel")
async def cancel_entry(entry_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*SCHEDULERS))):
    e=await scoped_entry(db,user,entry_id,True)
    if e.status not in {"active","offered"}:raise HTTPException(409,"Entry cannot be cancelled")
    offers=(await db.scalars(select(WaitlistOffer).where(WaitlistOffer.entry_id==e.id,WaitlistOffer.clinic_id==user.clinic_id,WaitlistOffer.status=="active").with_for_update())).all()
    for item in offers:item.status="cancelled"
    e.status="cancelled";await db.commit();return entry_out(e)

@router.get("/recalls")
async def recalls(start:date|None=None,end:date|None=None,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*RECALL_READERS))):
    q=select(Recall).where(Recall.clinic_id==user.clinic_id,Recall.status=="pending")
    if start:q=q.where(Recall.due_date>=start)
    if end:q=q.where(Recall.due_date<=end)
    return [{"id":str(r.id),"patient_id":str(r.patient_id),"due_date":r.due_date,"reason":r.reason,"service":r.service,"status":r.status} for r in (await db.scalars(q.order_by(Recall.due_date))).all()]

@router.get("/recalls/{recall_id}")
async def recall_detail(recall_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*RECALL_READERS))):
    r=await db.scalar(select(Recall).where(Recall.id==recall_id,Recall.clinic_id==user.clinic_id))
    if not r:raise HTTPException(404,"Recall not found")
    history=(await db.scalars(select(RecallHistory).where(RecallHistory.recall_id==r.id,RecallHistory.clinic_id==user.clinic_id).order_by(RecallHistory.occurred_at))).all()
    return {"id":str(r.id),"patient_id":str(r.patient_id),"due_date":r.due_date,"reason":r.reason,"service":r.service,"status":r.status,"history":[{"action":h.action,"actor_id":str(h.actor_id),"occurred_at":h.occurred_at} for h in history]}

@router.post("/recalls",status_code=201)
async def create_recall(data:RecallCreate,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*RECALL_CREATORS))):
    if not await db.scalar(select(Patient.id).where(Patient.id==data.patient_id,Patient.clinic_id==user.clinic_id)):raise HTTPException(404,"Patient not found")
    r=Recall(clinic_id=user.clinic_id,patient_id=data.patient_id,due_date=data.due_date,reason=data.reason.strip(),service=data.service,status="pending",created_by_id=user.id);db.add(r);await db.commit();return {"id":str(r.id),"patient_id":str(r.patient_id),"due_date":r.due_date,"reason":r.reason,"service":r.service,"status":r.status}

@router.post("/recalls/{recall_id}/actions")
async def recall_action(recall_id:UUID,data:RecallAction,db:AsyncSession=Depends(get_db),user:User=Depends(require_roles(*RECALL_CREATORS))):
    r=await db.scalar(select(Recall).where(Recall.id==recall_id,Recall.clinic_id==user.clinic_id).with_for_update())
    if not r:raise HTTPException(404,"Recall not found")
    if r.status not in {"pending", "contacted"}:
        raise HTTPException(409, "Recall is already resolved")
    r.status=data.action;db.add(RecallHistory(clinic_id=user.clinic_id,recall_id=r.id,actor_id=user.id,action=data.action));await db.commit()
    history=(await db.scalars(select(RecallHistory).where(RecallHistory.recall_id==r.id,RecallHistory.clinic_id==user.clinic_id).order_by(RecallHistory.occurred_at))).all()
    return {"id":str(r.id),"status":r.status,"history":[{"action":h.action,"actor_id":str(h.actor_id),"occurred_at":h.occurred_at} for h in history]}
