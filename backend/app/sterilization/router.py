"""T23 sterilization: sets, packs, cycles, approval, use, recall and reverse traceability.

Traceability comes from approved cycles and an append-only pack ledger, never from a checklist tick.
A pack's state is derived from its latest ledger event; nothing clinical is rewritten (corrections are new rows).
"""
import csv
import io
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.service import require_roles
from app.database import Appointment, Patient, PerformedProcedure, User, get_db, utcnow
from app.equipment.models import Equipment, EquipmentUnavailability
from app.sterilization.models import (
    SterilizationCycle,
    SterilizationCyclePack,
    SterilizationIndicatorResult,
    SterilizationPack,
    SterilizationPackEvent,
    SterilizationRecall,
    SterilizationSetType,
)

router = APIRouter(prefix="/sterilization", tags=["sterilization"])
MANAGERS = ("clinic_manager", "administrator")
WRITERS = (*MANAGERS, "assistant")
READERS = (*WRITERS, "doctor")  # reception sees nothing from T23
STERILIZER_TYPES = {"sterilizator", "autoclav"}
SAFE_PACK_STATES = {"unsterile", "used", "expired", "compromised"}  # may enter a (re)processing cycle
CLOCK_SKEW = timedelta(minutes=5)
LIST_LIMIT = 500


def _key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", " ".join(value.split()).casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Valoarea este obligatorie")
    return value.strip()


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _aware(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(422, f"{field} trebuie să includă fusul orar")
    return value.astimezone(UTC)


# ---------------------------------------------------------------- schemas
class SetTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    expiry_days: int = Field(default=30, ge=1, le=365)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _name_not_blank(cls, value):
        return _text(value)


class PackIn(BaseModel):
    set_type_id: UUID
    code: str = Field(min_length=1, max_length=60)

    @field_validator("code", mode="before")
    @classmethod
    def _code_not_blank(cls, value):
        return _text(value)


class CycleIn(BaseModel):
    equipment_id: UUID
    program: str = Field(min_length=1, max_length=120)
    pack_ids: list[UUID] = Field(min_length=1, max_length=200)
    cycle_number: int | None = Field(default=None, ge=1, le=10_000_000)
    operator_id: UUID | None = None
    started_at: datetime | None = None

    @field_validator("program", mode="before")
    @classmethod
    def _program_not_blank(cls, value):
        return _text(value)

    @field_validator("pack_ids")
    @classmethod
    def unique_packs(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Un pachet nu poate apărea de două ori în ciclu")
        return value


class FinishIn(BaseModel):
    temperature_c: Decimal = Field(gt=0, le=200, max_digits=5, decimal_places=1)
    pressure_kpa: Decimal = Field(gt=0, le=1000, max_digits=7, decimal_places=1)
    hold_minutes: int = Field(ge=1, le=600)
    chemical_result: str = Field(pattern="^(pending|pass|fail)$")
    biological_result: str = Field(default="pending", pattern="^(pending|pass|fail)$")
    ended_at: datetime | None = None


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=2000)


class IndicatorIn(BaseModel):
    kind: str = Field(pattern="^(chemical|biological)$")
    result: str = Field(pattern="^(pending|pass|fail)$")
    note: str | None = Field(default=None, max_length=2000)


class UseIn(BaseModel):
    appointment_id: UUID
    performed_procedure_id: UUID | None = None


# ---------------------------------------------------------------- helpers
def derive_state(events: list[SterilizationPackEvent], now: datetime) -> str:
    if not events:
        return "unsterile"
    last = events[-1]
    kind = last.event_type
    if kind == "entered_cycle":
        return "in_cycle"
    if kind == "sterilized":
        return "expired" if last.expires_at is not None and last.expires_at <= now else "sterile"
    if kind == "cycle_rejected":
        return "unsterile"
    return kind  # used, compromised


REFUSAL = {
    "unsterile": "Pachetul este nesterilizat",
    "in_cycle": "Pachetul este încă într-un ciclu de sterilizare",
    "used": "Pachetul a fost deja folosit",
    "expired": "Pachetul a expirat",
    "compromised": "Pachetul este compromis (ciclu rechemat)",
}


async def _events_by_pack(db: AsyncSession, clinic_id: UUID, pack_ids) -> dict[UUID, list[SterilizationPackEvent]]:
    result: dict[UUID, list[SterilizationPackEvent]] = {}
    if not pack_ids:
        return result
    rows = await db.scalars(select(SterilizationPackEvent).where(
        SterilizationPackEvent.clinic_id == clinic_id, SterilizationPackEvent.pack_id.in_(list(pack_ids)))
        .order_by(SterilizationPackEvent.created_at, SterilizationPackEvent.id))
    for row in rows:
        result.setdefault(row.pack_id, []).append(row)
    return result


async def _cycle(db: AsyncSession, user: User, cycle_id: UUID, lock: bool = False) -> SterilizationCycle:
    query = select(SterilizationCycle).where(SterilizationCycle.id == cycle_id, SterilizationCycle.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    value = await db.scalar(query)
    if value is None:
        raise HTTPException(404, "Ciclu inexistent")
    return value


async def _cycle_packs(db: AsyncSession, cycle: SterilizationCycle, lock: bool = False):
    query = (select(SterilizationPack).join(SterilizationCyclePack, SterilizationCyclePack.pack_id == SterilizationPack.id)
             .where(SterilizationCyclePack.cycle_id == cycle.id, SterilizationPack.clinic_id == cycle.clinic_id)
             .order_by(SterilizationPack.id))
    if lock:
        query = query.with_for_update()
    return list(await db.scalars(query))


async def _equipment_unavailable_reason(db: AsyncSession, equipment: Equipment, at: datetime) -> str | None:
    if not equipment.is_active:
        return "Aparatul este dezactivat"
    if equipment.state != "available":
        return f"Aparatul este indisponibil (stare: {equipment.state})"
    outage = await db.scalar(select(EquipmentUnavailability).where(
        EquipmentUnavailability.clinic_id == equipment.clinic_id, EquipmentUnavailability.equipment_id == equipment.id,
        EquipmentUnavailability.starts_at <= at, EquipmentUnavailability.ends_at > at,
        (EquipmentUnavailability.closed_at.is_(None)) | (EquipmentUnavailability.closed_at > at)).limit(1))
    if outage:
        return f"Aparatul este indisponibil: {outage.reason}"
    return None


async def _cycle_views(db: AsyncSession, clinic_id: UUID, cycles: list[SterilizationCycle]) -> list[dict]:
    """Constant number of queries for any list of cycles."""
    if not cycles:
        return []
    ids = [c.id for c in cycles]
    names = {row.id: row.name for row in await db.execute(select(Equipment.id, Equipment.name).where(
        Equipment.clinic_id == clinic_id, Equipment.id.in_({c.equipment_id for c in cycles})))}
    user_ids = {c.operator_id for c in cycles} | {c.decided_by_id for c in cycles if c.decided_by_id}
    users = {row.id: row.full_name for row in await db.execute(select(User.id, User.full_name).where(
        User.clinic_id == clinic_id, User.id.in_(user_ids)))}
    indicators: dict[UUID, dict[str, str]] = {}
    for row in await db.scalars(select(SterilizationIndicatorResult).where(
            SterilizationIndicatorResult.clinic_id == clinic_id, SterilizationIndicatorResult.cycle_id.in_(ids))
            .order_by(SterilizationIndicatorResult.recorded_at, SterilizationIndicatorResult.id)):
        indicators.setdefault(row.cycle_id, {})[row.kind] = row.result
    packs: dict[UUID, list[str]] = {}
    for cycle_id, code in await db.execute(select(SterilizationCyclePack.cycle_id, SterilizationPack.code)
            .join(SterilizationPack, SterilizationPack.id == SterilizationCyclePack.pack_id)
            .where(SterilizationCyclePack.clinic_id == clinic_id, SterilizationCyclePack.cycle_id.in_(ids))
            .order_by(SterilizationPack.code)):
        packs.setdefault(cycle_id, []).append(code)
    recalled = {row.cycle_id: row for row in await db.scalars(select(SterilizationRecall).where(
        SterilizationRecall.clinic_id == clinic_id, SterilizationRecall.cycle_id.in_(ids)))}
    views = []
    for c in cycles:
        current = indicators.get(c.id, {})
        views.append({
            "id": str(c.id), "equipment_id": str(c.equipment_id), "equipment_name": names.get(c.equipment_id),
            "cycle_number": c.cycle_number, "program": c.program, "started_at": _iso(c.started_at), "ended_at": _iso(c.ended_at),
            "operator_id": str(c.operator_id), "operator_name": users.get(c.operator_id),
            "temperature_c": str(c.temperature_c) if c.temperature_c is not None else None,
            "pressure_kpa": str(c.pressure_kpa) if c.pressure_kpa is not None else None, "hold_minutes": c.hold_minutes,
            "status": c.status, "decided_by_id": str(c.decided_by_id) if c.decided_by_id else None,
            "decided_by_name": users.get(c.decided_by_id), "decided_at": _iso(c.decided_at), "decision_note": c.decision_note,
            "chemical_result": current.get("chemical"), "biological_result": current.get("biological"),
            "compromised": c.id in recalled, "recall_reason": recalled[c.id].reason if c.id in recalled else None,
            "pack_codes": packs.get(c.id, []), "pack_count": len(packs.get(c.id, [])),
        })
    return views


def _event(user: User, pack: SterilizationPack, event_type: str, **extra) -> SterilizationPackEvent:
    return SterilizationPackEvent(clinic_id=user.clinic_id, pack_id=pack.id, event_type=event_type, actor_id=user.id, **extra)


# ---------------------------------------------------------------- set types
def _set_type_json(row: SterilizationSetType) -> dict:
    return {"id": str(row.id), "name": row.name, "description": row.description, "expiry_days": row.expiry_days,
            "is_active": row.is_active, "created_at": _iso(row.created_at)}


@router.get("/set-types")
async def list_set_types(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    rows = await db.scalars(select(SterilizationSetType).where(SterilizationSetType.clinic_id == user.clinic_id).order_by(SterilizationSetType.name_key))
    return [_set_type_json(row) for row in rows]


@router.post("/set-types", status_code=201)
async def create_set_type(payload: SetTypeIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    row = SterilizationSetType(clinic_id=user.clinic_id, created_by_id=user.id, name=payload.name, name_key=_key(payload.name),
                               description=payload.description, expiry_days=payload.expiry_days, is_active=payload.is_active)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Există deja un tip de set cu acest nume")
    return _set_type_json(row)


@router.patch("/set-types/{set_type_id}")
async def update_set_type(set_type_id: UUID, payload: SetTypeIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    row = await db.scalar(select(SterilizationSetType).where(SterilizationSetType.id == set_type_id, SterilizationSetType.clinic_id == user.clinic_id))
    if row is None:
        raise HTTPException(404, "Tip de set inexistent")
    row.name, row.name_key, row.description = payload.name, _key(payload.name), payload.description
    row.expiry_days, row.is_active = payload.expiry_days, payload.is_active  # affects only future approvals
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Există deja un tip de set cu acest nume")
    return _set_type_json(row)


# ---------------------------------------------------------------- packs
async def _pack_views(db: AsyncSession, clinic_id: UUID, packs: list[tuple[SterilizationPack, str]], now: datetime) -> list[dict]:
    events = await _events_by_pack(db, clinic_id, [pack.id for pack, _ in packs])
    cycle_ids = {ev[-1].cycle_id for ev in events.values() if ev[-1].cycle_id}
    cycles = {}
    if cycle_ids:
        cycles = {row.id: row for row in await db.execute(select(SterilizationCycle.id, SterilizationCycle.cycle_number, Equipment.name.label("equipment_name"))
            .join(Equipment, Equipment.id == SterilizationCycle.equipment_id)
            .where(SterilizationCycle.clinic_id == clinic_id, SterilizationCycle.id.in_(cycle_ids)))}
    views = []
    for pack, set_name in packs:
        history = events.get(pack.id, [])
        last = history[-1] if history else None
        cycle = cycles.get(last.cycle_id) if last and last.cycle_id else None
        views.append({
            "id": str(pack.id), "code": pack.code, "set_type_id": str(pack.set_type_id), "set_type_name": set_name,
            "state": derive_state(history, now), "expires_at": _iso(last.expires_at) if last and last.event_type == "sterilized" else None,
            "cycle_id": str(last.cycle_id) if last and last.cycle_id else None,
            "cycle_number": cycle.cycle_number if cycle else None, "equipment_name": cycle.equipment_name if cycle else None,
            "last_event_at": _iso(last.created_at) if last else None, "created_at": _iso(pack.created_at)})
    return views


@router.get("/packs")
async def list_packs(state: str | None = Query(default=None), db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    rows = (await db.execute(select(SterilizationPack, SterilizationSetType.name).join(SterilizationSetType, SterilizationSetType.id == SterilizationPack.set_type_id)
            .where(SterilizationPack.clinic_id == user.clinic_id).order_by(SterilizationPack.code_key).limit(LIST_LIMIT))).all()
    views = await _pack_views(db, user.clinic_id, [(pack, name) for pack, name in rows], utcnow())
    return [v for v in views if state is None or v["state"] == state]


@router.post("/packs", status_code=201)
async def create_pack(payload: PackIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    set_type = await db.scalar(select(SterilizationSetType).where(SterilizationSetType.id == payload.set_type_id, SterilizationSetType.clinic_id == user.clinic_id))
    if set_type is None:
        raise HTTPException(404, "Tip de set inexistent")
    if not set_type.is_active:
        raise HTTPException(409, "Tipul de set este dezactivat")
    pack = SterilizationPack(clinic_id=user.clinic_id, set_type_id=set_type.id, code=payload.code, code_key=_key(payload.code), created_by_id=user.id)
    db.add(pack)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Codul pachetului există deja în clinică")
    return (await _pack_views(db, user.clinic_id, [(pack, set_type.name)], utcnow()))[0]


@router.get("/packs/{pack_id}/history")
async def pack_history(pack_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    pack = await db.scalar(select(SterilizationPack).where(SterilizationPack.id == pack_id, SterilizationPack.clinic_id == user.clinic_id))
    if pack is None:
        raise HTTPException(404, "Pachet inexistent")
    actor = aliased(User)
    rows = (await db.execute(select(SterilizationPackEvent, actor.full_name).join(actor, actor.id == SterilizationPackEvent.actor_id)
            .where(SterilizationPackEvent.clinic_id == user.clinic_id, SterilizationPackEvent.pack_id == pack_id)
            .order_by(SterilizationPackEvent.created_at, SterilizationPackEvent.id))).all()
    return [{"id": str(e.id), "event_type": e.event_type, "cycle_id": str(e.cycle_id) if e.cycle_id else None,
             "appointment_id": str(e.appointment_id) if e.appointment_id else None, "expires_at": _iso(e.expires_at),
             "reason": e.reason, "actor_name": name, "created_at": _iso(e.created_at)} for e, name in rows]


@router.post("/packs/{pack_id}/use")
async def use_pack(pack_id: UUID, payload: UseIn, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    pack = await db.scalar(select(SterilizationPack).where(SterilizationPack.id == pack_id, SterilizationPack.clinic_id == user.clinic_id).with_for_update())
    if pack is None:
        raise HTTPException(404, "Pachet inexistent")
    appointment = await db.scalar(select(Appointment).where(Appointment.id == payload.appointment_id, Appointment.clinic_id == user.clinic_id))
    if appointment is None:
        raise HTTPException(404, "Programare inexistentă")
    performed = None
    if payload.performed_procedure_id:
        performed = await db.scalar(select(PerformedProcedure).where(
            PerformedProcedure.id == payload.performed_procedure_id, PerformedProcedure.clinic_id == user.clinic_id,
            PerformedProcedure.appointment_id == appointment.id))
        if performed is None:
            raise HTTPException(404, "Manoperă inexistentă la această programare")
    if user.role == "doctor" and appointment.doctor_id != user.id and not (performed and performed.executed_by_id == user.id):
        raise HTTPException(403, "Medicul poate lega pachete numai la programările și manoperele lui")
    if appointment.status == "cancelled":
        raise HTTPException(409, "Programarea este anulată")
    history = (await _events_by_pack(db, user.clinic_id, [pack.id])).get(pack.id, [])
    state = derive_state(history, utcnow())
    last = history[-1] if history else None
    if state == "used" and last.appointment_id == appointment.id and (payload.performed_procedure_id in (None, last.performed_procedure_id)):
        response.status_code = 200  # double click / retry: same single link
        return _use_json(pack, last)
    if state != "sterile":
        raise HTTPException(409, REFUSAL[state])
    cycle = await db.scalar(select(SterilizationCycle).where(SterilizationCycle.id == last.cycle_id, SterilizationCycle.clinic_id == user.clinic_id))
    if cycle is None or cycle.status != "approved":
        raise HTTPException(409, "Ciclul pachetului nu este aprobat")
    event = _event(user, pack, "used", cycle_id=cycle.id, appointment_id=appointment.id,
                   performed_procedure_id=performed.id if performed else None)
    db.add(event)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        again = (await _events_by_pack(db, user.clinic_id, [pack.id])).get(pack.id, [])
        if again and again[-1].event_type == "used" and again[-1].appointment_id == appointment.id:
            response.status_code = 200
            return _use_json(pack, again[-1])
        raise HTTPException(409, REFUSAL["used"])
    response.status_code = 201
    return _use_json(pack, event)


def _use_json(pack: SterilizationPack, event: SterilizationPackEvent) -> dict:
    return {"event_id": str(event.id), "pack_id": str(pack.id), "code": pack.code, "state": "used",
            "appointment_id": str(event.appointment_id), "performed_procedure_id": str(event.performed_procedure_id) if event.performed_procedure_id else None,
            "cycle_id": str(event.cycle_id), "used_at": _iso(event.created_at)}


# ---------------------------------------------------------------- cycles
@router.get("/cycles")
async def list_cycles(db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    rows = list(await db.scalars(select(SterilizationCycle).where(SterilizationCycle.clinic_id == user.clinic_id)
                                 .order_by(SterilizationCycle.started_at.desc(), SterilizationCycle.id).limit(LIST_LIMIT)))
    return await _cycle_views(db, user.clinic_id, rows)


@router.get("/cycles/{cycle_id}")
async def get_cycle(cycle_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    cycle = await _cycle(db, user, cycle_id)
    view = (await _cycle_views(db, user.clinic_id, [cycle]))[0]
    author = aliased(User)
    history = (await db.execute(select(SterilizationIndicatorResult, author.full_name).join(author, author.id == SterilizationIndicatorResult.recorded_by_id)
        .where(SterilizationIndicatorResult.clinic_id == user.clinic_id, SterilizationIndicatorResult.cycle_id == cycle.id)
        .order_by(SterilizationIndicatorResult.recorded_at, SterilizationIndicatorResult.id))).all()
    view["indicators"] = [{"kind": r.kind, "result": r.result, "note": r.note, "recorded_by_name": name, "recorded_at": _iso(r.recorded_at)} for r, name in history]
    return view


@router.post("/cycles", status_code=201)
async def start_cycle(payload: CycleIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    equipment = await db.scalar(select(Equipment).where(Equipment.id == payload.equipment_id, Equipment.clinic_id == user.clinic_id).with_for_update())
    if equipment is None:
        raise HTTPException(404, "Echipament inexistent")
    if _key(equipment.equipment_type) not in STERILIZER_TYPES:
        raise HTTPException(409, "Echipamentul nu este marcat ca sterilizator")
    now = utcnow()
    started = _aware(payload.started_at, "Începutul") or now
    if started > now + CLOCK_SKEW:
        raise HTTPException(422, "Începutul ciclului nu poate fi în viitor")
    reason = await _equipment_unavailable_reason(db, equipment, started)
    if reason:
        raise HTTPException(409, reason)
    operator_id = payload.operator_id or user.id
    if not await db.scalar(select(User.id).where(User.id == operator_id, User.clinic_id == user.clinic_id, User.is_active.is_(True))):
        raise HTTPException(404, "Operator inexistent")
    packs = list(await db.scalars(select(SterilizationPack).where(SterilizationPack.clinic_id == user.clinic_id, SterilizationPack.id.in_(payload.pack_ids))
                                  .order_by(SterilizationPack.id).with_for_update()))
    if len(packs) != len(payload.pack_ids):
        raise HTTPException(404, "Pachet inexistent")
    events = await _events_by_pack(db, user.clinic_id, [p.id for p in packs])
    for pack in packs:
        state = derive_state(events.get(pack.id, []), now)
        if state not in SAFE_PACK_STATES:
            detail = "Pachetul este deja steril, nu intră într-un ciclu nou" if state == "sterile" else REFUSAL[state]
            raise HTTPException(409, f"{pack.code}: {detail}")
    number = payload.cycle_number
    if number is None:
        number = (await db.scalar(select(func.coalesce(func.max(SterilizationCycle.cycle_number), 0)).where(SterilizationCycle.equipment_id == equipment.id))) + 1
    elif await db.scalar(select(SterilizationCycle.id).where(SterilizationCycle.equipment_id == equipment.id, SterilizationCycle.cycle_number == number)):
        raise HTTPException(409, f"Numărul de ciclu {number} există deja pe acest aparat")
    cycle = SterilizationCycle(clinic_id=user.clinic_id, equipment_id=equipment.id, cycle_number=number, program=payload.program,
                               started_at=started, operator_id=operator_id, status="in_progress")
    db.add(cycle)
    try:
        await db.flush()
        for pack in packs:
            db.add(SterilizationCyclePack(clinic_id=user.clinic_id, cycle_id=cycle.id, pack_id=pack.id))
            db.add(_event(user, pack, "entered_cycle", cycle_id=cycle.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, f"Numărul de ciclu {number} există deja pe acest aparat")
    return (await _cycle_views(db, user.clinic_id, [cycle]))[0]


@router.post("/cycles/{cycle_id}/finish")
async def finish_cycle(cycle_id: UUID, payload: FinishIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    cycle = await _cycle(db, user, cycle_id, lock=True)
    if cycle.status != "in_progress":
        raise HTTPException(409, "Ciclul nu mai este în curs")
    now = utcnow()
    ended = _aware(payload.ended_at, "Sfârșitul") or now
    if ended > now + CLOCK_SKEW:
        raise HTTPException(422, "Sfârșitul ciclului nu poate fi în viitor")
    if ended < cycle.started_at:
        raise HTTPException(422, "Sfârșitul trebuie să fie după început")
    cycle.ended_at, cycle.status = ended, "finished"
    cycle.temperature_c, cycle.pressure_kpa, cycle.hold_minutes = payload.temperature_c, payload.pressure_kpa, payload.hold_minutes
    for kind, result in (("chemical", payload.chemical_result), ("biological", payload.biological_result)):
        db.add(SterilizationIndicatorResult(clinic_id=user.clinic_id, cycle_id=cycle.id, kind=kind, result=result, recorded_by_id=user.id))
    await db.commit()
    return (await _cycle_views(db, user.clinic_id, [cycle]))[0]


@router.post("/cycles/{cycle_id}/decision")
async def decide_cycle(cycle_id: UUID, payload: DecisionIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    cycle = await _cycle(db, user, cycle_id, lock=True)
    if cycle.operator_id == user.id:
        raise HTTPException(403, "Ciclul nu poate fi aprobat sau respins de operatorul lui")
    if cycle.status != "finished":
        raise HTTPException(409, "Ciclul trebuie încheiat și nedecis")
    current = {r.kind: r.result for r in await db.scalars(select(SterilizationIndicatorResult).where(
        SterilizationIndicatorResult.clinic_id == user.clinic_id, SterilizationIndicatorResult.cycle_id == cycle.id)
        .order_by(SterilizationIndicatorResult.recorded_at, SterilizationIndicatorResult.id))}
    if payload.decision == "approved":
        if current.get("chemical") != "pass":
            raise HTTPException(409, "Indicatorul chimic trebuie să fie conform pentru aprobare")
        if current.get("biological") == "fail":
            raise HTTPException(409, "Indicatorul biologic este neconform; ciclul nu poate fi aprobat")
    packs = await _cycle_packs(db, cycle, lock=True)
    events = await _events_by_pack(db, user.clinic_id, [p.id for p in packs])
    expiry = {row.id: row.expiry_days for row in await db.execute(select(SterilizationSetType.id, SterilizationSetType.expiry_days).where(
        SterilizationSetType.clinic_id == user.clinic_id, SterilizationSetType.id.in_({p.set_type_id for p in packs})))}
    now = utcnow()
    for pack in packs:
        history = events.get(pack.id, [])
        if not history or history[-1].event_type != "entered_cycle" or history[-1].cycle_id != cycle.id:
            continue
        if payload.decision == "approved":
            db.add(_event(user, pack, "sterilized", cycle_id=cycle.id, expires_at=now + timedelta(days=expiry[pack.set_type_id])))
        else:
            db.add(_event(user, pack, "cycle_rejected", cycle_id=cycle.id, reason=payload.note))
    cycle.status = payload.decision
    cycle.decided_by_id, cycle.decided_at, cycle.decision_note = user.id, now, (payload.note or "").strip() or None
    await db.commit()
    return (await _cycle_views(db, user.clinic_id, [cycle]))[0]


@router.post("/cycles/{cycle_id}/indicators", status_code=201)
async def record_indicator(cycle_id: UUID, payload: IndicatorIn, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*WRITERS))):
    cycle = await _cycle(db, user, cycle_id, lock=True)
    if cycle.status == "in_progress":
        raise HTTPException(409, "Ciclul nu este încheiat")
    if payload.kind == "chemical" and cycle.status != "finished":
        raise HTTPException(409, "Indicatorul chimic nu se mai modifică după decizie")
    db.add(SterilizationIndicatorResult(clinic_id=user.clinic_id, cycle_id=cycle.id, kind=payload.kind, result=payload.result,
                                        note=(payload.note or "").strip() or None, recorded_by_id=user.id))
    already_recalled = await db.scalar(select(SterilizationRecall.id).where(SterilizationRecall.cycle_id == cycle.id))
    if payload.kind == "biological" and payload.result == "fail" and cycle.status == "approved" and not already_recalled:
        reason = (payload.note or "").strip() or "Indicator biologic pozitiv"
        db.add(SterilizationRecall(clinic_id=user.clinic_id, cycle_id=cycle.id, reason=reason, recorded_by_id=user.id))
        packs = await _cycle_packs(db, cycle, lock=True)
        events = await _events_by_pack(db, user.clinic_id, [p.id for p in packs])
        for pack in packs:
            history = events.get(pack.id, [])
            if history and history[-1].event_type == "sterilized" and history[-1].cycle_id == cycle.id:
                db.add(_event(user, pack, "compromised", cycle_id=cycle.id, reason=reason))
    await db.commit()
    return (await _cycle_views(db, user.clinic_id, [cycle]))[0]


# ---------------------------------------------------------------- recall
async def _recall_data(db: AsyncSession, user: User, cycle_id: UUID) -> dict:
    cycle = await _cycle(db, user, cycle_id)
    view = (await _cycle_views(db, user.clinic_id, [cycle]))[0]
    recall = await db.scalar(select(SterilizationRecall).where(SterilizationRecall.cycle_id == cycle.id, SterilizationRecall.clinic_id == user.clinic_id))
    items, unused = [], []
    if recall:
        doctor = aliased(User)
        rows = (await db.execute(select(SterilizationPackEvent, SterilizationPack.code, SterilizationSetType.name, Appointment, Patient, doctor.full_name)
            .join(SterilizationPack, SterilizationPack.id == SterilizationPackEvent.pack_id)
            .join(SterilizationSetType, SterilizationSetType.id == SterilizationPack.set_type_id)
            .join(Appointment, and_(Appointment.id == SterilizationPackEvent.appointment_id, Appointment.clinic_id == user.clinic_id))
            .join(Patient, and_(Patient.id == Appointment.patient_id, Patient.clinic_id == user.clinic_id))
            .join(doctor, doctor.id == Appointment.doctor_id)
            .where(SterilizationPackEvent.clinic_id == user.clinic_id, SterilizationPackEvent.cycle_id == cycle.id, SterilizationPackEvent.event_type == "used")
            .order_by(Appointment.starts_at, SterilizationPack.code))).all()
        for event, code, set_name, appointment, patient, doctor_name in rows:
            items.append({"pack_id": str(event.pack_id), "pack_code": code, "set_type": set_name, "used_at": _iso(event.created_at),
                          "appointment_id": str(appointment.id), "appointment_starts_at": _iso(appointment.starts_at),
                          "patient_id": str(patient.id), "patient_name": f"{patient.first_name} {patient.last_name}", "doctor_name": doctor_name,
                          "reason": f"Ciclul {cycle.cycle_number} ({view['equipment_name']}) a fost declarat compromis: {recall.reason}"})
        unused = [code for code, in await db.execute(select(SterilizationPack.code).join(SterilizationPackEvent, SterilizationPackEvent.pack_id == SterilizationPack.id)
            .where(SterilizationPackEvent.clinic_id == user.clinic_id, SterilizationPackEvent.cycle_id == cycle.id, SterilizationPackEvent.event_type == "compromised")
            .order_by(SterilizationPack.code))]
    return {"cycle": view, "compromised": recall is not None, "reason": recall.reason if recall else None,
            "recorded_at": _iso(recall.recorded_at) if recall else None, "items": items, "unused_packs": unused}


@router.get("/cycles/{cycle_id}/recall")
async def recall_list(cycle_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    return await _recall_data(db, user, cycle_id)


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text  # spreadsheet formula injection


@router.get("/cycles/{cycle_id}/recall.csv")
async def recall_csv(cycle_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    data = await _recall_data(db, user, cycle_id)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Pacient", "Data programării", "Medic", "Pachet", "Set", "Folosit la", "Ciclu", "Aparat", "Motiv"])
    for item in data["items"]:
        writer.writerow([_csv_cell(v) for v in (item["patient_name"], item["appointment_starts_at"], item["doctor_name"], item["pack_code"],
                         item["set_type"], item["used_at"], data["cycle"]["cycle_number"], data["cycle"]["equipment_name"], item["reason"])])
    name = re.sub(r"[^A-Za-z0-9_-]", "", f"rechemare-ciclu-{data['cycle']['cycle_number']}")
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ---------------------------------------------------------------- reverse traceability
async def _trace(db: AsyncSession, user: User, *conditions) -> list[dict]:
    rows = (await db.execute(
        select(SterilizationPackEvent, SterilizationPack.code, SterilizationSetType.name, SterilizationCycle, Equipment.name,
               Appointment.starts_at, Appointment.patient_id, PerformedProcedure.procedure_name, SterilizationRecall.reason)
        .join(SterilizationPack, SterilizationPack.id == SterilizationPackEvent.pack_id)
        .join(SterilizationSetType, SterilizationSetType.id == SterilizationPack.set_type_id)
        .join(SterilizationCycle, SterilizationCycle.id == SterilizationPackEvent.cycle_id)
        .join(Equipment, Equipment.id == SterilizationCycle.equipment_id)
        .join(Appointment, Appointment.id == SterilizationPackEvent.appointment_id)
        .outerjoin(PerformedProcedure, PerformedProcedure.id == SterilizationPackEvent.performed_procedure_id)
        .outerjoin(SterilizationRecall, SterilizationRecall.cycle_id == SterilizationCycle.id)
        .where(SterilizationPackEvent.clinic_id == user.clinic_id, SterilizationPackEvent.event_type == "used", *conditions)
        .order_by(SterilizationPackEvent.created_at, SterilizationPack.code))).all()
    return [{"event_id": str(e.id), "pack_id": str(e.pack_id), "pack_code": code, "set_type": set_name, "used_at": _iso(e.created_at),
             "appointment_id": str(e.appointment_id), "appointment_starts_at": _iso(starts), "patient_id": str(patient_id),
             "performed_procedure_id": str(e.performed_procedure_id) if e.performed_procedure_id else None, "procedure_name": procedure,
             "cycle_id": str(c.id), "cycle_number": c.cycle_number, "cycle_status": c.status, "equipment_id": str(c.equipment_id),
             "equipment_name": equipment_name, "approved_at": _iso(c.decided_at) if c.status == "approved" else None,
             "compromised": recall_reason is not None, "recall_reason": recall_reason}
            for e, code, set_name, c, equipment_name, starts, patient_id, procedure, recall_reason in rows]


@router.get("/trace/appointments/{appointment_id}")
async def trace_appointment(appointment_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    if not await db.scalar(select(Appointment.id).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Programare inexistentă")
    return await _trace(db, user, SterilizationPackEvent.appointment_id == appointment_id)


@router.get("/trace/patients/{patient_id}")
async def trace_patient(patient_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*READERS))):
    if not await db.scalar(select(Patient.id).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id)):
        raise HTTPException(404, "Pacient inexistent")
    return await _trace(db, user, Appointment.patient_id == patient_id)
