from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import Appointment, AuditEvent, Room, User, get_db, utcnow

router = APIRouter(prefix="/rooms", tags=["rooms"])
MANAGERS = ("clinic_manager", "administrator")
ACTIVE_APPOINTMENT_STATUSES = ("scheduled", "confirmed", "arrived", "in_progress", "completed")


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    location_id: UUID | None = None


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None
    location_id: UUID | None = None


class RoomResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    name: str
    is_active: bool
    location_id: UUID
    model_config = ConfigDict(from_attributes=True)


class RoomStatusResponse(BaseModel):
    room: RoomResponse
    affected_appointments: list[dict[str, object]] = []


@router.get("", response_model=list[RoomResponse])
async def list_rooms(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "doctor", "assistant", "reception", "administrator"))):
    return (await db.scalars(select(Room).where(Room.clinic_id == user.clinic_id).order_by(Room.is_active.desc(), Room.name))).all()


@router.post("", response_model=RoomResponse, status_code=201)
async def create_room(payload: RoomCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Room name cannot be empty")
    if await db.scalar(select(Room.id).where(Room.clinic_id == user.clinic_id, func.lower(Room.name) == name.lower())):
        raise HTTPException(409, "Room name already exists")
    from app.locations.models import ClinicLocation
    active_locations = list((await db.scalars(select(ClinicLocation.id).where(ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True)).order_by(ClinicLocation.created_at))).all())
    if payload.location_id is None and len(active_locations) > 1:
        raise HTTPException(422, "Alege sediul cabinetului")
    location_id = payload.location_id or (active_locations[0] if active_locations else None)
    if not location_id or not await db.scalar(select(ClinicLocation.id).where(ClinicLocation.id == location_id, ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True))):
        raise HTTPException(422, "Alege un sediu activ al clinicii")
    room = Room(clinic_id=user.clinic_id, name=name, location_id=location_id)
    db.add(room)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Room name already exists") from exc
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="room", entity_id=room.id, action="created"))
    await db.commit()
    await db.refresh(room)
    return room


@router.patch("/{room_id}", response_model=RoomStatusResponse)
async def update_room(room_id: UUID, payload: RoomUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    room = await db.scalar(select(Room).where(Room.id == room_id, Room.clinic_id == user.clinic_id).with_for_update())
    if not room:
        raise HTTPException(404, "Room not found")
    if payload.location_id is not None and payload.location_id != room.location_id:
        future = await db.scalar(select(Appointment.id).where(Appointment.clinic_id == user.clinic_id, Appointment.room_id == room.id, Appointment.starts_at > utcnow(), Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES)).limit(1))
        if future:
            raise HTTPException(409, "Cabinetul nu poate fi mutat deoarece are programări viitoare")
        from app.locations.models import ClinicLocation
        if not await db.scalar(select(ClinicLocation.id).where(ClinicLocation.id == payload.location_id, ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True))):
            raise HTTPException(422, "Sediul nu este activ sau nu aparține clinicii")
        room.location_id = payload.location_id
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Room name cannot be empty")
        if await db.scalar(select(Room.id).where(Room.clinic_id == user.clinic_id, func.lower(Room.name) == name.lower(), Room.id != room.id)):
            raise HTTPException(409, "Room name already exists")
        room.name = name
    if payload.is_active is False and room.is_active:
        affected = (await db.scalars(select(Appointment).where(Appointment.clinic_id == user.clinic_id, Appointment.room_id == room.id, Appointment.starts_at > utcnow(), Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES)).order_by(Appointment.starts_at))).all()
        if affected:
            return RoomStatusResponse(room=room, affected_appointments=[{"id": str(item.id), "starts_at": item.starts_at.isoformat(), "status": item.status} for item in affected])
        room.is_active = False
    elif payload.is_active is True:
        room.is_active = True
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Room name already exists") from exc
    db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="room", entity_id=room.id, action="updated"))
    await db.commit()
    await db.refresh(room)
    return RoomStatusResponse(room=room, affected_appointments=[])
