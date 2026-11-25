from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user, require_roles
from app.database import Appointment, Clinic, Room, User, get_db, utcnow
from app.locations.models import ClinicLocation, UserLocationAssignment

router = APIRouter(prefix="/locations", tags=["locations"])
MANAGERS = ("clinic_manager", "administrator")


class LocationInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Denumirea sediului este obligatorie.")
        return value


class LocationPreferencesInput(BaseModel):
    default_location_id: UUID
    all_locations: bool = True
    location_ids: list[UUID] = Field(default_factory=list)


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_optional_name(cls, value):
        if value is None:
            raise ValueError("Denumirea sediului nu poate fi goală")
        value = value.strip()
        if not value:
            raise ValueError("Denumirea sediului nu poate fi goală")
        return value


@router.get("")
async def list_locations(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles("clinic_manager", "administrator", "doctor", "assistant", "reception"))):
    return list((await db.scalars(select(ClinicLocation).where(ClinicLocation.clinic_id == user.clinic_id).order_by(ClinicLocation.name))).all())


@router.get("/preferences")
async def get_location_preferences(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    assigned = list((await db.scalars(select(UserLocationAssignment.location_id).where(UserLocationAssignment.user_id == user.id).order_by(UserLocationAssignment.location_id))).all())
    return {"default_location_id": str(user.default_location_id) if user.default_location_id else None,
            "all_locations": user.all_locations, "location_ids": [str(item) for item in assigned]}


@router.put("/preferences")
async def update_location_preferences(payload: LocationPreferencesInput, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    assigned_ids = set(payload.location_ids)
    if not payload.all_locations and (not assigned_ids or payload.default_location_id not in assigned_ids):
        raise HTTPException(422, "Sediul implicit trebuie să fie în lista sediilor de lucru")
    requested = assigned_ids | {payload.default_location_id}
    rows = list((await db.scalars(select(ClinicLocation).where(ClinicLocation.id.in_(requested), ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True)))).all())
    if len(rows) != len(requested):
        raise HTTPException(404, "Un sediu nu există sau nu este activ în clinică")
    await db.execute(delete(UserLocationAssignment).where(UserLocationAssignment.user_id == user.id))
    if not payload.all_locations:
        db.add_all(UserLocationAssignment(user_id=user.id, location_id=location_id) for location_id in sorted(assigned_ids, key=str))
    user.default_location_id = payload.default_location_id
    user.all_locations = payload.all_locations
    await db.commit()
    return await get_location_preferences(db, user)


@router.post("", status_code=201)
async def create_location(payload: LocationInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    clinic = await db.get(Clinic, user.clinic_id)
    if payload.timezone:
        try:
            ZoneInfo(payload.timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(422, "Fusul orar trebuie să fie un fus IANA valid") from exc
        if payload.timezone != clinic.timezone:
            raise HTTPException(422, "În versiunea 1 sediile folosesc fusul clinicii")
    row = ClinicLocation(clinic_id=user.clinic_id, **payload.model_dump())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Există deja un sediu cu acest nume") from exc
    await db.refresh(row)
    return row


@router.patch("/{location_id}")
async def update_location(location_id: UUID, payload: LocationUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    row = await db.scalar(select(ClinicLocation).where(ClinicLocation.id == location_id, ClinicLocation.clinic_id == user.clinic_id).with_for_update())
    if not row:
        raise HTTPException(404, "Sediul nu există")
    clinic = await db.get(Clinic, user.clinic_id)
    values = payload.model_dump(exclude_unset=True)
    timezone = values.get("timezone", row.timezone)
    if timezone:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(422, "Fusul orar trebuie să fie un fus IANA valid") from exc
        if timezone != clinic.timezone:
            raise HTTPException(422, "În versiunea 1 sediile folosesc fusul clinicii")
    is_active = values.get("is_active", row.is_active)
    if not is_active:
        room = await db.scalar(select(Room.id).where(Room.clinic_id == user.clinic_id, Room.location_id == row.id, Room.is_active.is_(True)).limit(1))
        future = await db.scalar(select(Appointment.id).join(Room, Room.id == Appointment.room_id).where(Appointment.clinic_id == user.clinic_id, Room.location_id == row.id, Appointment.starts_at > utcnow(), Appointment.status.in_(("scheduled", "confirmed", "arrived", "in_progress"))).limit(1))
        if room or future:
            raise HTTPException(409, "Sediul are cabinete active sau programări viitoare")
        active = await db.scalar(select(func.count()).select_from(ClinicLocation).where(ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True)))
        if row.is_active and active <= 1:
            raise HTTPException(409, "Clinica trebuie să păstreze cel puțin un sediu activ")
        if row.is_active and not is_active:
            replacement_id = await db.scalar(select(ClinicLocation.id).where(ClinicLocation.clinic_id == user.clinic_id, ClinicLocation.is_active.is_(True), ClinicLocation.id != row.id).order_by(ClinicLocation.created_at).limit(1))
            restricted_users = list((await db.scalars(select(UserLocationAssignment.user_id).join(User, User.id == UserLocationAssignment.user_id).where(UserLocationAssignment.location_id == row.id, User.all_locations.is_(False)))).all())
            await db.execute(update(User).where(User.default_location_id == row.id).values(default_location_id=replacement_id))
            await db.execute(delete(UserLocationAssignment).where(UserLocationAssignment.location_id == row.id))
            db.add_all(UserLocationAssignment(user_id=assigned_user_id, location_id=replacement_id) for assigned_user_id in restricted_users)
    for key, value in values.items():
        setattr(row, key, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Există deja un sediu cu acest nume") from exc
    await db.refresh(row)
    return row
