from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.service import (
    ACTIVE_STATUSES,
    add_history,
    appointment_end,
    check_conflicts,
    clinic_timezone,
    history_payload,
    local_bounds,
    local_to_utc,
    utc,
    validate_availability,
)
from app.auth.service import require_roles
from app.database import (
    Appointment,
    AppointmentHistory,
    AuditEvent,
    Patient,
    ResourceUnavailability,
    Room,
    User,
    WorkingHours,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/appointments", tags=["appointments"])
rooms_router = APIRouter(prefix="/rooms", tags=["rooms"])
staff_router = APIRouter(prefix="/staff", tags=["staff"])
availability_router = APIRouter(prefix="/availability", tags=["availability"])
MANAGERS = ("clinic_manager", "administrator")
ALL_STAFF = ("clinic_manager", "doctor", "assistant", "reception", "administrator")
MANAGE_APPOINTMENTS = ("clinic_manager", "doctor", "reception", "administrator")
STATUSES = {"scheduled", "confirmed", "arrived", "in_progress", "completed", "cancelled", "no_show"}
TRANSITIONS = {"scheduled": {"confirmed", "arrived", "cancelled", "no_show"}, "confirmed": {"arrived", "cancelled", "no_show"}, "arrived": {"in_progress", "cancelled", "no_show"}, "in_progress": {"completed", "cancelled"}, "completed": set(), "cancelled": {"scheduled", "confirmed"}, "no_show": {"scheduled", "confirmed"}}
class AppointmentCreate(BaseModel):
    patient_id: UUID; doctor_id: UUID; assistant_id: UUID | None = None; room_id: UUID; starts_at: datetime | None = None; local_start: datetime | None = None; timezone_fold: int | None = Field(default=None, ge=0, le=1); duration_minutes: int = Field(ge=5, le=24 * 60); status: str = "scheduled"; appointment_type: str = Field(min_length=1, max_length=100); notes: str | None = Field(default=None, max_length=5000)
    @model_validator(mode="after")
    def has_start(self) -> AppointmentCreate:
        if self.starts_at is None and self.local_start is None: raise ValueError("starts_at or local_start is required")
        if self.starts_at is not None and self.local_start is not None: raise ValueError("Provide only one of starts_at or local_start")
        return self
class AppointmentUpdate(BaseModel):
    patient_id: UUID | None = None; doctor_id: UUID | None = None; assistant_id: UUID | None = None; room_id: UUID | None = None; starts_at: datetime | None = None; local_start: datetime | None = None; timezone_fold: int | None = Field(default=None, ge=0, le=1); duration_minutes: int | None = Field(default=None, ge=5, le=24 * 60); appointment_type: str | None = Field(default=None, min_length=1, max_length=100); notes: str | None = Field(default=None, max_length=5000)
class RescheduleRequest(BaseModel):
    local_start: datetime | None = None; starts_at: datetime | None = None; timezone_fold: int | None = Field(default=None, ge=0, le=1); duration_minutes: int | None = Field(default=None, ge=5, le=24 * 60); doctor_id: UUID | None = None; assistant_id: UUID | None = None; room_id: UUID | None = None; reason: str | None = Field(default=None, max_length=500)
class AppointmentStatus(BaseModel): status: str
class AppointmentResponse(BaseModel):
    id: UUID; clinic_id: UUID; patient_id: UUID; doctor_id: UUID; assistant_id: UUID | None; room_id: UUID; starts_at: datetime; ends_at: datetime; local_start: datetime; local_end: datetime; timezone: str; duration_minutes: int; status: str; appointment_type: str; notes: str | None; patient_name: str | None = None; doctor_name: str | None = None; assistant_name: str | None = None; room_name: str | None = None
    model_config = ConfigDict(from_attributes=True)
class RoomResponse(BaseModel):
    id: UUID; clinic_id: UUID; name: str; is_active: bool
    model_config = ConfigDict(from_attributes=True)
class DoctorResponse(BaseModel):
    id: UUID; full_name: str; role: str
    model_config = ConfigDict(from_attributes=True)
class WorkingHoursInput(BaseModel):
    day_of_week: int = Field(ge=0, le=6); start_time: time; end_time: time; is_active: bool = True
    @model_validator(mode="after")
    def valid_interval(self) -> WorkingHoursInput:
        if self.end_time <= self.start_time: raise ValueError("Working-hours end must be after start")
        return self
class WorkingHoursResponse(WorkingHoursInput):
    id: UUID
    model_config = ConfigDict(from_attributes=True)
class UnavailabilityInput(BaseModel):
    resource_type: str; resource_id: UUID; starts_at: datetime; ends_at: datetime; reason: str | None = Field(default=None, max_length=240)
    @model_validator(mode="after")
    def valid_interval(self) -> UnavailabilityInput:
        if self.resource_type not in {"doctor", "assistant", "room"}: raise ValueError("resource_type must be doctor, assistant, or room")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None or self.ends_at <= self.starts_at: raise ValueError("Unavailability requires an aware, positive interval")
        return self
class UnavailabilityResponse(BaseModel):
    id: UUID
    resource_type: str
    resource_id: UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
class AppointmentHistoryResponse(BaseModel):
    id: UUID; actor_id: UUID; action: str; details: dict; occurred_at: datetime
    model_config = ConfigDict(from_attributes=True)
def resolve_start(payload: AppointmentCreate | AppointmentUpdate | RescheduleRequest, timezone: str, current: datetime | None = None) -> datetime:
    if payload.local_start is not None: return local_to_utc(payload.local_start, timezone, payload.timezone_fold)
    if payload.starts_at is not None:
        if payload.starts_at.tzinfo is None: raise HTTPException(422, "The appointment instant must include a timezone")
        return utc(payload.starts_at)
    if current is None: raise HTTPException(422, "starts_at or local_start is required")
    return utc(current)
def as_response(item: Appointment, timezone: str, names: tuple[str | None, str | None, str | None, str | None]) -> AppointmentResponse:
    zone = ZoneInfo(timezone); start = utc(item.starts_at); end = utc(item.ends_at or appointment_end(start, item.duration_minutes))
    return AppointmentResponse(id=item.id, clinic_id=item.clinic_id, patient_id=item.patient_id, doctor_id=item.doctor_id, assistant_id=item.assistant_id, room_id=item.room_id, starts_at=start, ends_at=end, local_start=start.astimezone(zone), local_end=end.astimezone(zone), timezone=timezone, duration_minutes=item.duration_minutes, status=item.status, appointment_type=item.appointment_type, notes=item.notes, patient_name=names[0], doctor_name=names[1], assistant_name=names[2], room_name=names[3])
async def refs(db: AsyncSession, clinic_id: UUID, patient_id: UUID, doctor_id: UUID, assistant_id: UUID | None, room_id: UUID) -> None:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)); doctor = await db.scalar(select(User).where(User.id == doctor_id, User.clinic_id == clinic_id, User.role.in_(["doctor", "clinic_manager", "administrator"]), User.is_active.is_(True)).with_for_update()); room = await db.scalar(select(Room).where(Room.id == room_id, Room.clinic_id == clinic_id, Room.is_active.is_(True)).with_for_update())
    if not patient or not doctor or not room: raise HTTPException(422, "Patient, doctor, or room is not valid for this clinic")
    if assistant_id and not await db.scalar(select(User.id).where(User.id == assistant_id, User.clinic_id == clinic_id, User.role == "assistant", User.is_active.is_(True)).with_for_update()): raise HTTPException(422, "Assistant is not valid for this clinic")
async def validate_slot(db: AsyncSession, clinic_id: UUID, start: datetime, duration: int, doctor_id: UUID, room_id: UUID, assistant_id: UUID | None, exclude_id: UUID | None = None) -> None:
    end = appointment_end(start, duration); await validate_availability(db, clinic_id, start, end, doctor_id, assistant_id, room_id); await check_conflicts(db, clinic_id, start, end, doctor_id, room_id, assistant_id, exclude_id)
async def appointment_with_names(db: AsyncSession, item: Appointment) -> AppointmentResponse:
    patient = await db.get(Patient, item.patient_id); doctor = await db.get(User, item.doctor_id); assistant = await db.get(User, item.assistant_id) if item.assistant_id else None; room = await db.get(Room, item.room_id); clinic, _ = await clinic_timezone(db, item.clinic_id)
    return as_response(item, clinic.timezone, ((f"{patient.first_name} {patient.last_name}" if patient else None), doctor.full_name if doctor else None, assistant.full_name if assistant else None, room.name if room else None))
@router.post("", response_model=AppointmentResponse, status_code=201)
async def create_appointment(payload: AppointmentCreate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGE_APPOINTMENTS))):
    if payload.status != "scheduled": raise HTTPException(422, "New appointments must start in scheduled status")
    clinic, _ = await clinic_timezone(db, user.clinic_id); start = resolve_start(payload, clinic.timezone); await refs(db, user.clinic_id, payload.patient_id, payload.doctor_id, payload.assistant_id, payload.room_id); await validate_slot(db, user.clinic_id, start, payload.duration_minutes, payload.doctor_id, payload.room_id, payload.assistant_id)
    item = Appointment(clinic_id=user.clinic_id, patient_id=payload.patient_id, doctor_id=payload.doctor_id, assistant_id=payload.assistant_id, room_id=payload.room_id, starts_at=start, duration_minutes=payload.duration_minutes, status="scheduled", appointment_type=payload.appointment_type, notes=payload.notes, ends_at=appointment_end(start, payload.duration_minutes)); db.add(item)
    try:
        await db.flush(); add_history(db, item, user.id, "created"); db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="created")); await db.commit()
    except IntegrityError as exc: await db.rollback(); raise HTTPException(409, "The selected doctor, room, or assistant is already booked") from exc
    await db.refresh(item); return await appointment_with_names(db, item)
@router.get("", response_model=list[AppointmentResponse])
async def list_appointments(start: datetime | None = None, end: datetime | None = None, day: date | None = None, week_start: date | None = None, room_id: UUID | None = None, doctor_id: UUID | None = None, assistant_id: UUID | None = None, status_filter: str | None = Query(default=None, alias="status"), appointment_type: str | None = None, patient_search: str | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))):
    clinic, _ = await clinic_timezone(db, user.clinic_id)
    if day: start, end = local_bounds(day, clinic.timezone)
    elif week_start: start, end = local_bounds(week_start, clinic.timezone)[0], local_bounds(week_start + timedelta(days=7), clinic.timezone)[0]
    else: start, end = (utc(start) if start else None), (utc(end) if end else None)
    query = select(Appointment).join(Patient, Patient.id == Appointment.patient_id).where(Appointment.clinic_id == user.clinic_id).order_by(Appointment.starts_at)
    if start: query = query.where(Appointment.starts_at < end if end else True, Appointment.ends_at > start)
    elif end: query = query.where(Appointment.starts_at < end)
    if room_id: query = query.where(Appointment.room_id == room_id)
    if doctor_id: query = query.where(Appointment.doctor_id == doctor_id)
    if assistant_id: query = query.where(Appointment.assistant_id == assistant_id)
    if status_filter:
        if status_filter not in STATUSES: raise HTTPException(422, "Unknown appointment status")
        query = query.where(Appointment.status == status_filter)
    if appointment_type: query = query.where(Appointment.appointment_type.ilike(f"%{appointment_type.strip()}%"))
    if patient_search:
        term = f"%{patient_search.strip()}%"; query = query.where(or_(Patient.first_name.ilike(term), Patient.last_name.ilike(term), Patient.phone.ilike(term), Patient.email.ilike(term)))
    return [await appointment_with_names(db, item) for item in (await db.scalars(query)).all()]
@router.get("/{appointment_id}", response_model=AppointmentResponse)
async def get_appointment(appointment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id));
    if not item: raise HTTPException(404, "Appointment not found")
    return await appointment_with_names(db, item)
@router.patch("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(appointment_id: UUID, payload: AppointmentUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGE_APPOINTMENTS))):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id).with_for_update());
    if not item: raise HTTPException(404, "Appointment not found")
    previous = history_payload(item); data = payload.model_dump(exclude_unset=True); clinic, _ = await clinic_timezone(db, user.clinic_id); patient_id = data.get("patient_id", item.patient_id); doctor_id = data.get("doctor_id", item.doctor_id); assistant_id = data.get("assistant_id", item.assistant_id); room_id = data.get("room_id", item.room_id); duration = data.get("duration_minutes", item.duration_minutes); start = resolve_start(payload, clinic.timezone, item.starts_at)
    await refs(db, user.clinic_id, patient_id, doctor_id, assistant_id, room_id)
    if item.status in ACTIVE_STATUSES: await validate_slot(db, user.clinic_id, start, duration, doctor_id, room_id, assistant_id, item.id)
    for key in ("patient_id", "doctor_id", "assistant_id", "room_id", "duration_minutes", "appointment_type", "notes"):
        if key in data: setattr(item, key, data[key])
    item.starts_at = start; item.ends_at = appointment_end(start, duration); item.updated_at = utcnow(); add_history(db, item, user.id, "updated", previous); db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="updated"))
    try: await db.commit()
    except IntegrityError as exc: await db.rollback(); raise HTTPException(409, "The selected doctor, room, or assistant is already booked") from exc
    await db.refresh(item); return await appointment_with_names(db, item)
@router.patch("/{appointment_id}/status", response_model=AppointmentResponse)
async def update_status(appointment_id: UUID, payload: AppointmentStatus, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGE_APPOINTMENTS))):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id).with_for_update());
    if not item: raise HTTPException(404, "Appointment not found")
    if payload.status not in STATUSES: raise HTTPException(422, "Unknown appointment status")
    if payload.status != item.status and payload.status not in TRANSITIONS[item.status]: raise HTTPException(409, f"Invalid appointment transition: {item.status} -> {payload.status}")
    if payload.status in ACTIVE_STATUSES and item.status not in ACTIVE_STATUSES: await refs(db, user.clinic_id, item.patient_id, item.doctor_id, item.assistant_id, item.room_id); await validate_slot(db, user.clinic_id, utc(item.starts_at), item.duration_minutes, item.doctor_id, item.room_id, item.assistant_id, item.id)
    previous = history_payload(item); item.status = payload.status; add_history(db, item, user.id, "status_changed", previous); db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="status_changed"))
    try: await db.commit()
    except IntegrityError as exc: await db.rollback(); raise HTTPException(409, "The appointment cannot use this slot") from exc
    await db.refresh(item); return await appointment_with_names(db, item)
@router.post("/{appointment_id}/reschedule", response_model=AppointmentResponse)
async def reschedule(appointment_id: UUID, payload: RescheduleRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGE_APPOINTMENTS))):
    item = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id).with_for_update());
    if not item: raise HTTPException(404, "Appointment not found")
    if item.status not in ACTIVE_STATUSES: raise HTTPException(409, "Only active appointments can be rescheduled")
    clinic, _ = await clinic_timezone(db, user.clinic_id); previous = history_payload(item); start = resolve_start(payload, clinic.timezone, item.starts_at); duration = payload.duration_minutes or item.duration_minutes; doctor_id = payload.doctor_id or item.doctor_id; room_id = payload.room_id or item.room_id; assistant_id = payload.assistant_id if payload.assistant_id is not None else item.assistant_id
    await refs(db, user.clinic_id, item.patient_id, doctor_id, assistant_id, room_id); await validate_slot(db, user.clinic_id, start, duration, doctor_id, room_id, assistant_id, item.id); item.starts_at = start; item.ends_at = appointment_end(start, duration); item.duration_minutes = duration; item.doctor_id = doctor_id; item.room_id = room_id; item.assistant_id = assistant_id; item.updated_at = utcnow(); add_history(db, item, user.id, "rescheduled", previous, payload.reason); db.add(AuditEvent(clinic_id=user.clinic_id, actor_id=user.id, entity_type="appointment", entity_id=item.id, action="rescheduled"))
    try: await db.commit()
    except IntegrityError as exc: await db.rollback(); raise HTTPException(409, "The new appointment slot is unavailable") from exc
    await db.refresh(item); return await appointment_with_names(db, item)
@router.get("/{appointment_id}/history", response_model=list[AppointmentHistoryResponse])
async def appointment_history(appointment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))):
    if not await db.scalar(select(Appointment.id).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id)): raise HTTPException(404, "Appointment not found")
    return list((await db.scalars(select(AppointmentHistory).where(AppointmentHistory.appointment_id == appointment_id, AppointmentHistory.clinic_id == user.clinic_id).order_by(AppointmentHistory.occurred_at.desc()))).all())
@rooms_router.get("", response_model=list[RoomResponse])
async def list_rooms(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))): return (await db.scalars(select(Room).where(Room.clinic_id == user.clinic_id).order_by(Room.is_active.desc(), Room.name))).all()
@staff_router.get("/doctors", response_model=list[DoctorResponse])
async def list_doctors(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))): return (await db.scalars(select(User).where(User.clinic_id == user.clinic_id, User.role.in_(["doctor", "clinic_manager", "administrator"]), User.is_active.is_(True)).order_by(User.full_name))).all()
@availability_router.get("/working-hours", response_model=list[WorkingHoursResponse])
async def list_working_hours(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))): return list((await db.scalars(select(WorkingHours).where(WorkingHours.clinic_id == user.clinic_id).order_by(WorkingHours.day_of_week, WorkingHours.start_time))).all())
@availability_router.put("/working-hours", response_model=list[WorkingHoursResponse])
async def replace_working_hours(payload: list[WorkingHoursInput], db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await db.execute(WorkingHours.__table__.delete().where(WorkingHours.clinic_id == user.clinic_id)); rows = [WorkingHours(clinic_id=user.clinic_id, **item.model_dump()) for item in payload]; db.add_all(rows); await db.commit(); return list((await db.scalars(select(WorkingHours).where(WorkingHours.clinic_id == user.clinic_id).order_by(WorkingHours.day_of_week, WorkingHours.start_time))).all())
@availability_router.get("/unavailability", response_model=list[UnavailabilityResponse])
async def list_unavailability(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ALL_STAFF))): return list((await db.scalars(select(ResourceUnavailability).where(ResourceUnavailability.clinic_id == user.clinic_id).order_by(ResourceUnavailability.starts_at))).all())
@availability_router.post("/unavailability", response_model=UnavailabilityResponse, status_code=201)
async def create_unavailability(payload: UnavailabilityInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    valid = await db.scalar(select(User.id).where(User.id == payload.resource_id, User.clinic_id == user.clinic_id)) if payload.resource_type in {"doctor", "assistant"} else await db.scalar(select(Room.id).where(Room.id == payload.resource_id, Room.clinic_id == user.clinic_id))
    if not valid: raise HTTPException(422, "Resource is not valid for this clinic")
    item = ResourceUnavailability(clinic_id=user.clinic_id, created_by_id=user.id, **payload.model_dump()); db.add(item); await db.commit(); await db.refresh(item); return item
@availability_router.delete("/unavailability/{item_id}", status_code=204)
async def delete_unavailability(item_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    item = await db.scalar(select(ResourceUnavailability).where(ResourceUnavailability.id == item_id, ResourceUnavailability.clinic_id == user.clinic_id).with_for_update());
    if not item: raise HTTPException(404, "Unavailability not found")
    await db.delete(item); await db.commit()
