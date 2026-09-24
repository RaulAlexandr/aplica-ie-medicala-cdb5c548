from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import Appointment, AuditEvent, Patient, Room, User, get_db

router = APIRouter(prefix="/appointments", tags=["appointments"])
rooms_router = APIRouter(prefix="/rooms", tags=["rooms"])
STATUSES = {"scheduled", "confirmed", "arrived", "in_progress", "completed", "cancelled", "no_show"}
ACTIVE_STATUSES = STATUSES - {"cancelled", "no_show"}
TRANSITIONS = {
    "scheduled": {"confirmed", "arrived", "cancelled", "no_show"},
    "confirmed": {"arrived", "cancelled", "no_show"},
    "arrived": {"in_progress", "cancelled", "no_show"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": {"scheduled", "confirmed"},
    "no_show": {"scheduled", "confirmed"},
}

class AppointmentCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    assistant_id: UUID | None = None
    room_id: UUID
    starts_at: datetime
    duration_minutes: int = Field(ge=5, le=24 * 60)
    status: str = "scheduled"
    appointment_type: str = Field(min_length=1, max_length=100)
    notes: str | None = None
class AppointmentStatus(BaseModel):
    status: str
class AppointmentResponse(AppointmentCreate):
    id: UUID
    clinic_id: UUID
    ends_at: datetime
    model_config = ConfigDict(from_attributes=True)
class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
class RoomResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

def normalized(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise HTTPException(422, "starts_at must include a timezone")
    return value.astimezone(UTC)

def end_for(start: datetime, duration: int) -> datetime:
    return normalized(start) + timedelta(minutes=duration)

def ends(item: Appointment | AppointmentCreate) -> datetime:
    return end_for(item.starts_at, item.duration_minutes)

def as_response(item: Appointment) -> AppointmentResponse:
    return AppointmentResponse.model_validate({**AppointmentCreate.model_validate(item, from_attributes=True).model_dump(), "id": item.id, "clinic_id": item.clinic_id, "ends_at": ends(item)})

async def validate_refs(db: AsyncSession, payload: AppointmentCreate, clinic_id: UUID) -> None:
    if payload.status not in STATUSES:
        raise HTTPException(422, "Unknown appointment status")
    patient = await db.scalar(select(Patient).where(Patient.id == payload.patient_id, Patient.clinic_id == clinic_id))
    doctor = await db.scalar(select(User).where(User.id == payload.doctor_id, User.clinic_id == clinic_id, User.role.in_(["doctor", "clinic_manager", "administrator"]), User.is_active.is_(True)))
    room = await db.scalar(select(Room).where(Room.id == payload.room_id, Room.clinic_id == clinic_id, Room.is_active.is_(True)))
    if not patient or not doctor or not room:
        raise HTTPException(422, "Patient, doctor, or room is not valid for this clinic")
    if payload.assistant_id:
        assistant = await db.scalar(select(User).where(User.id == payload.assistant_id, User.clinic_id == clinic_id, User.role == "assistant", User.is_active.is_(True)))
        if not assistant:
            raise HTTPException(422, "Assistant is not valid for this clinic")

async def check_conflicts(db: AsyncSession, payload: AppointmentCreate, clinic_id: UUID) -> None:
    start = require_aware(payload.starts_at)
    end = end_for(start, payload.duration_minutes)
    query = select(Appointment).where(Appointment.clinic_id == clinic_id, Appointment.status.in_(ACTIVE_STATUSES), Appointment.starts_at < end)
    candidates = (await db.scalars(query)).all()
    for existing in candidates:
        existing_start = normalized(existing.starts_at)
        if existing_start >= end or existing_start + timedelta(minutes=existing.duration_minutes) <= start:
            continue
        if existing.doctor_id == payload.doctor_id or existing.room_id == payload.room_id or (payload.assistant_id is not None and existing.assistant_id == payload.assistant_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Scheduling conflict for doctor, room, or assistant")

@rooms_router.post("", response_model=RoomResponse, status_code=201)
async def create_room(payload: RoomCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "administrator"))):
    room = Room(clinic_id=user.clinic_id, name=payload.name.strip())
    db.add(room)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Room name already exists") from exc
    await db.refresh(room)
    return room

@rooms_router.get("", response_model=list[RoomResponse])
async def list_rooms(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    return (await db.scalars(select(Room).where(Room.clinic_id == user.clinic_id, Room.is_active.is_(True)).order_by(Room.name))).all()

@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(payload: AppointmentCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "reception", "administrator"))):
    await validate_refs(db, payload, user.clinic_id)
    if payload.status in ACTIVE_STATUSES:
        await check_conflicts(db, payload, user.clinic_id)
    item = Appointment(clinic_id=user.clinic_id, **payload.model_dump())
    db.add(item)
    try:
        await db.flush()
        db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="created"))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Scheduling conflict for doctor, room, or assistant") from exc
    await db.refresh(item)
    return as_response(item)

@router.get("", response_model=list[AppointmentResponse])
async def list_appointments(start: datetime | None = None, end: datetime | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    query = select(Appointment).where(Appointment.clinic_id == user.clinic_id).order_by(Appointment.starts_at)
    if start:
        query = query.where(Appointment.starts_at >= normalized(start))
    if end:
        query = query.where(Appointment.starts_at < normalized(end))
    return [as_response(item) for item in (await db.scalars(query)).all()]

@router.patch("/{appointment_id}/status", response_model=AppointmentResponse)
async def update_status(appointment_id: UUID, payload: AppointmentStatus, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id).with_for_update())
    if not item:
        raise HTTPException(404, "Appointment not found")
    if payload.status not in STATUSES:
        raise HTTPException(422, "Unknown appointment status")
    if payload.status != item.status and payload.status not in TRANSITIONS[item.status]:
        raise HTTPException(409, f"Invalid appointment transition: {item.status} -> {payload.status}")
    if payload.status in ACTIVE_STATUSES and item.status not in ACTIVE_STATUSES:
        candidate = AppointmentCreate.model_validate(item, from_attributes=True)
        await check_conflicts(db, candidate, user.clinic_id)
    item.status = payload.status
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="status_changed"))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Scheduling conflict for doctor, room, or assistant") from exc
    await db.refresh(item)
    return as_response(item)
