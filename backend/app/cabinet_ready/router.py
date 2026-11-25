from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, BeforeValidator, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.service import local_bounds
from app.auth.service import require_roles
from app.cabinet_ready.models import (
    CabinetChecklistItem,
    CabinetChecklistVersion,
    CabinetConfirmationEvent,
    CabinetManualBlock,
    CabinetManualBlockClosure,
    CabinetParticularity,
    CabinetParticularityDecision,
    CabinetPreparation,
    CabinetPreparationItem,
    CabinetRequestKey,
)
from app.cabinet_ready.service import (
    ACTIVE_APPOINTMENTS,
    build_cards,
    latest_checklists,
    planned_lines,
    preparation_scope,
    procedures_signature,
    reception_card,
)
from app.database import Appointment, Clinic, Patient, ProcedureCatalog, Room, User, get_db, utcnow

router = APIRouter(prefix="/cabinet-ready", tags=["cabinet_ready"])
MANAGERS = ("clinic_manager", "administrator")
WORKERS = ("doctor", "assistant", *MANAGERS)
READERS = (*WORKERS, "reception")
PROPOSERS = ("doctor", "assistant")
Kind = Literal["material", "instrument", "document", "action"]


def _required_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Valoarea este obligatorie")
    return value.strip()


RequiredText = Annotated[str, BeforeValidator(_required_text)]


class ChecklistLineIn(BaseModel):
    kind: Kind
    label: RequiredText = Field(min_length=1, max_length=200)
    required: bool
    prep_minutes: int = Field(ge=0, le=240)


class ChecklistSaveIn(BaseModel):
    expected_version: int = Field(ge=0)
    client_request_id: UUID
    lines: list[ChecklistLineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_required(self):
        if not any(line.required for line in self.lines):
            raise ValueError("Checklistul trebuie să aibă cel puțin un element obligatoriu.")
        return self


class RequestIn(BaseModel):
    client_request_id: UUID


class ReasonIn(RequestIn):
    reason: RequiredText = Field(min_length=1, max_length=500)


class BlockIn(ReasonIn):
    check_with: RequiredText = Field(min_length=1, max_length=200)


class ParticularityIn(RequestIn):
    text: RequiredText = Field(min_length=1, max_length=500)
    item_kind: Kind | None = None
    item_label: str | None = Field(default=None, max_length=200)
    item_required: bool | None = None
    item_minutes: int | None = Field(default=None, ge=0, le=240)

    @model_validator(mode="after")
    def complete_item(self):
        label = self.item_label.strip() if self.item_label else None
        self.item_label = label or None
        values = (self.item_kind, self.item_label, self.item_required, self.item_minutes)
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError("Elementul suplimentar are nevoie de tip, denumire, obligativitate și minute.")
        return self


class DecisionIn(RequestIn):
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


def _signature(user: User, action: str, target, payload: BaseModel) -> str:
    body = payload.model_dump(mode="json", exclude={"client_request_id"})
    encoded = json.dumps({"actor": str(user.id), "action": action, "target": str(target), "body": body},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _replay(db: AsyncSession, clinic_id: UUID, request_id: UUID, signature: str) -> dict | None:
    prior = await db.scalar(select(CabinetRequestKey).where(
        CabinetRequestKey.clinic_id == clinic_id, CabinetRequestKey.client_request_id == str(request_id),
    ).execution_options(populate_existing=True))
    if not prior:
        return None
    if prior.signature != signature:
        raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
    response = {**json.loads(prior.response), "retry": True}
    await db.rollback()
    return response


async def _finish(db: AsyncSession, user: User, request_id: UUID, signature: str, response: dict, conflict: str) -> dict:
    db.add(CabinetRequestKey(clinic_id=user.clinic_id, client_request_id=str(request_id), signature=signature,
                             response=json.dumps(response, ensure_ascii=False), actor_id=user.id))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        prior = await _replay(db, user.clinic_id, request_id, signature)
        if prior:
            return prior
        raise HTTPException(409, conflict) from exc
    return {**response, "retry": False}


async def _zone(db: AsyncSession, clinic_id: UUID) -> tuple[Clinic, ZoneInfo]:
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(404, "Clinică inexistentă")
    try:
        return clinic, ZoneInfo(clinic.timezone)
    except Exception as exc:
        raise HTTPException(500, "Fusul orar al clinicii nu este valid.") from exc


async def _writable_appointment(db: AsyncSession, user: User, appointment_id: UUID) -> Appointment:
    appointment = await db.scalar(select(Appointment).where(
        Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not appointment:
        raise HTTPException(404, "Programare inexistentă.")
    if appointment.status not in ACTIVE_APPOINTMENTS:
        raise HTTPException(409, "Programarea nu mai este activă.")
    return appointment


async def _current_preparation(db: AsyncSession, clinic_id: UUID, appointment_id: UUID) -> CabinetPreparation | None:
    return await db.scalar(select(CabinetPreparation).where(
        CabinetPreparation.clinic_id == clinic_id, CabinetPreparation.appointment_id == appointment_id,
    ).order_by(CabinetPreparation.revision.desc()).limit(1).execution_options(populate_existing=True))


async def _approved(db: AsyncSession, clinic_id: UUID, appointment_id: UUID):
    return (await db.scalars(select(CabinetParticularity).join(
        CabinetParticularityDecision, CabinetParticularityDecision.particularity_id == CabinetParticularity.id,
    ).where(
        CabinetParticularity.clinic_id == clinic_id, CabinetParticularity.appointment_id == appointment_id,
        CabinetParticularityDecision.decision == "approved",
    ).order_by(CabinetParticularity.proposed_at, CabinetParticularity.id))).all()


def _is_current(preparation: CabinetPreparation, appointment: Appointment, signature: str) -> bool:
    return (preparation.room_id == appointment.room_id and preparation.starts_at == appointment.starts_at
            and preparation.procedures_signature == signature)


@router.get("/board")
async def board(day: date = Query(...), room_id: UUID | None = Query(default=None),
                user: User = Depends(require_roles(*READERS)), db: AsyncSession = Depends(get_db)):
    clinic, zone = await _zone(db, user.clinic_id)
    start, end = local_bounds(day, clinic.timezone)
    query = select(Appointment, Patient, User, Room).join(Patient, Patient.id == Appointment.patient_id).join(
        User, User.id == Appointment.doctor_id).join(Room, Room.id == Appointment.room_id).where(
        Appointment.clinic_id == user.clinic_id, Patient.clinic_id == user.clinic_id, User.clinic_id == user.clinic_id,
        Room.clinic_id == user.clinic_id, Appointment.status.in_(ACTIVE_APPOINTMENTS),
        Appointment.starts_at >= start, Appointment.starts_at < end,
    ).order_by(Room.name, Appointment.starts_at, Appointment.id)
    if room_id:
        query = query.where(Appointment.room_id == room_id)
    rows = [tuple(row) for row in (await db.execute(query)).all()]
    cards = await build_cards(db, user, rows, zone)
    if user.role == "reception":
        cards = [reception_card(card) for card in cards]
    rooms = (await db.execute(select(Room.id, Room.name).where(Room.clinic_id == user.clinic_id, Room.is_active.is_(True)).order_by(Room.name))).all()
    return {"day": day.isoformat(), "timezone": clinic.timezone, "server_now": utcnow().isoformat(),
            "rooms": [{"id": str(row_id), "name": name} for row_id, name in rooms], "cards": cards}


@router.get("/checklists/{procedure_id}")
async def get_checklist(procedure_id: UUID, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    catalog = await db.scalar(select(ProcedureCatalog).where(ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id))
    if not catalog:
        raise HTTPException(404, "Procedura nu există.")
    latest, items = await latest_checklists(db, user.clinic_id, {procedure_id})
    version = latest.get(procedure_id)
    versions = (await db.scalars(select(CabinetChecklistVersion.version).where(
        CabinetChecklistVersion.clinic_id == user.clinic_id, CabinetChecklistVersion.procedure_catalog_id == procedure_id,
    ).order_by(CabinetChecklistVersion.version))).all()
    return {"procedure_catalog_id": str(procedure_id), "procedure_name": catalog.name, "version": version.version if version else 0,
            "versions": list(versions), "lines": [{"kind": row.kind, "label": row.label, "required": row.required, "prep_minutes": row.prep_minutes}
                                                  for row in items.get(version.id, [])] if version else []}


@router.put("/checklists/{procedure_id}", status_code=201)
async def save_checklist(procedure_id: UUID, payload: ChecklistSaveIn, user: User = Depends(require_roles(*MANAGERS)),
                         db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "checklist", procedure_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    catalog = await db.scalar(select(ProcedureCatalog).where(
        ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    if not catalog:
        raise HTTPException(404, "Procedura nu există.")
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    current = await db.scalar(select(func.max(CabinetChecklistVersion.version)).where(
        CabinetChecklistVersion.clinic_id == user.clinic_id, CabinetChecklistVersion.procedure_catalog_id == procedure_id)) or 0
    if current != payload.expected_version:
        raise HTTPException(409, "Checklistul a fost actualizat de alt utilizator. Reîncarcă și încearcă din nou.")
    version = CabinetChecklistVersion(clinic_id=user.clinic_id, procedure_catalog_id=procedure_id, version=current + 1, created_by_id=user.id)
    db.add(version)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
            return prior
        raise HTTPException(409, "Checklistul s-a schimbat simultan. Reîncarcă și încearcă din nou.") from exc
    db.add_all([CabinetChecklistItem(clinic_id=user.clinic_id, version_id=version.id, position=index, kind=line.kind,
                                     label=line.label, required=line.required, prep_minutes=line.prep_minutes)
                for index, line in enumerate(payload.lines, start=1)])
    return await _finish(db, user, payload.client_request_id, signature,
                         {"id": str(version.id), "procedure_catalog_id": str(procedure_id), "version": version.version},
                         "Checklistul s-a schimbat simultan. Reîncarcă și încearcă din nou.")


@router.post("/appointments/{appointment_id}/start", status_code=201)
async def start_preparation(appointment_id: UUID, payload: RequestIn, user: User = Depends(require_roles(*WORKERS)),
                            db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "start", appointment_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    appointment = await _writable_appointment(db, user, appointment_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    demand = (await preparation_scope(db, user.clinic_id, [appointment.id])).get(appointment.id, [])
    if not demand:
        raise HTTPException(409, "Programarea nu are nicio procedură efectivă.")
    latest, items = await latest_checklists(db, user.clinic_id, {row["procedure_catalog_id"] for row in demand})
    missing = [row["name"] for row in demand if row["procedure_catalog_id"] not in latest]
    if missing:
        raise HTTPException(409, f"Fără checklist: {', '.join(missing)}.")
    procedures = procedures_signature(demand)
    current = await _current_preparation(db, user.clinic_id, appointment.id)
    if current and _is_current(current, appointment, procedures):
        raise HTTPException(409, "Pregătirea este deja începută.")
    lines = planned_lines(demand, latest, items, await _approved(db, user.clinic_id, appointment.id))
    preparation = CabinetPreparation(clinic_id=user.clinic_id, appointment_id=appointment.id, revision=(current.revision if current else 0) + 1,
                                     room_id=appointment.room_id, starts_at=appointment.starts_at, procedures_signature=procedures,
                                     started_by_id=user.id, started_at=utcnow())
    db.add(preparation)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Pregătirea a fost începută simultan.") from exc
    db.add_all([CabinetPreparationItem(clinic_id=user.clinic_id, preparation_id=preparation.id, position=index, **line)
                for index, line in enumerate(lines, start=1)])
    return await _finish(db, user, payload.client_request_id, signature, {"id": str(preparation.id), "revision": preparation.revision},
                         "Pregătirea a fost începută simultan.")


async def _writable_item(db: AsyncSession, user: User, item_id: UUID):
    target = (await db.execute(select(CabinetPreparationItem.preparation_id, CabinetPreparation.appointment_id).join(
        CabinetPreparation, CabinetPreparation.id == CabinetPreparationItem.preparation_id,
    ).where(CabinetPreparationItem.id == item_id, CabinetPreparationItem.clinic_id == user.clinic_id,
            CabinetPreparation.clinic_id == user.clinic_id))).first()
    if not target:
        raise HTTPException(404, "Element inexistent.")
    appointment = await _writable_appointment(db, user, target.appointment_id)
    current = await _current_preparation(db, user.clinic_id, appointment.id)
    demand = (await preparation_scope(db, user.clinic_id, [appointment.id])).get(appointment.id, [])
    if not current or current.id != target.preparation_id or not _is_current(current, appointment, procedures_signature(demand)):
        raise HTTPException(409, "Programarea s-a schimbat. Reîncepe pregătirea.")
    last = await db.scalar(select(CabinetConfirmationEvent).where(
        CabinetConfirmationEvent.preparation_item_id == item_id,
    ).order_by(CabinetConfirmationEvent.sequence.desc()).limit(1).execution_options(populate_existing=True))
    return last


async def _record_event(db: AsyncSession, user: User, item_id: UUID, action: str, reason: str | None, last, request_id: UUID, signature: str):
    event = CabinetConfirmationEvent(clinic_id=user.clinic_id, preparation_item_id=item_id, sequence=(last.sequence if last else 0) + 1,
                                     action=action, reason=reason, actor_id=user.id, occurred_at=utcnow())
    db.add(event)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if prior := await _replay(db, user.clinic_id, request_id, signature):
            return prior
        raise HTTPException(409, "Elementul a fost modificat simultan.") from exc
    return await _finish(db, user, request_id, signature,
                         {"id": str(event.id), "item_id": str(item_id), "action": action, "actor": user.full_name,
                          "occurred_at": event.occurred_at.isoformat()}, "Elementul a fost modificat simultan.")


@router.post("/items/{item_id}/confirm", status_code=201)
async def confirm_item(item_id: UUID, payload: RequestIn, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "confirm", item_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    last = await _writable_item(db, user, item_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    if last and last.action == "confirm":
        raise HTTPException(409, "Elementul este deja confirmat.")
    return await _record_event(db, user, item_id, "confirm", None, last, payload.client_request_id, signature)


@router.post("/items/{item_id}/revoke", status_code=201)
async def revoke_item(item_id: UUID, payload: ReasonIn, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "revoke", item_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    last = await _writable_item(db, user, item_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    if not last or last.action != "confirm":
        raise HTTPException(409, "Elementul nu este confirmat.")
    return await _record_event(db, user, item_id, "revoke", payload.reason, last, payload.client_request_id, signature)


@router.post("/appointments/{appointment_id}/blocks", status_code=201)
async def add_block(appointment_id: UUID, payload: BlockIn, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "block", appointment_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    appointment = await _writable_appointment(db, user, appointment_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    block = CabinetManualBlock(clinic_id=user.clinic_id, appointment_id=appointment.id, reason=payload.reason,
                               check_with=payload.check_with, created_by_id=user.id, created_at=utcnow())
    db.add(block)
    await db.flush()
    return await _finish(db, user, payload.client_request_id, signature, {"id": str(block.id)}, "Blocajul a intrat în conflict.")


@router.post("/blocks/{block_id}/close", status_code=201)
async def close_block(block_id: UUID, payload: ReasonIn, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "close_block", block_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    block = await db.scalar(select(CabinetManualBlock).where(CabinetManualBlock.id == block_id, CabinetManualBlock.clinic_id == user.clinic_id))
    if not block:
        raise HTTPException(404, "Blocaj inexistent.")
    await _writable_appointment(db, user, block.appointment_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    if await db.scalar(select(CabinetManualBlockClosure.id).where(CabinetManualBlockClosure.block_id == block_id)):
        raise HTTPException(409, "Blocajul este deja închis.")
    closure = CabinetManualBlockClosure(clinic_id=user.clinic_id, block_id=block_id, reason=payload.reason, closed_by_id=user.id, closed_at=utcnow())
    db.add(closure)
    await db.flush()
    return await _finish(db, user, payload.client_request_id, signature, {"id": str(closure.id), "block_id": str(block_id)}, "Blocajul este deja închis.")


@router.post("/appointments/{appointment_id}/particularities", status_code=201)
async def propose_particularity(appointment_id: UUID, payload: ParticularityIn, user: User = Depends(require_roles(*PROPOSERS)),
                                db: AsyncSession = Depends(get_db)):
    signature = _signature(user, "particularity", appointment_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    appointment = await _writable_appointment(db, user, appointment_id)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    row = CabinetParticularity(clinic_id=user.clinic_id, appointment_id=appointment.id, text=payload.text, item_kind=payload.item_kind,
                               item_label=payload.item_label, item_required=payload.item_required, item_minutes=payload.item_minutes,
                               proposed_by_id=user.id, proposed_at=utcnow())
    db.add(row)
    await db.flush()
    return await _finish(db, user, payload.client_request_id, signature, {"id": str(row.id), "status": "pending"}, "Propunerea a intrat în conflict.")


@router.post("/particularities/{particularity_id}/decision", status_code=201)
async def decide_particularity(particularity_id: UUID, payload: DecisionIn, user: User = Depends(require_roles(*READERS)),
                               db: AsyncSession = Depends(get_db)):
    if user.role == "reception":
        raise HTTPException(403, "Rolul nu poate decide particularități.")
    signature = _signature(user, "decision", particularity_id, payload)
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    row = await db.scalar(select(CabinetParticularity).where(CabinetParticularity.id == particularity_id, CabinetParticularity.clinic_id == user.clinic_id))
    if not row:
        raise HTTPException(404, "Particularitate inexistentă.")
    appointment = await _writable_appointment(db, user, row.appointment_id)
    if appointment.doctor_id != user.id:
        raise HTTPException(403, "Numai medicul programării poate aproba sau respinge.")
    if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
        return prior
    if await db.scalar(select(CabinetParticularityDecision.id).where(CabinetParticularityDecision.particularity_id == row.id)):
        raise HTTPException(409, "Particularitatea are deja o decizie.")
    db.add(CabinetParticularityDecision(clinic_id=user.clinic_id, particularity_id=row.id, decision=payload.decision,
                                        reason=payload.reason.strip() if payload.reason and payload.reason.strip() else None,
                                        decided_by_id=user.id, decided_at=utcnow()))
    if payload.decision == "approved" and row.item_kind:
        current = await _current_preparation(db, user.clinic_id, appointment.id)
        demand = (await preparation_scope(db, user.clinic_id, [appointment.id])).get(appointment.id, [])
        if current and _is_current(current, appointment, procedures_signature(demand)):
            position = (await db.scalar(select(func.max(CabinetPreparationItem.position)).where(
                CabinetPreparationItem.preparation_id == current.id)) or 0) + 1
            db.add(CabinetPreparationItem(clinic_id=user.clinic_id, preparation_id=current.id, position=position, particularity_id=row.id,
                                          kind=row.item_kind, label=row.item_label, required=bool(row.item_required), prep_minutes=row.item_minutes or 0))
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if prior := await _replay(db, user.clinic_id, payload.client_request_id, signature):
            return prior
        raise HTTPException(409, "Particularitatea are deja o decizie.") from exc
    return await _finish(db, user, payload.client_request_id, signature, {"id": str(row.id), "status": payload.decision},
                         "Particularitatea are deja o decizie.")


@router.get("/appointments/{appointment_id}/history")
async def history(appointment_id: UUID, user: User = Depends(require_roles(*WORKERS)), db: AsyncSession = Depends(get_db)):
    appointment = await db.scalar(select(Appointment).where(Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id))
    if not appointment:
        raise HTTPException(404, "Programare inexistentă.")
    preparations = (await db.scalars(select(CabinetPreparation).where(
        CabinetPreparation.clinic_id == user.clinic_id, CabinetPreparation.appointment_id == appointment_id,
    ).order_by(CabinetPreparation.revision))).all()
    revision = {row.id: row.revision for row in preparations}
    rows = (await db.execute(select(CabinetConfirmationEvent, CabinetPreparationItem).join(
        CabinetPreparationItem, CabinetPreparationItem.id == CabinetConfirmationEvent.preparation_item_id,
    ).where(CabinetConfirmationEvent.clinic_id == user.clinic_id, CabinetPreparationItem.preparation_id.in_(list(revision))
            ).order_by(CabinetConfirmationEvent.occurred_at, CabinetConfirmationEvent.id))).all() if revision else []
    blocks = (await db.execute(select(CabinetManualBlock, CabinetManualBlockClosure).outerjoin(
        CabinetManualBlockClosure, CabinetManualBlockClosure.block_id == CabinetManualBlock.id,
    ).where(CabinetManualBlock.clinic_id == user.clinic_id, CabinetManualBlock.appointment_id == appointment_id
            ).order_by(CabinetManualBlock.created_at, CabinetManualBlock.id))).all()
    actor_ids = {row.started_by_id for row in preparations} | {event.actor_id for event, _item in rows}
    actor_ids |= {block.created_by_id for block, _closure in blocks} | {closure.closed_by_id for _block, closure in blocks if closure}
    people = dict((await db.execute(select(User.id, User.full_name).where(User.clinic_id == user.clinic_id, User.id.in_(actor_ids))))
                  .all()) if actor_ids else {}
    return {
        "preparations": [{"id": str(row.id), "revision": row.revision, "started_at": row.started_at.isoformat(),
                          "started_by": people.get(row.started_by_id), "starts_at": row.starts_at.isoformat(), "room_id": str(row.room_id)}
                         for row in preparations],
        "confirmations": [{"id": str(event.id), "revision": revision[item.preparation_id], "item_id": str(item.id), "label": item.label,
                           "action": event.action, "reason": event.reason, "actor": people.get(event.actor_id),
                           "occurred_at": event.occurred_at.isoformat()} for event, item in rows],
        "blocks": [{"id": str(block.id), "reason": block.reason, "check_with": block.check_with, "created_by": people.get(block.created_by_id),
                    "created_at": block.created_at.isoformat(), "closed_at": closure.closed_at.isoformat() if closure else None,
                    "closed_by": people.get(closure.closed_by_id) if closure else None, "close_reason": closure.reason if closure else None}
                   for block, closure in blocks],
    }
