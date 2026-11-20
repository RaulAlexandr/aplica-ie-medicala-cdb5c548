"""T08 treatment plans: stages, planned items, estimates, acceptance and execution links.

Status matrix (plan):
    draft       -> accepted (accept), cancelled
    accepted    -> draft (content edit bumps version), in_progress (first performed link), completed, cancelled
    in_progress -> in_progress (content edit bumps version and requires a new acceptance), completed, cancelled
    completed, cancelled: terminal

Item progress is derived on read, never stored as "done": an item is done only while it is
linked to a T04 performed procedure whose status is ``completed``. Planning never creates
performed procedures, prices to collect or timer segments.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    AuditEvent,
    Patient,
    PerformedProcedure,
    ProcedureCatalog,
    TreatmentPlan,
    TreatmentPlanAcceptance,
    TreatmentPlanEvent,
    TreatmentPlanItem,
    TreatmentPlanStage,
    User,
    get_db,
    utcnow,
)
from app.performed_procedures.router import (
    MAX_SURFACE_LENGTH,
    validate_fdi_tooth,
    validate_price_value,
    validate_surface,
)
from app.procedures.locations import location_violation

router = APIRouter(prefix="/treatment-plans", tags=["treatment_plans"])
# Reception and assistants have no access to T08 content in the pilot.
PLAN_ROLES = ("clinic_manager", "doctor", "administrator")
PRIORITIES = ("urgent", "high", "normal", "low")
EDITABLE_STATUSES = {"draft", "accepted", "in_progress"}
LINKABLE_APPOINTMENT_STATUSES_EXCLUDED = {"cancelled", "no_show"}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value must not be blank")
    return value


def _price(value: object) -> Decimal | None:
    """Decimal estimate from a JSON string; numbers are refused (no float money)."""
    if value is None:
        return None
    try:
        return validate_price_value(value)
    except TypeError as exc:
        raise ValueError(str(exc)) from exc


def _surface_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip().lower() for part in value.split(",") if part.strip())


def _normalize_surface(value: str | None) -> str | None:
    value = validate_surface(value)
    surfaces = _surface_set(value)
    if not surfaces:
        return None
    if len(surfaces) != len([part for part in value.split(",") if part.strip()]):
        raise ValueError("Duplicate tooth surface")
    return ",".join(sorted(surfaces))


class ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tooth_fdi: str | None = Field(default=None, max_length=3)
    tooth_surface: str | None = Field(default=None, max_length=MAX_SURFACE_LENGTH)
    priority: Literal["urgent", "high", "normal", "low"] = "normal"
    estimated_price: Decimal | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("tooth_fdi", mode="before")
    @classmethod
    def valid_tooth(cls, value: str | None) -> str | None:
        return validate_fdi_tooth(value)

    @field_validator("tooth_surface", mode="before")
    @classmethod
    def valid_surface(cls, value: str | None) -> str | None:
        return _normalize_surface(value)

    @field_validator("estimated_price", mode="before")
    @classmethod
    def valid_price(cls, value: object) -> Decimal | None:
        return _price(value)


class ItemCreate(ItemFields):
    procedure_catalog_id: UUID


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tooth_fdi: str | None = Field(default=None, max_length=3)
    tooth_surface: str | None = Field(default=None, max_length=MAX_SURFACE_LENGTH)
    priority: Literal["urgent", "high", "normal", "low"] | None = None
    estimated_price: Decimal | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    notes: str | None = Field(default=None, max_length=2000)
    stage_id: UUID | None = None

    @field_validator("tooth_fdi", mode="before")
    @classmethod
    def valid_tooth(cls, value: str | None) -> str | None:
        return validate_fdi_tooth(value)

    @field_validator("tooth_surface", mode="before")
    @classmethod
    def valid_surface(cls, value: str | None) -> str | None:
        return _normalize_surface(value)

    @field_validator("estimated_price", mode="before")
    @classmethod
    def valid_price(cls, value: object) -> Decimal | None:
        return _price(value)


class StageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    items: list[ItemCreate] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        return _required_text(value)


class StagePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    move: Literal["up", "down"] | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _required_text(value)


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    stages: list[StageCreate] = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        return _required_text(value)


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _required_text(value)


class ReasonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        return _required_text(value)


class AcceptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=2000)
    expected_version: int | None = None

    @field_validator("note")
    @classmethod
    def note_not_blank(cls, value: str) -> str:
        return _required_text(value)


class AppointmentLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: UUID | None


class PerformedLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    performed_procedure_id: UUID


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _uuid_key(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise HTTPException(422, "Idempotency-Key must be a UUID") from exc


async def _patient(db: AsyncSession, patient_id: UUID, user: User) -> Patient:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == user.clinic_id))
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")
    return patient


async def _plan(db: AsyncSession, plan_id: UUID, user: User, *, lock: bool = False) -> TreatmentPlan:
    query = select(TreatmentPlan).where(TreatmentPlan.id == plan_id, TreatmentPlan.clinic_id == user.clinic_id)
    if lock:
        query = query.with_for_update()
    plan = await db.scalar(query)
    if not plan:
        raise HTTPException(404, "Treatment plan not found or access denied")
    return plan


async def _item(db: AsyncSession, item_id: UUID, user: User) -> tuple[TreatmentPlan, TreatmentPlanItem]:
    """Lock the owning plan first (single lock order: plan -> item), then load the item."""
    plan_id = await db.scalar(select(TreatmentPlanItem.plan_id).where(
        TreatmentPlanItem.id == item_id, TreatmentPlanItem.clinic_id == user.clinic_id))
    if not plan_id:
        raise HTTPException(404, "Treatment plan item not found or access denied")
    plan = await _plan(db, plan_id, user, lock=True)
    item = await db.scalar(select(TreatmentPlanItem).where(
        TreatmentPlanItem.id == item_id, TreatmentPlanItem.clinic_id == user.clinic_id).with_for_update())
    if not item:
        raise HTTPException(404, "Treatment plan item not found or access denied")
    return plan, item


async def _stage(db: AsyncSession, plan: TreatmentPlan, stage_id: UUID) -> TreatmentPlanStage:
    stage = await db.scalar(select(TreatmentPlanStage).where(
        TreatmentPlanStage.id == stage_id, TreatmentPlanStage.plan_id == plan.id,
        TreatmentPlanStage.clinic_id == plan.clinic_id))
    if not stage:
        raise HTTPException(404, "Stage not found in this treatment plan")
    return stage


def _require_editable(plan: TreatmentPlan) -> None:
    if plan.status not in EDITABLE_STATUSES:
        raise HTTPException(409, f"Treatment plan is {plan.status} and can no longer be changed")


def _content_changed(plan: TreatmentPlan) -> None:
    """Content/estimate edits after acceptance open a new version requiring a new acceptance."""
    if plan.accepted_version is not None and plan.accepted_version == plan.version:
        plan.version += 1
        if plan.status == "accepted":
            plan.status = "draft"
    plan.updated_at = utcnow()


async def _record(db: AsyncSession, plan: TreatmentPlan, user: User, action: str, *,
                  item_id: UUID | None = None, details: dict | None = None) -> None:
    await db.flush()
    db.add(TreatmentPlanEvent(clinic_id=plan.clinic_id, plan_id=plan.id, item_id=item_id, actor_id=user.id,
                              action=action, plan_version=plan.version, details=details or {}, occurred_at=utcnow()))
    db.add(AuditEvent(clinic_id=plan.clinic_id, actor_id=user.id, entity_type="treatment_plan",
                      entity_id=plan.id, action=action[:40]))


async def _active_catalog(db: AsyncSession, catalog_id: UUID, user: User) -> ProcedureCatalog:
    catalog = await db.scalar(select(ProcedureCatalog).where(
        ProcedureCatalog.id == catalog_id, ProcedureCatalog.clinic_id == user.clinic_id))
    if not catalog:
        raise HTTPException(404, "Procedure not found in clinic catalog")
    if not catalog.is_active:
        raise HTTPException(409, "Inactive catalog procedures cannot be added to a treatment plan")
    return catalog


def _validate_location(tooth: str | None, surface: str | None) -> None:
    if surface and not tooth:
        raise HTTPException(422, "Tooth surfaces require an FDI tooth")


def _validate_allowed_location(catalog: ProcedureCatalog, tooth: str | None, surface: str | None) -> None:
    violation = location_violation(catalog.allowed_location, catalog.code, tooth, surface)
    if violation:
        raise HTTPException(422, violation)


async def _new_item(db: AsyncSession, plan: TreatmentPlan, stage: TreatmentPlanStage, payload: ItemCreate,
                    user: User, position: int, idempotency_key: str | None = None) -> TreatmentPlanItem:
    catalog = await _active_catalog(db, payload.procedure_catalog_id, user)
    _validate_location(payload.tooth_fdi, payload.tooth_surface)
    _validate_allowed_location(catalog, payload.tooth_fdi, payload.tooth_surface)
    now = utcnow()
    item = TreatmentPlanItem(
        clinic_id=plan.clinic_id, plan_id=plan.id, stage_id=stage.id, position=position,
        procedure_catalog_id=catalog.id, procedure_code=catalog.code, procedure_name=catalog.name,
        currency=catalog.currency,
        estimated_price=payload.estimated_price if payload.estimated_price is not None else catalog.base_price,
        estimated_duration_minutes=payload.estimated_duration_minutes or catalog.default_duration_minutes,
        tooth_fdi=payload.tooth_fdi, tooth_surface=payload.tooth_surface, priority=payload.priority,
        notes=_clean(payload.notes), status="active", idempotency_key=idempotency_key,
        created_at=now, updated_at=now,
    )
    db.add(item)
    return item


def _item_progress(item: TreatmentPlanItem, performed: PerformedProcedure | None) -> str:
    if item.status == "cancelled":
        return "cancelled"
    if performed is not None:
        if performed.status == "completed":
            return "done"
        if performed.status == "cancelled":
            return "attention"
        return "in_progress"
    if item.appointment_id:
        return "scheduled"
    return "planned"


def _progress(states: list[str]) -> dict:
    total = len(states)
    cancelled = states.count("cancelled")
    done = states.count("done")
    relevant = total - cancelled
    return {
        "total": total, "done": done, "cancelled": cancelled,
        "in_progress": states.count("in_progress"), "scheduled": states.count("scheduled"),
        "planned": states.count("planned"), "attention": states.count("attention"),
        "remaining": relevant - done,
        "percent": int(done * 100 / relevant) if relevant else 0,
    }


def _totals(rows: list[tuple[TreatmentPlanItem, str]]) -> list[dict]:
    estimated: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    completed: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    minutes: dict[str, int] = defaultdict(int)
    for item, state in rows:
        if state == "cancelled":
            continue
        estimated[item.currency] += item.estimated_price
        minutes[item.currency] += item.estimated_duration_minutes
        if state == "done":
            completed[item.currency] += item.estimated_price
    return [
        {"currency": currency, "estimated_total": _money(estimated[currency]),
         "completed_estimate": _money(completed[currency]),
         "remaining_estimate": _money(estimated[currency] - completed[currency]),
         "estimated_minutes": minutes[currency]}
        for currency in sorted(estimated)
    ]


async def _names(db: AsyncSession, ids: set[UUID | None]) -> dict[UUID, str]:
    ids = {value for value in ids if value}
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in rows}


def _item_content(item: TreatmentPlanItem) -> dict:
    return {
        "id": str(item.id), "procedure_catalog_id": str(item.procedure_catalog_id),
        "procedure_code": item.procedure_code, "procedure_name": item.procedure_name,
        "currency": item.currency, "estimated_price": _money(item.estimated_price),
        "estimated_duration_minutes": item.estimated_duration_minutes,
        "tooth_fdi": item.tooth_fdi, "tooth_surface": item.tooth_surface,
        "priority": item.priority, "notes": item.notes, "status": item.status,
    }


async def _load_content(db: AsyncSession, plan: TreatmentPlan):
    stages = list(await db.scalars(select(TreatmentPlanStage).where(
        TreatmentPlanStage.plan_id == plan.id, TreatmentPlanStage.clinic_id == plan.clinic_id,
    ).order_by(TreatmentPlanStage.position, TreatmentPlanStage.id)))
    items = list(await db.scalars(select(TreatmentPlanItem).where(
        TreatmentPlanItem.plan_id == plan.id, TreatmentPlanItem.clinic_id == plan.clinic_id,
    ).order_by(TreatmentPlanItem.position, TreatmentPlanItem.created_at, TreatmentPlanItem.id)))
    performed_ids = {item.performed_procedure_id for item in items if item.performed_procedure_id}
    appointment_ids = {item.appointment_id for item in items if item.appointment_id}
    performed = {}
    if performed_ids:
        performed = {row.id: row for row in await db.scalars(select(PerformedProcedure).where(
            PerformedProcedure.id.in_(performed_ids), PerformedProcedure.clinic_id == plan.clinic_id))}
    appointments = {}
    if appointment_ids:
        appointments = {row.id: row for row in await db.scalars(select(Appointment).where(
            Appointment.id.in_(appointment_ids), Appointment.clinic_id == plan.clinic_id))}
    return stages, items, performed, appointments


def _snapshot(plan: TreatmentPlan, stages, items, performed) -> dict:
    by_stage = defaultdict(list)
    rows = []
    for item in items:
        by_stage[item.stage_id].append(item)
        rows.append((item, _item_progress(item, performed.get(item.performed_procedure_id))))
    return {
        "version": plan.version, "title": plan.title, "notes": plan.notes,
        "stages": [{"id": str(stage.id), "position": stage.position, "title": stage.title, "notes": stage.notes,
                    "items": [_item_content(item) for item in by_stage[stage.id]]} for stage in stages],
        "totals": [{"currency": total["currency"], "estimated_total": total["estimated_total"],
                    "estimated_minutes": total["estimated_minutes"]} for total in _totals(rows)],
    }


async def _plan_response(db: AsyncSession, plan: TreatmentPlan) -> dict:
    stages, items, performed, appointments = await _load_content(db, plan)
    acceptances = list(await db.scalars(select(TreatmentPlanAcceptance).where(
        TreatmentPlanAcceptance.plan_id == plan.id, TreatmentPlanAcceptance.clinic_id == plan.clinic_id,
    ).order_by(TreatmentPlanAcceptance.version)))
    events = list(await db.scalars(select(TreatmentPlanEvent).where(
        TreatmentPlanEvent.plan_id == plan.id, TreatmentPlanEvent.clinic_id == plan.clinic_id,
    ).order_by(TreatmentPlanEvent.occurred_at.desc(), TreatmentPlanEvent.id)))
    names = await _names(db, {plan.created_by_id, plan.completed_by_id, plan.cancelled_by_id}
                         | {item.cancelled_by_id for item in items} | {item.appointment_linked_by_id for item in items}
                         | {item.performed_linked_by_id for item in items}
                         | {row.accepted_by_id for row in acceptances} | {event.actor_id for event in events})
    rows = []
    by_stage: dict[UUID, list[dict]] = defaultdict(list)
    stage_states: dict[UUID, list[str]] = defaultdict(list)
    for item in items:
        procedure = performed.get(item.performed_procedure_id)
        appointment = appointments.get(item.appointment_id)
        state = _item_progress(item, procedure)
        rows.append((item, state))
        stage_states[item.stage_id].append(state)
        by_stage[item.stage_id].append({
            **_item_content(item), "stage_id": str(item.stage_id), "position": item.position,
            "progress": state,
            "cancel_reason": item.cancel_reason, "cancelled_at": _iso(item.cancelled_at),
            "cancelled_by": names.get(item.cancelled_by_id),
            "appointment": None if not appointment else {
                "id": str(appointment.id), "starts_at": _iso(appointment.starts_at), "status": appointment.status,
                "appointment_type": appointment.appointment_type,
                "linked_by": names.get(item.appointment_linked_by_id), "linked_at": _iso(item.appointment_linked_at),
            },
            "performed_procedure": None if not procedure else {
                "id": str(procedure.id), "status": procedure.status, "procedure_code": procedure.procedure_code,
                "procedure_name": procedure.procedure_name, "tooth_fdi": procedure.tooth_fdi,
                "tooth_surface": procedure.tooth_surface, "appointment_id": str(procedure.appointment_id),
                "linked_by": names.get(item.performed_linked_by_id), "linked_at": _iso(item.performed_linked_at),
            },
        })
    all_states = [state for _, state in rows]
    return {
        "id": str(plan.id), "clinic_id": str(plan.clinic_id), "patient_id": str(plan.patient_id),
        "title": plan.title, "notes": plan.notes, "status": plan.status,
        "version": plan.version, "accepted_version": plan.accepted_version,
        "needs_acceptance": plan.status in EDITABLE_STATUSES and plan.accepted_version != plan.version,
        "created_by": names.get(plan.created_by_id), "created_at": _iso(plan.created_at),
        "updated_at": _iso(plan.updated_at),
        "completed_at": _iso(plan.completed_at), "completed_by": names.get(plan.completed_by_id),
        "cancelled_at": _iso(plan.cancelled_at), "cancelled_by": names.get(plan.cancelled_by_id),
        "cancel_reason": plan.cancel_reason,
        "can_complete": _completion_blocker(plan, all_states) is None,
        "completion_blocker": _completion_blocker(plan, all_states),
        "estimate_label": "Estimare; nu este factură sau sumă încasată",
        "totals": _totals(rows),
        "progress": _progress(all_states),
        "stages": [{
            "id": str(stage.id), "position": stage.position, "title": stage.title, "notes": stage.notes,
            "progress": _progress(stage_states[stage.id]), "items": by_stage[stage.id],
        } for stage in stages],
        "acceptances": [{
            "id": str(row.id), "version": row.version, "accepted_by": names.get(row.accepted_by_id),
            "accepted_by_id": str(row.accepted_by_id), "accepted_at": _iso(row.accepted_at), "note": row.note,
            "is_current": row.version == plan.version, "snapshot": row.snapshot,
        } for row in acceptances],
        "history": [{
            "id": str(event.id), "action": event.action, "plan_version": event.plan_version,
            "item_id": str(event.item_id) if event.item_id else None, "actor": names.get(event.actor_id),
            "occurred_at": _iso(event.occurred_at), "details": event.details,
        } for event in events],
    }


def _completion_blocker(plan: TreatmentPlan, states: list[str]) -> str | None:
    if plan.status not in {"accepted", "in_progress"}:
        return f"Plan status {plan.status} cannot be completed"
    if plan.accepted_version != plan.version:
        return "The current plan version must be accepted before completion"
    open_items = [state for state in states if state not in {"done", "cancelled"}]
    if open_items:
        return f"{len(open_items)} item(s) are neither completed nor explicitly cancelled"
    if "done" not in states:
        return "At least one item must be completed; cancel the plan instead"
    return None


async def _respond(db: AsyncSession, plan: TreatmentPlan) -> dict:
    await db.refresh(plan)
    return await _plan_response(db, plan)


@router.get("/patients/{patient_id}")
async def list_patient_plans(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> list[dict]:
    await _patient(db, patient_id, user)
    plans = await db.scalars(select(TreatmentPlan).where(
        TreatmentPlan.clinic_id == user.clinic_id, TreatmentPlan.patient_id == patient_id,
    ).order_by(TreatmentPlan.created_at.desc(), TreatmentPlan.id))
    result = []
    for plan in plans:
        full = await _plan_response(db, plan)
        result.append({key: full[key] for key in (
            "id", "patient_id", "title", "status", "version", "accepted_version", "needs_acceptance",
            "created_at", "updated_at", "totals", "progress")})
    return result


@router.get("/patients/{patient_id}/link-candidates")
async def link_candidates(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    """Existing appointments and performed procedures of this patient only (read-only T02/T04 data)."""
    await _patient(db, patient_id, user)
    appointments = await db.scalars(select(Appointment).where(
        Appointment.clinic_id == user.clinic_id, Appointment.patient_id == patient_id,
        Appointment.status.not_in(LINKABLE_APPOINTMENT_STATUSES_EXCLUDED),
    ).order_by(Appointment.starts_at.desc()).limit(100))
    procedures = list(await db.scalars(select(PerformedProcedure).where(
        PerformedProcedure.clinic_id == user.clinic_id, PerformedProcedure.patient_id == patient_id,
        PerformedProcedure.status != "cancelled",
    ).order_by(PerformedProcedure.created_at.desc()).limit(200)))
    linked = {}
    if procedures:
        rows = await db.execute(select(TreatmentPlanItem.performed_procedure_id, TreatmentPlanItem.id).where(
            TreatmentPlanItem.clinic_id == user.clinic_id,
            TreatmentPlanItem.performed_procedure_id.in_([row.id for row in procedures])))
        linked = {row[0]: str(row[1]) for row in rows}
    return {
        "appointments": [{"id": str(row.id), "starts_at": _iso(row.starts_at), "status": row.status,
                          "appointment_type": row.appointment_type} for row in appointments],
        "performed_procedures": [{
            "id": str(row.id), "appointment_id": str(row.appointment_id),
            "procedure_catalog_id": str(row.procedure_catalog_id), "procedure_code": row.procedure_code,
            "procedure_name": row.procedure_name, "tooth_fdi": row.tooth_fdi, "tooth_surface": row.tooth_surface,
            "status": row.status, "created_at": _iso(row.created_at), "linked_item_id": linked.get(row.id),
        } for row in procedures],
    }


@router.post("/patients/{patient_id}", status_code=status.HTTP_201_CREATED)
async def create_plan(
    patient_id: UUID,
    payload: PlanCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    await _patient(db, patient_id, user)
    key = _uuid_key(idempotency_key)
    if key:
        existing = await db.scalar(select(TreatmentPlan).where(
            TreatmentPlan.clinic_id == user.clinic_id, TreatmentPlan.created_by_id == user.id,
            TreatmentPlan.idempotency_key == key))
        if existing:
            if existing.patient_id != patient_id or existing.title != payload.title:
                raise HTTPException(409, "Idempotency-Key was already used with a different request")
            return await _plan_response(db, existing)
    now = utcnow()
    plan = TreatmentPlan(clinic_id=user.clinic_id, patient_id=patient_id, title=payload.title,
                         notes=_clean(payload.notes), status="draft", version=1, created_by_id=user.id,
                         idempotency_key=key, created_at=now, updated_at=now)
    db.add(plan)
    await db.flush()
    for stage_index, stage_payload in enumerate(payload.stages, start=1):
        stage = TreatmentPlanStage(clinic_id=user.clinic_id, plan_id=plan.id, position=stage_index,
                                   title=stage_payload.title, notes=_clean(stage_payload.notes),
                                   created_at=now, updated_at=now)
        db.add(stage)
        await db.flush()
        for item_index, item_payload in enumerate(stage_payload.items, start=1):
            await _new_item(db, plan, stage, item_payload, user, item_index)
    await _record(db, plan, user, "created", details={"stages": len(payload.stages)})
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if key:
            existing = await db.scalar(select(TreatmentPlan).where(
                TreatmentPlan.clinic_id == user.clinic_id, TreatmentPlan.created_by_id == user.id,
                TreatmentPlan.idempotency_key == key))
            if existing and existing.patient_id == patient_id:
                return await _plan_response(db, existing)
        raise HTTPException(409, "Treatment plan could not be saved") from exc
    return await _respond(db, plan)


@router.get("/{plan_id}")
async def get_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    return await _plan_response(db, await _plan(db, plan_id, user))


@router.patch("/{plan_id}")
async def update_plan(
    plan_id: UUID,
    payload: PlanPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is None:
        raise HTTPException(422, "Title cannot be empty")
    if "notes" in changes:
        changes["notes"] = _clean(changes["notes"])
    changed = {field: {"from": getattr(plan, field), "to": value}
               for field, value in changes.items() if getattr(plan, field) != value}
    if changed:
        _content_changed(plan)
        for field, value in changes.items():
            setattr(plan, field, value)
        await _record(db, plan, user, "plan_edited", details={"fields": sorted(changed)})
        await db.commit()
    return await _respond(db, plan)


@router.post("/{plan_id}/stages", status_code=status.HTTP_201_CREATED)
async def add_stage(
    plan_id: UUID,
    payload: StageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    position = (await db.scalar(select(func.max(TreatmentPlanStage.position)).where(
        TreatmentPlanStage.plan_id == plan.id)) or 0) + 1
    now = utcnow()
    stage = TreatmentPlanStage(clinic_id=plan.clinic_id, plan_id=plan.id, position=position, title=payload.title,
                               notes=_clean(payload.notes), created_at=now, updated_at=now)
    db.add(stage)
    await db.flush()
    for index, item_payload in enumerate(payload.items, start=1):
        await _new_item(db, plan, stage, item_payload, user, index)
    _content_changed(plan)
    await _record(db, plan, user, "stage_added", details={"stage_id": str(stage.id), "title": stage.title})
    await db.commit()
    return await _respond(db, plan)


@router.patch("/{plan_id}/stages/{stage_id}")
async def update_stage(
    plan_id: UUID,
    stage_id: UUID,
    payload: StagePatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    stage = await _stage(db, plan, stage_id)
    changed: list[str] = []
    changes = payload.model_dump(exclude_unset=True, exclude={"move"})
    if "title" in changes and changes["title"] is None:
        raise HTTPException(422, "Title cannot be empty")
    for field, value in changes.items():
        value = _clean(value) if field == "notes" else value
        if getattr(stage, field) != value:
            setattr(stage, field, value)
            changed.append(field)
    if payload.move:
        siblings = list(await db.scalars(select(TreatmentPlanStage).where(
            TreatmentPlanStage.plan_id == plan.id).order_by(TreatmentPlanStage.position, TreatmentPlanStage.id)))
        index = next(i for i, row in enumerate(siblings) if row.id == stage.id)
        other = index - 1 if payload.move == "up" else index + 1
        if 0 <= other < len(siblings):
            siblings[index], siblings[other] = siblings[other], siblings[index]
            for position, row in enumerate(siblings, start=1):
                row.position = position
            changed.append("position")
    if changed:
        stage.updated_at = utcnow()
        _content_changed(plan)
        await _record(db, plan, user, "stage_edited", details={"stage_id": str(stage.id), "fields": changed})
        await db.commit()
    return await _respond(db, plan)


@router.delete("/{plan_id}/stages/{stage_id}")
async def delete_stage(
    plan_id: UUID,
    stage_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    stage = await _stage(db, plan, stage_id)
    count = await db.scalar(select(func.count()).select_from(TreatmentPlanItem).where(
        TreatmentPlanItem.stage_id == stage.id))
    if count:
        raise HTTPException(409, "Only empty stages can be deleted; delete or cancel its items first")
    await db.delete(stage)
    await db.flush()
    remaining = list(await db.scalars(select(TreatmentPlanStage).where(
        TreatmentPlanStage.plan_id == plan.id).order_by(TreatmentPlanStage.position, TreatmentPlanStage.id)))
    for position, row in enumerate(remaining, start=1):
        row.position = position
    _content_changed(plan)
    await _record(db, plan, user, "stage_deleted", details={"stage_id": str(stage_id), "title": stage.title})
    await db.commit()
    return await _respond(db, plan)


@router.post("/{plan_id}/stages/{stage_id}/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    plan_id: UUID,
    stage_id: UUID,
    payload: ItemCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    stage = await _stage(db, plan, stage_id)
    key = _uuid_key(idempotency_key)
    if key:
        existing = await db.scalar(select(TreatmentPlanItem).where(
            TreatmentPlanItem.plan_id == plan.id, TreatmentPlanItem.idempotency_key == key))
        if existing:
            if existing.stage_id != stage.id or existing.procedure_catalog_id != payload.procedure_catalog_id:
                raise HTTPException(409, "Idempotency-Key was already used with a different request")
            return await _respond(db, plan)
    position = (await db.scalar(select(func.max(TreatmentPlanItem.position)).where(
        TreatmentPlanItem.stage_id == stage.id)) or 0) + 1
    item = await _new_item(db, plan, stage, payload, user, position, key)
    _content_changed(plan)
    await db.flush()
    await _record(db, plan, user, "item_added", item_id=item.id, details={
        "procedure_code": item.procedure_code, "tooth_fdi": item.tooth_fdi, "stage_id": str(stage.id)})
    await db.commit()
    return await _respond(db, plan)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: UUID,
    payload: ItemPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    _require_editable(plan)
    if item.status == "cancelled":
        raise HTTPException(409, "Cancelled items cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    for field in ("priority", "estimated_price", "estimated_duration_minutes", "stage_id"):
        if field in changes and changes[field] is None:
            raise HTTPException(422, f"{field} cannot be null")
    location_fields = {"tooth_fdi", "tooth_surface"} & changes.keys()
    if location_fields and item.performed_procedure_id:
        raise HTTPException(409, "Unlink the performed procedure before changing the planned location")
    tooth = changes.get("tooth_fdi", item.tooth_fdi)
    surface = changes.get("tooth_surface", item.tooth_surface)
    _validate_location(tooth, surface)
    if location_fields:
        # A changed location must respect the service rule; untouched locations are never re-validated.
        catalog = await db.scalar(select(ProcedureCatalog).where(
            ProcedureCatalog.id == item.procedure_catalog_id, ProcedureCatalog.clinic_id == plan.clinic_id))
        if catalog:
            _validate_allowed_location(catalog, tooth, surface)
    if "stage_id" in changes and changes["stage_id"] != item.stage_id:
        await _stage(db, plan, changes["stage_id"])
        item.position = (await db.scalar(select(func.max(TreatmentPlanItem.position)).where(
            TreatmentPlanItem.stage_id == changes["stage_id"])) or 0) + 1
    if "notes" in changes:
        changes["notes"] = _clean(changes["notes"])
    changed = {}
    for field, value in changes.items():
        if getattr(item, field) != value:
            previous = getattr(item, field)
            changed[field] = {"from": str(previous) if previous is not None else None,
                              "to": str(value) if value is not None else None}
            setattr(item, field, value)
    if changed:
        item.updated_at = utcnow()
        _content_changed(plan)
        await _record(db, plan, user, "item_edited", item_id=item.id, details={"changes": changed})
        await db.commit()
    return await _respond(db, plan)


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    _require_editable(plan)
    if item.appointment_id or item.performed_procedure_id:
        raise HTTPException(409, "Linked items cannot be deleted; unlink or cancel them explicitly")
    details = {"procedure_code": item.procedure_code, "tooth_fdi": item.tooth_fdi}
    await db.delete(item)
    _content_changed(plan)
    await _record(db, plan, user, "item_deleted", item_id=item_id, details=details)
    await db.commit()
    return await _respond(db, plan)


@router.post("/items/{item_id}/cancel")
async def cancel_item(
    item_id: UUID,
    payload: ReasonInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    _require_editable(plan)
    if item.status == "cancelled":
        raise HTTPException(409, "Item is already cancelled")
    if item.performed_procedure_id:
        raise HTTPException(409, "Unlink the performed procedure before cancelling this planned item")
    now = utcnow()
    item.status, item.cancel_reason, item.cancelled_at, item.cancelled_by_id = "cancelled", payload.reason, now, user.id
    item.updated_at = now
    plan.updated_at = now
    await _record(db, plan, user, "item_cancelled", item_id=item.id, details={"reason": payload.reason})
    await db.commit()
    return await _respond(db, plan)


@router.put("/items/{item_id}/appointment")
async def link_appointment(
    item_id: UUID,
    payload: AppointmentLink,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    _require_editable(plan)
    if item.status == "cancelled":
        raise HTTPException(409, "Cancelled items cannot be scheduled")
    if payload.appointment_id == item.appointment_id:
        return await _respond(db, plan)
    previous = item.appointment_id
    if payload.appointment_id is not None:
        appointment = await db.scalar(select(Appointment).where(
            Appointment.id == payload.appointment_id, Appointment.clinic_id == user.clinic_id))
        if not appointment or appointment.patient_id != plan.patient_id:
            raise HTTPException(404, "Appointment not found for this patient")
        if appointment.status in LINKABLE_APPOINTMENT_STATUSES_EXCLUDED:
            raise HTTPException(409, f"A {appointment.status} appointment cannot be linked")
    now = utcnow()
    item.appointment_id = payload.appointment_id
    item.appointment_linked_by_id = user.id if payload.appointment_id else None
    item.appointment_linked_at = now if payload.appointment_id else None
    item.updated_at = plan.updated_at = now
    await _record(db, plan, user, "appointment_linked" if payload.appointment_id else "appointment_unlinked",
                  item_id=item.id, details={"appointment_id": str(payload.appointment_id) if payload.appointment_id else None,
                                            "previous_appointment_id": str(previous) if previous else None})
    await db.commit()
    return await _respond(db, plan)


@router.put("/items/{item_id}/performed-procedure")
async def link_performed_procedure(
    item_id: UUID,
    payload: PerformedLink,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    if plan.status not in {"accepted", "in_progress"}:
        raise HTTPException(409, "Performed procedures can be linked only to an accepted plan")
    if item.status == "cancelled":
        raise HTTPException(409, "Cancelled items cannot be fulfilled")
    if item.performed_procedure_id == payload.performed_procedure_id:
        return await _respond(db, plan)
    if item.performed_procedure_id is not None:
        raise HTTPException(409, "This item is already linked; unlink it first")
    procedure = await db.scalar(select(PerformedProcedure).where(
        PerformedProcedure.id == payload.performed_procedure_id, PerformedProcedure.clinic_id == user.clinic_id))
    if not procedure or procedure.patient_id != plan.patient_id:
        raise HTTPException(404, "Performed procedure not found for this patient")
    if procedure.status == "cancelled":
        raise HTTPException(409, "A cancelled performed procedure cannot fulfil a planned item")
    if procedure.procedure_catalog_id != item.procedure_catalog_id:
        raise HTTPException(422, "Performed procedure uses a different catalog service than the planned item")
    if (procedure.tooth_fdi or None) != item.tooth_fdi or _surface_set(procedure.tooth_surface) != _surface_set(item.tooth_surface):
        raise HTTPException(422, "Performed procedure location (tooth/surfaces) does not match the planned item")
    other = await db.scalar(select(TreatmentPlanItem.id).where(
        TreatmentPlanItem.performed_procedure_id == procedure.id, TreatmentPlanItem.id != item.id))
    if other:
        raise HTTPException(409, "This performed procedure already fulfils another plan item")
    now = utcnow()
    item.performed_procedure_id = procedure.id
    item.performed_linked_by_id = user.id
    item.performed_linked_at = now
    item.updated_at = plan.updated_at = now
    if plan.status == "accepted":
        plan.status = "in_progress"
    try:
        await _record(db, plan, user, "performed_linked", item_id=item.id, details={
            "performed_procedure_id": str(procedure.id), "performed_status": procedure.status})
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "This performed procedure already fulfils another plan item") from exc
    return await _respond(db, plan)


@router.delete("/items/{item_id}/performed-procedure")
async def unlink_performed_procedure(
    item_id: UUID,
    payload: ReasonInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan, item = await _item(db, item_id, user)
    _require_editable(plan)
    if item.performed_procedure_id is None:
        raise HTTPException(409, "Item has no linked performed procedure")
    previous = item.performed_procedure_id
    item.performed_procedure_id = item.performed_linked_by_id = item.performed_linked_at = None
    item.updated_at = plan.updated_at = utcnow()
    await _record(db, plan, user, "performed_unlinked", item_id=item.id, details={
        "performed_procedure_id": str(previous), "reason": payload.reason})
    await db.commit()
    return await _respond(db, plan)


@router.post("/{plan_id}/accept")
async def accept_plan(
    plan_id: UUID,
    payload: AcceptInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    if payload.expected_version is not None and payload.expected_version != plan.version:
        raise HTTPException(409, f"Plan changed to version {plan.version}; review it before accepting")
    if plan.accepted_version == plan.version:
        raise HTTPException(409, f"Version {plan.version} is already accepted")
    stages, items, performed, _ = await _load_content(db, plan)
    if not any(item.status == "active" for item in items):
        raise HTTPException(422, "A plan needs at least one active planned item before acceptance")
    now = utcnow()
    db.add(TreatmentPlanAcceptance(clinic_id=plan.clinic_id, plan_id=plan.id, version=plan.version,
                                   accepted_by_id=user.id, accepted_at=now, note=payload.note,
                                   snapshot=_snapshot(plan, stages, items, performed)))
    plan.accepted_version = plan.version
    plan.status = "in_progress" if any(item.performed_procedure_id for item in items) else "accepted"
    plan.updated_at = now
    try:
        await _record(db, plan, user, "accepted", details={"version": plan.version, "note": payload.note})
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "This plan version was already accepted") from exc
    return await _respond(db, plan)


@router.post("/{plan_id}/complete")
async def complete_plan(
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    plan = await _plan(db, plan_id, user, lock=True)
    _, items, performed, _ = await _load_content(db, plan)
    blocker = _completion_blocker(plan, [_item_progress(item, performed.get(item.performed_procedure_id)) for item in items])
    if blocker:
        raise HTTPException(409, blocker)
    now = utcnow()
    plan.status, plan.completed_at, plan.completed_by_id, plan.updated_at = "completed", now, user.id, now
    await _record(db, plan, user, "completed")
    await db.commit()
    return await _respond(db, plan)


@router.post("/{plan_id}/cancel")
async def cancel_plan(
    plan_id: UUID,
    payload: ReasonInput,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*PLAN_ROLES)),
) -> dict:
    """Cancels the plan only; appointments and performed procedures stay untouched."""
    plan = await _plan(db, plan_id, user, lock=True)
    _require_editable(plan)
    now = utcnow()
    plan.status, plan.cancelled_at, plan.cancelled_by_id = "cancelled", now, user.id
    plan.cancel_reason, plan.updated_at = payload.reason, now
    await _record(db, plan, user, "cancelled", details={"reason": payload.reason})
    await db.commit()
    return await _respond(db, plan)
