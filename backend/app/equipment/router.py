import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    Clinic,
    Patient,
    PerformedProcedure,
    ProcedureCatalog,
    Room,
    TreatmentPlanItem,
    User,
    get_db,
    utcnow,
)
from app.equipment.models import (
    Equipment,
    EquipmentCheck,
    EquipmentDependency,
    EquipmentDocument,
    EquipmentUnavailability,
)

router = APIRouter(prefix="/equipment", tags=["equipment"])
MANAGERS = ("clinic_manager", "administrator")
STAFF = (*MANAGERS, "doctor", "assistant")
READERS = (*STAFF, "reception")
ACTIVE_APPOINTMENT_STATUSES = ("scheduled", "confirmed", "arrived", "in_progress")


def effective_end(outage: EquipmentUnavailability) -> datetime:
    return min(outage.ends_at, outage.closed_at) if outage.closed_at else outage.ends_at


def normalize_service_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().strip())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


class EquipmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    equipment_type: str = Field(min_length=1, max_length=100)
    serial_number: str | None = Field(default=None, max_length=120)
    room_id: UUID | None = None
    responsible_id: UUID
    state: str = "available"
    is_active: bool = True

    @field_validator("name", "equipment_type", mode="before")
    @classmethod
    def nonblank(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Valoarea este obligatorie")
        return value.strip()


class CheckInput(BaseModel):
    check_type: str
    due_at: datetime
    completed_at: datetime | None = None
    result: str | None = Field(default=None, max_length=2000)


class CheckCompletionInput(BaseModel):
    result: str = Field(min_length=1, max_length=2000)

    @field_validator("result", mode="before")
    @classmethod
    def result_not_blank(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Rezultatul este obligatoriu")
        return value.strip()


class OutageInput(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    starts_at: datetime
    ends_at: datetime

    @field_validator("reason", mode="before")
    @classmethod
    def reason_not_blank(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Motivul este obligatoriu")
        return value.strip()


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    document_type: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class DependencyInput(BaseModel):
    room_id: UUID | None = None
    procedure_id: UUID | None = None


def _json(row):
    return {key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.__dict__.items() if not key.startswith("_")}


async def _equipment(db: AsyncSession, user: User, equipment_id: UUID, lock: bool = False) -> Equipment:
    query = select(Equipment).where(Equipment.id == equipment_id, Equipment.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    value = await db.scalar(query)
    if value is None:
        raise HTTPException(404, "Echipament inexistent")
    return value


async def _clinic_datetime(db: AsyncSession, clinic_id: UUID, value: datetime) -> datetime:
    if value.tzinfo is None:
        clinic = await db.get(Clinic, clinic_id)
        if not clinic:
            raise HTTPException(404, "Clinică inexistentă")
        value = value.replace(tzinfo=ZoneInfo(clinic.timezone))
    return value.astimezone(UTC)


async def _affected(db: AsyncSession, user: User, equipment: Equipment, outage: EquipmentUnavailability):
    outage_end = effective_end(outage)
    if outage_end <= outage.starts_at:
        return []
    deps = list(await db.scalars(select(EquipmentDependency).where(
        EquipmentDependency.clinic_id == user.clinic_id, EquipmentDependency.equipment_id == equipment.id)))
    room_ids = {d.room_id for d in deps if d.room_id}
    if equipment.room_id:
        room_ids.add(equipment.room_id)
    service_ids = {d.procedure_id for d in deps if d.procedure_id}
    service_terms = set()
    if service_ids:
        services = list((await db.execute(select(ProcedureCatalog.name, ProcedureCatalog.code).where(
            ProcedureCatalog.clinic_id == user.clinic_id, ProcedureCatalog.id.in_(service_ids)))).all())
        service_terms = {normalize_service_text(value) for row in services for value in row if value.strip()}
    if not room_ids and not service_ids:
        return []
    sure_appointment_ids = set()
    if service_ids:
        sure_appointment_ids.update((await db.scalars(select(PerformedProcedure.appointment_id).where(
            PerformedProcedure.clinic_id == user.clinic_id,
            PerformedProcedure.procedure_catalog_id.in_(service_ids),
            PerformedProcedure.status != "cancelled"))).all())
        sure_appointment_ids.update((await db.scalars(select(TreatmentPlanItem.appointment_id).where(
            TreatmentPlanItem.clinic_id == user.clinic_id,
            TreatmentPlanItem.procedure_catalog_id.in_(service_ids),
            TreatmentPlanItem.status == "active", TreatmentPlanItem.appointment_id.is_not(None)))).all())
    query = (select(Appointment, Patient, User, Room).join(Patient, and_(Patient.id == Appointment.patient_id, Patient.clinic_id == user.clinic_id))
             .join(User, and_(User.id == Appointment.doctor_id, User.clinic_id == user.clinic_id))
             .join(Room, and_(Room.id == Appointment.room_id, Room.clinic_id == user.clinic_id))
             .where(Appointment.clinic_id == user.clinic_id, Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES),
                    Appointment.starts_at < outage_end)
             .order_by(Appointment.starts_at))
    rows = (await db.execute(query)).all()
    responsible = await db.get(User, equipment.responsible_id)
    affected = []
    for appointment, patient, doctor, room in rows:
        end = appointment.ends_at or appointment.starts_at + timedelta(minutes=appointment.duration_minutes)
        if end <= outage.starts_at or appointment.starts_at >= outage_end:
            continue
        match = "sigur" if room.id in room_ids or appointment.id in sure_appointment_ids else "posibil"
        if match == "posibil":
            appointment_text = normalize_service_text(appointment.appointment_type)
            if not any(term and term in appointment_text for term in service_terms):
                continue
        affected.append({"appointment_id": str(appointment.id), "patient": f"{patient.first_name} {patient.last_name}",
            "starts_at": appointment.starts_at.isoformat(), "ends_at": end.isoformat(),
            "doctor": doctor.full_name, "room": room.name, "appointment_type": appointment.appointment_type,
            "responsible": responsible.full_name if responsible else "—", "match": match})
    return affected


@router.get("")
async def list_equipment(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    rows = list(await db.scalars(select(Equipment).where(Equipment.clinic_id == user.clinic_id).order_by(Equipment.name)))
    now = utcnow()
    open_equipment_ids = set((await db.scalars(select(EquipmentUnavailability.equipment_id).where(
        EquipmentUnavailability.clinic_id == user.clinic_id,
        EquipmentUnavailability.closed_at.is_(None),
        EquipmentUnavailability.starts_at <= now,
        EquipmentUnavailability.ends_at > now))).all())
    return [{**_json(row), "missing_unavailability": row.state in {"broken", "maintenance"}
             and row.id not in open_equipment_ids} for row in rows]


@router.post("", status_code=201)
async def create_equipment(payload: EquipmentInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    if payload.state not in {"available", "maintenance", "broken", "retired"}:
        raise HTTPException(422, "Stare neacceptată")
    if payload.room_id and not await db.scalar(select(Room.id).where(Room.id == payload.room_id, Room.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Cabinet inexistent")
    if not await db.scalar(select(User.id).where(User.id == payload.responsible_id, User.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Responsabil inexistent")
    value = Equipment(clinic_id=user.clinic_id, created_by_id=user.id, **payload.model_dump())
    db.add(value); await db.commit(); await db.refresh(value)
    return _json(value)


@router.patch("/{equipment_id}")
async def update_equipment(equipment_id: UUID, payload: EquipmentInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    value = await _equipment(db, user, equipment_id)
    if payload.state not in {"available", "maintenance", "broken", "retired"}:
        raise HTTPException(422, "Stare neacceptată")
    if payload.room_id and not await db.scalar(select(Room.id).where(Room.id == payload.room_id, Room.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Cabinet inexistent")
    if not await db.scalar(select(User.id).where(User.id == payload.responsible_id, User.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Responsabil inexistent")
    for key, item in payload.model_dump().items():
        setattr(value, key, item)
    value.updated_at = utcnow(); await db.commit(); await db.refresh(value)
    now = utcnow()
    open_outage_id = await db.scalar(select(EquipmentUnavailability.id).where(
        EquipmentUnavailability.clinic_id == user.clinic_id,
        EquipmentUnavailability.equipment_id == value.id,
        EquipmentUnavailability.closed_at.is_(None),
        EquipmentUnavailability.starts_at <= now,
        EquipmentUnavailability.ends_at > now).order_by(EquipmentUnavailability.created_at.desc()))
    return {**_json(value), "open_unavailability_id": str(open_outage_id) if open_outage_id else None,
            "unavailability_required": value.state in {"broken", "maintenance"} and open_outage_id is None}


@router.get("/{equipment_id}/history")
async def equipment_history(equipment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    await _equipment(db, user, equipment_id)
    checks = list(await db.scalars(select(EquipmentCheck).where(EquipmentCheck.clinic_id == user.clinic_id, EquipmentCheck.equipment_id == equipment_id).order_by(EquipmentCheck.due_at)))
    documents = list(await db.scalars(select(EquipmentDocument).where(EquipmentDocument.clinic_id == user.clinic_id, EquipmentDocument.equipment_id == equipment_id).order_by(EquipmentDocument.added_at)))
    outages = list(await db.scalars(select(EquipmentUnavailability).where(EquipmentUnavailability.clinic_id == user.clinic_id, EquipmentUnavailability.equipment_id == equipment_id).order_by(EquipmentUnavailability.starts_at)))
    dependencies = list(await db.scalars(select(EquipmentDependency).where(EquipmentDependency.clinic_id == user.clinic_id, EquipmentDependency.equipment_id == equipment_id)))
    return {"checks": [_json(x) for x in checks], "documents": [_json(x) for x in documents],
            "unavailabilities": [{**_json(x), "affected_appointments": await _affected(db, user, await _equipment(db, user, equipment_id), x)} for x in outages],
            "dependencies": [_json(x) for x in dependencies]}


@router.post("/{equipment_id}/checks", status_code=201)
async def add_check(equipment_id: UUID, payload: CheckInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    equipment = await _equipment(db, user, equipment_id)
    if payload.check_type not in {"service", "inspection"}:
        raise HTTPException(422, "Tipul verificării nu este acceptat")
    result = payload.result.strip() if payload.result is not None else None
    if payload.result is not None and not result:
        raise HTTPException(422, "Rezultatul nu poate fi gol")
    due = await _clinic_datetime(db, user.clinic_id, payload.due_at)
    completed = await _clinic_datetime(db, user.clinic_id, payload.completed_at) if payload.completed_at else None
    if completed is not None:
        if completed > utcnow():
            raise HTTPException(422, "Momentul efectuării nu poate fi în viitor")
        if result is None:
            raise HTTPException(422, "Rezultatul este obligatoriu pentru o verificare efectuată")
    elif result is not None:
        raise HTTPException(422, "Rezultatul se completează numai pentru o verificare efectuată")
    value = EquipmentCheck(clinic_id=user.clinic_id, equipment_id=equipment.id, author_id=user.id,
        check_type=payload.check_type, due_at=due, completed_at=completed,
        completed_by_id=user.id if completed else None, result=result)
    db.add(value); await db.commit(); await db.refresh(value)
    return _json(value)


@router.post("/{equipment_id}/checks/{check_id}/complete")
async def complete_check(equipment_id: UUID, check_id: UUID, payload: CheckCompletionInput,
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await _equipment(db, user, equipment_id)
    check = await db.scalar(select(EquipmentCheck).where(
        EquipmentCheck.id == check_id, EquipmentCheck.equipment_id == equipment_id,
        EquipmentCheck.clinic_id == user.clinic_id).with_for_update())
    if check is None:
        raise HTTPException(404, "Verificarea nu există")
    if check.completed_at is not None or check.completed_by_id is not None:
        raise HTTPException(409, "Verificarea este deja efectuată")
    check.completed_at = utcnow()
    check.completed_by_id = user.id
    check.result = payload.result
    await db.commit()
    await db.refresh(check)
    return _json(check)


@router.get("/checks/due")
async def due_checks(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    now = utcnow()
    rows = (await db.execute(select(EquipmentCheck, Equipment).join(Equipment, and_(Equipment.id == EquipmentCheck.equipment_id,
        Equipment.clinic_id == user.clinic_id)).where(EquipmentCheck.clinic_id == user.clinic_id,
        EquipmentCheck.completed_at.is_(None), EquipmentCheck.due_at <= now + timedelta(days=30)).order_by(EquipmentCheck.due_at))).all()
    return [{**_json(row), "equipment_name": equipment.name, "overdue": row.due_at < now} for row, equipment in rows]


@router.post("/{equipment_id}/documents", status_code=201)
async def add_document(equipment_id: UUID, payload: DocumentInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await _equipment(db, user, equipment_id)
    row = EquipmentDocument(clinic_id=user.clinic_id, equipment_id=equipment_id, added_by_id=user.id, **payload.model_dump())
    db.add(row); await db.commit(); await db.refresh(row)
    return _json(row)


@router.post("/{equipment_id}/dependencies", status_code=201)
async def add_dependency(equipment_id: UUID, payload: DependencyInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await _equipment(db, user, equipment_id)
    if (payload.room_id is None) == (payload.procedure_id is None):
        raise HTTPException(422, "Alege cabinetul sau serviciul, nu ambele")
    if payload.room_id and not await db.scalar(select(Room.id).where(Room.id == payload.room_id, Room.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Cabinet inexistent")
    if payload.procedure_id and not await db.scalar(select(ProcedureCatalog.id).where(ProcedureCatalog.id == payload.procedure_id, ProcedureCatalog.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Serviciu inexistent")
    row = EquipmentDependency(clinic_id=user.clinic_id, equipment_id=equipment_id, created_by_id=user.id, **payload.model_dump())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback(); raise HTTPException(409, "Dependența există deja")
    await db.refresh(row); return _json(row)


@router.post("/{equipment_id}/unavailabilities", status_code=201)
async def report_unavailability(equipment_id: UUID, payload: OutageInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF))):
    equipment = await _equipment(db, user, equipment_id)
    starts = await _clinic_datetime(db, user.clinic_id, payload.starts_at)
    ends = await _clinic_datetime(db, user.clinic_id, payload.ends_at)
    if ends <= starts:
        raise HTTPException(422, "Sfârșitul trebuie să fie după început")
    row = EquipmentUnavailability(clinic_id=user.clinic_id, equipment_id=equipment.id, reported_by_id=user.id,
        reason=payload.reason.strip(), starts_at=starts, ends_at=ends)
    db.add(row); await db.commit(); await db.refresh(row)
    return {**_json(row), "affected_appointments": await _affected(db, user, equipment, row)}


@router.post("/{equipment_id}/unavailabilities/{outage_id}/close")
async def close_unavailability(equipment_id: UUID, outage_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    await _equipment(db, user, equipment_id)
    row = await db.scalar(select(EquipmentUnavailability).where(EquipmentUnavailability.id == outage_id,
        EquipmentUnavailability.equipment_id == equipment_id, EquipmentUnavailability.clinic_id == user.clinic_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Indisponibilitate inexistentă")
    if row.closed_at:
        raise HTTPException(409, "Indisponibilitatea este deja închisă")
    row.closed_at = utcnow(); row.closed_by_id = user.id
    await db.commit(); return _json(row)


@router.get("/{equipment_id}/unavailabilities/{outage_id}/affected")
async def affected_appointments(equipment_id: UUID, outage_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    equipment = await _equipment(db, user, equipment_id)
    outage = await db.scalar(select(EquipmentUnavailability).where(EquipmentUnavailability.id == outage_id,
        EquipmentUnavailability.equipment_id == equipment_id, EquipmentUnavailability.clinic_id == user.clinic_id))
    if not outage:
        raise HTTPException(404, "Indisponibilitate inexistentă")
    return await _affected(db, user, equipment, outage)
