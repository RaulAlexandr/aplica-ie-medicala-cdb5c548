from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    Appointment,
    AppointmentHistory,
    Clinic,
    ResourceUnavailability,
    WorkingHours,
)

ACTIVE_STATUSES = {"scheduled", "confirmed", "arrived", "in_progress", "completed"}
RESOURCE_LABELS = {"doctor": "doctor", "assistant": "assistant", "room": "room"}


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def week_bounds(day: date, timezone: str) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=7)


def _roundtrip(local: datetime, zone: ZoneInfo, fold: int) -> datetime:
    return local.replace(tzinfo=zone, fold=fold).astimezone(UTC).astimezone(zone).replace(tzinfo=None)


def local_to_utc(value: datetime, timezone: str, fold: int | None = None) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    zone = ZoneInfo(timezone)
    matches = [_roundtrip(value, zone, candidate) == value for candidate in (0, 1)]
    if not any(matches):
        raise HTTPException(422, "This local time does not exist because of the daylight-saving transition")
    ambiguous = matches[0] and matches[1] and zone.utcoffset(value.replace(tzinfo=zone, fold=0)) != zone.utcoffset(value.replace(tzinfo=zone, fold=1))
    if ambiguous and fold not in (0, 1):
        raise HTTPException(422, "This local time is ambiguous; select which occurrence you mean")
    selected = fold if ambiguous else 0
    return value.replace(tzinfo=zone, fold=selected).astimezone(UTC)


def appointment_end(start: datetime, duration_minutes: int) -> datetime:
    return start + timedelta(minutes=duration_minutes)


async def clinic_timezone(db: AsyncSession, clinic_id: UUID) -> tuple[Clinic, ZoneInfo]:
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(404, "Clinic not found")
    try:
        return clinic, ZoneInfo(clinic.timezone)
    except Exception as exc:
        raise HTTPException(500, "Clinic timezone is invalid; ask a manager to correct it") from exc


async def validate_working_hours(db: AsyncSession, clinic_id: UUID, start: datetime, end: datetime) -> None:
    _clinic, zone = await clinic_timezone(db, clinic_id)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    if local_start.date() != local_end.date():
        raise HTTPException(422, "Appointments must fit within one clinic-local working day")
    configured_rows = (await db.scalars(select(WorkingHours).where(WorkingHours.clinic_id == clinic_id, WorkingHours.is_active.is_(True)))).all()
    if not configured_rows:
        return
    rows = [row for row in configured_rows if row.day_of_week == local_start.weekday()]
    if not rows:
        raise HTTPException(422, "The clinic is closed on the selected day")
    start_time = local_start.timetz().replace(tzinfo=None)
    end_time = local_end.timetz().replace(tzinfo=None)
    if not any(row.start_time <= start_time and row.end_time >= end_time for row in rows):
        raise HTTPException(422, "The selected time falls outside the clinic’s working hours")


async def validate_unavailability(db: AsyncSession, clinic_id: UUID, start: datetime, end: datetime, doctor_id: UUID, assistant_id: UUID | None, room_id: UUID) -> None:
    resource_ids = [("doctor", doctor_id), ("room", room_id)]
    if assistant_id:
        resource_ids.append(("assistant", assistant_id))
    conditions = [and_(ResourceUnavailability.resource_type == kind, ResourceUnavailability.resource_id == resource_id) for kind, resource_id in resource_ids]
    if not conditions:
        return
    blocked = (await db.scalars(select(ResourceUnavailability).where(ResourceUnavailability.clinic_id == clinic_id, or_(*conditions), ResourceUnavailability.starts_at < end, ResourceUnavailability.ends_at > start))).all()
    if blocked:
        resource = RESOURCE_LABELS[blocked[0].resource_type]
        raise HTTPException(422, f"The assigned {resource} is unavailable during the selected time")


async def validate_availability(db: AsyncSession, clinic_id: UUID, start: datetime, end: datetime, doctor_id: UUID, assistant_id: UUID | None, room_id: UUID) -> None:
    await db.execute(select(Clinic.id).where(Clinic.id == clinic_id).with_for_update())
    await validate_working_hours(db, clinic_id, start, end)
    await validate_unavailability(db, clinic_id, start, end, doctor_id, assistant_id, room_id)


async def check_conflicts(db: AsyncSession, clinic_id: UUID, start: datetime, end: datetime, doctor_id: UUID, room_id: UUID, assistant_id: UUID | None, exclude_id: UUID | None = None) -> None:
    query = select(Appointment).where(Appointment.clinic_id == clinic_id, Appointment.status.in_(ACTIVE_STATUSES), Appointment.starts_at < end, Appointment.ends_at > start)
    if exclude_id:
        query = query.where(Appointment.id != exclude_id)
    for item in (await db.scalars(query)).all():
        if item.doctor_id == doctor_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This doctor is already booked during the selected time")
        if item.room_id == room_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This treatment room is already booked during the selected time")
        if assistant_id and item.assistant_id == assistant_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "The assigned assistant is already booked during the selected time")


def history_payload(item: Appointment) -> dict[str, Any]:
    return {
        "patient_id": str(item.patient_id), "doctor_id": str(item.doctor_id), "assistant_id": str(item.assistant_id) if item.assistant_id else None,
        "room_id": str(item.room_id), "starts_at": utc(item.starts_at).isoformat(), "ends_at": utc(item.ends_at).isoformat(),
        "duration_minutes": item.duration_minutes, "appointment_type": item.appointment_type, "status": item.status, "notes": item.notes,
    }


def add_history(db: AsyncSession, item: Appointment, actor_id: UUID, action: str, previous: dict[str, Any] | None = None, reason: str | None = None) -> None:
    details: dict[str, Any] = {"previous": previous} if previous else {}
    if reason:
        details["reason"] = reason
    db.add(AppointmentHistory(clinic_id=item.clinic_id, appointment_id=item.id, actor_id=actor_id, action=action, details=details))
