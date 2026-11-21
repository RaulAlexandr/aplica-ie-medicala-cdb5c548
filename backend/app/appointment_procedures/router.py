from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.appointment_procedures.effective import effective_procedures
from app.appointment_procedures.models import AppointmentProcedure, AppointmentProcedureRevision
from app.auth.service import STAFF_ROLES, require_roles
from app.database import Appointment, ProcedureCatalog, User, get_db, utcnow

router = APIRouter(prefix="/appointment-procedures", tags=["appointment_procedures"])
MANAGE_APPOINTMENTS = ("clinic_manager", "doctor", "reception", "administrator")
CLOSED = {"completed", "cancelled", "no_show"}


class DesiredLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure_catalog_id: UUID
    quantity: int


class ReplaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    procedures: list[DesiredLine]


async def _appointment(db: AsyncSession, clinic_id: UUID, appointment_id: UUID, *, lock: bool = False) -> Appointment:
    stmt = select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == clinic_id)
    if lock:
        stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
    item = await db.scalar(stmt)
    if item is None:
        raise HTTPException(404, "Programarea nu a fost găsită.")
    return item


async def _revision(db: AsyncSession, appointment: Appointment, lock: bool = False) -> AppointmentProcedureRevision:
    model = AppointmentProcedureRevision
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = pg_insert(model).values(id=uuid4(), clinic_id=appointment.clinic_id, appointment_id=appointment.id, revision=0, updated_at=utcnow()).on_conflict_do_nothing(index_elements=["appointment_id"])
    else:
        stmt = sqlite_insert(model).values(id=uuid4(), clinic_id=appointment.clinic_id, appointment_id=appointment.id, revision=0, updated_at=utcnow()).on_conflict_do_nothing(index_elements=["appointment_id"])
    await db.execute(stmt)
    stmt = select(model).where(model.appointment_id == appointment.id, model.clinic_id == appointment.clinic_id)
    if lock:
        stmt = stmt.with_for_update()
    row = await db.scalar(stmt)
    if row is None:
        raise HTTPException(404, "Programarea nu a fost găsită.")
    return row


@router.get("")
async def list_for_interval(start: datetime = Query(), end: datetime = Query(), db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise HTTPException(422, "Intervalul trebuie să conțină momente valide în UTC.")
    if (end - start).days > 92 or (end - start).total_seconds() > 92 * 86400:
        raise HTTPException(422, "Intervalul nu poate depăși 92 de zile.")
    appointments = (await db.scalars(select(Appointment).where(Appointment.clinic_id == user.clinic_id,
        Appointment.starts_at >= start, Appointment.starts_at < end, Appointment.status.notin_(("cancelled", "no_show"))))).all()
    ids = [a.id for a in appointments]
    effective = await effective_procedures(db, user.clinic_id, ids)
    revisions = (await db.scalars(select(AppointmentProcedureRevision).where(AppointmentProcedureRevision.clinic_id == user.clinic_id,
        AppointmentProcedureRevision.appointment_id.in_(ids)))).all() if ids else []
    revision_by_id = {row.appointment_id: row.revision for row in revisions}
    return [{"appointment_id": a.id, "revision": revision_by_id.get(a.id, 0), "procedures": effective[a.id]} for a in appointments]


@router.get("/{appointment_id}")
async def get_for_appointment(appointment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    appointment = await _appointment(db, user.clinic_id, appointment_id)
    rev = await db.scalar(select(AppointmentProcedureRevision).where(
        AppointmentProcedureRevision.appointment_id == appointment.id,
        AppointmentProcedureRevision.clinic_id == user.clinic_id))
    effective = await effective_procedures(db, user.clinic_id, [appointment.id])
    return {"appointment_id": appointment.id, "revision": rev.revision if rev else 0,
            "procedures": effective[appointment.id]}


@router.put("/{appointment_id}")
async def replace_for_appointment(appointment_id: UUID, payload: ReplaceInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGE_APPOINTMENTS))):
    appointment = await _appointment(db, user.clinic_id, appointment_id, lock=True)
    if appointment.status in CLOSED:
        raise HTTPException(409, "Programarea este închisă și procedurile nu mai pot fi modificate.")
    if payload.expected_revision < 0 or len(payload.procedures) > 100:
        raise HTTPException(422, "Revizia sau numărul de proceduri nu este valid.")
    if any(line.quantity < 1 or line.quantity > 32 for line in payload.procedures):
        raise HTTPException(422, "Cantitatea trebuie să fie între 1 și 32.")
    ids = [line.procedure_catalog_id for line in payload.procedures]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Procedura apare de mai multe ori în listă.")
    rev = await _revision(db, appointment, lock=True)
    if rev.revision != payload.expected_revision:
        raise HTTPException(409, "Lista a fost modificată între timp. Reîncărcați programarea.")
    active = (await db.scalars(select(AppointmentProcedure).where(AppointmentProcedure.clinic_id == user.clinic_id,
        AppointmentProcedure.appointment_id == appointment.id, AppointmentProcedure.removed_at.is_(None)))).all()
    current = {row.procedure_catalog_id: row.quantity for row in active}
    desired = {line.procedure_catalog_id: line.quantity for line in payload.procedures}
    if current == desired:
        await db.commit()
        return {"appointment_id": appointment.id, "revision": rev.revision,
                "procedures": (await effective_procedures(db, user.clinic_id, [appointment.id]))[appointment.id]}
    catalog = (await db.scalars(select(ProcedureCatalog).where(ProcedureCatalog.clinic_id == user.clinic_id,
        ProcedureCatalog.id.in_(set(desired) - set(current))))).all() if set(desired) - set(current) else []
    catalog_by_id = {item.id: item for item in catalog}
    missing = (set(desired) - set(current)) - set(catalog_by_id)
    if missing:
        raise HTTPException(404, "Procedura nu a fost găsită.")
    inactive = [item for item in catalog if not item.is_active]
    if inactive:
        raise HTTPException(422, "Nu se poate adăuga o procedură inactivă.")
    now = utcnow()
    for row in active:
        if desired.get(row.procedure_catalog_id) != row.quantity:
            row.removed_at = now
            row.removed_by_id = user.id
    await db.flush()
    for procedure_id, quantity in desired.items():
        if current.get(procedure_id) != quantity:
            db.add(AppointmentProcedure(clinic_id=user.clinic_id, appointment_id=appointment.id,
                procedure_catalog_id=procedure_id, quantity=quantity, added_by_id=user.id, added_at=now))
    rev.revision += 1
    rev.updated_at = now
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Lista a fost modificată între timp. Reîncărcați programarea.") from exc
    return {"appointment_id": appointment.id, "revision": rev.revision,
            "procedures": (await effective_procedures(db, user.clinic_id, [appointment.id]))[appointment.id]}


@router.get("/{appointment_id}/history")
async def history(appointment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))):
    await _appointment(db, user.clinic_id, appointment_id)
    AddedUser, RemovedUser = aliased(User), aliased(User)
    rows = (await db.execute(select(AppointmentProcedure, ProcedureCatalog, AddedUser.full_name, RemovedUser.full_name)
        .join(ProcedureCatalog, ProcedureCatalog.id == AppointmentProcedure.procedure_catalog_id)
        .join(AddedUser, AddedUser.id == AppointmentProcedure.added_by_id)
        .outerjoin(RemovedUser, RemovedUser.id == AppointmentProcedure.removed_by_id)
        .where(AppointmentProcedure.clinic_id == user.clinic_id, AppointmentProcedure.appointment_id == appointment_id,
               ProcedureCatalog.clinic_id == user.clinic_id)
        .order_by(AppointmentProcedure.added_at, AppointmentProcedure.id))).all()
    return [{"id": row.id, "procedure_catalog_id": procedure.id, "code": procedure.code, "name": procedure.name,
             "quantity": row.quantity, "added_at": row.added_at, "added_by": added_by,
             "removed_at": row.removed_at, "removed_by": removed_by}
            for row, procedure, added_by, removed_by in rows]
