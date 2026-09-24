from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user, require_roles
from app.database import Appointment, AuditEvent, Patient, Room, User, get_db

router = APIRouter(prefix="/appointments", tags=["appointments"])
rooms_router = APIRouter(prefix="/rooms", tags=["rooms"])
STATUSES = {"scheduled", "confirmed", "arrived", "in_progress", "completed", "cancelled", "no_show"}


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RoomResponse(RoomCreate):
    id: UUID
    clinic_id: UUID
    is_active: bool


class AppointmentCreate(BaseModel):
    patient_id: UUID
    doctor_id: UUID
    assistant_id: UUID | None = None
    room_id: UUID
    starts_at: datetime
    duration_minutes: int = Field(ge=5, le=12 * 60)
    appointment_type: str = Field(min_length=1, max_length=100)
    notes: str | None = None
    status: str = "scheduled"


class AppointmentResponse(AppointmentCreate):
    id: UUID
    clinic_id: UUID
    ends_at: datetime


def ends(item: Appointment | AppointmentCreate) -> datetime:
    starts_at = item.starts_at
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    return starts_at + timedelta(minutes=item.duration_minutes)


async def validate_refs(db: AsyncSession, payload: AppointmentCreate, clinic_id: UUID) -> None:
    patient = await db.scalar(select(Patient).where(Patient.id == payload.patient_id, Patient.clinic_id == clinic_id))
    doctor = await db.scalar(select(User).where(User.id == payload.doctor_id, User.clinic_id == clinic_id, User.role.in_(["doctor", "clinic_manager", "administrator"])))
    room = await db.scalar(select(Room).where(Room.id == payload.room_id, Room.clinic_id == clinic_id, Room.is_active.is_(True)))
    if not patient or not doctor or not room:
        raise HTTPException(422, "Patient, doctor, or room is not valid for this clinic")
    if payload.assistant_id:
        assistant = await db.scalar(select(User).where(User.id == payload.assistant_id, User.clinic_id == clinic_id, User.role == "assistant"))
        if not assistant:
            raise HTTPException(422, "Assistant is not valid for this clinic")


async def check_conflicts(db: AsyncSession, payload: AppointmentCreate, clinic_id: UUID) -> None:
    if payload.starts_at.tzinfo is None:
        raise HTTPException(422, "starts_at must include a timezone")
    end = ends(payload)
    candidates = (await db.scalars(select(Appointment).where(Appointment.clinic_id == clinic_id, Appointment.status.not_in(["cancelled", "no_show"])))) .all()
    for existing in candidates:
        existing_end = ends(existing)
        existing_start = existing.starts_at if existing.starts_at.tzinfo is not None else existing.starts_at.replace(tzinfo=UTC)
        overlaps = payload.starts_at < existing_end and existing_start < end
        same_resource = existing.doctor_id == payload.doctor_id or existing.room_id == payload.room_id or (payload.assistant_id is not None and existing.assistant_id == payload.assistant_id)
        if overlaps and same_resource:
            raise HTTPException(status.HTTP_409_CONFLICT, "Scheduling conflict for doctor, room, or assistant")


@rooms_router.post("", response_model=RoomResponse, status_code=201)
async def create_room(payload: RoomCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "administrator"))):
    room = Room(clinic_id=user.clinic_id, name=payload.name.strip())
    db.add(room)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(409, "Room name already exists") from exc
    await db.refresh(room)
    return room


@rooms_router.get("", response_model=list[RoomResponse])
async def list_rooms(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return (await db.scalars(select(Room).where(Room.clinic_id == user.clinic_id, Room.is_active.is_(True)).order_by(Room.name))).all()


@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(payload: AppointmentCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.status not in STATUSES:
        raise HTTPException(422, "Unknown appointment status")
    await validate_refs(db, payload, user.clinic_id)
    await check_conflicts(db, payload, user.clinic_id)
    item = Appointment(clinic_id=user.clinic_id, **payload.model_dump())
    db.add(item)
    await db.flush()
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="created"))
    await db.commit()
    await db.refresh(item)
    return AppointmentResponse.model_validate({**payload.model_dump(), "id": item.id, "clinic_id": item.clinic_id, "ends_at": ends(item)})


@router.get("", response_model=list[AppointmentResponse])
async def list_appointments(start: datetime | None = None, end: datetime | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    query = select(Appointment).where(Appointment.clinic_id == user.clinic_id).order_by(Appointment.starts_at)
    if start: query = query.where(Appointment.starts_at >= start)
    if end: query = query.where(Appointment.starts_at < end)
    return [AppointmentResponse.model_validate({**AppointmentCreate.model_validate(item, from_attributes=True).model_dump(), "id": item.id, "clinic_id": item.clinic_id, "ends_at": ends(item)}) for item in (await db.scalars(query)).all()]


@router.patch("/{appointment_id}/status", response_model=AppointmentResponse)
async def update_status(appointment_id: UUID, status_value: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id))
    if not item: raise HTTPException(404, "Appointment not found")
    if status_value not in STATUSES: raise HTTPException(422, "Unknown appointment status")
    item.status = status_value
    await db.commit(); await db.refresh(item)
    payload = AppointmentCreate.model_validate(item, from_attributes=True)
    return AppointmentResponse.model_validate({**payload.model_dump(), "id": item.id, "clinic_id": item.clinic_id, "ends_at": ends(item)})
