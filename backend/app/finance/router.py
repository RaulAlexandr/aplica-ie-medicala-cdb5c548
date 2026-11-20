"""T13 internal patient account: services owed (projection of completed T04 procedures) and an
immutable ledger of money movements (payments, refunds, manager adjustments and reversals).

This is internal bookkeeping for the pilot. It is NOT fiscal invoicing, a fiscal receipt, an
accounting integration or an online payment. One currency (RON, the catalog currency).

Balance = completed services + adjustment_debit - adjustment_credit - payment + refund, where a
reversal neutralizes the movement it references. A negative balance is a patient credit/advance.

Roles: payments, refunds and their reversals: reception, clinic_manager, administrator.
Adjustments and their reversals: clinic_manager, administrator. Doctors and assistants read only.
The daily report is for reception, clinic_manager and administrator. Other clinics get 404.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    AuditEvent,
    Clinic,
    Patient,
    PatientFinancialEntry,
    PerformedProcedure,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/finance", tags=["finance"])

CURRENCY = "RON"
STAFF_ROLES = ("clinic_manager", "doctor", "assistant", "reception", "administrator")
CASH_ROLES = ("reception", "clinic_manager", "administrator")
ADJUST_ROLES = ("clinic_manager", "administrator")
CASH_KINDS = ("payment", "refund")
ADJUST_KINDS = ("adjustment_credit", "adjustment_debit")
UNRESOLVED_STATUSES = ("pending", "running", "paused")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
_AMOUNT_RE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")


def _iso(value: datetime | None) -> str | None:
    """Explicit UTC offset on every T13 timestamp (SQLite returns naive values stored as UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _money(value: Decimal) -> str:
    return str(Decimal(value).quantize(CENT))


def parse_amount(value: object) -> Decimal:
    # Numbers are refused (exactness): only decimal strings. Raised as ValueError so the API answers 422.
    text = value.strip() if isinstance(value, str) else None
    if text is None or not _AMOUNT_RE.match(text):
        raise ValueError("Amount must be a decimal string with at most two decimals, such as \"150.00\"")
    amount = Decimal(text)
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    return amount.quantize(CENT)


def _clean_reason(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["payment", "refund", "adjustment_credit", "adjustment_debit"]
    amount: Decimal
    currency: str = CURRENCY
    method: Literal["cash", "card", "transfer", "other"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    appointment_id: UUID | None = None
    client_request_id: UUID

    @field_validator("amount", mode="before")
    @classmethod
    def amount_string(cls, value: object) -> Decimal:
        return parse_amount(value)

    @field_validator("currency")
    @classmethod
    def single_currency(cls, value: str) -> str:
        if value.strip().upper() != CURRENCY:
            raise ValueError("Only RON is supported for the patient account")
        return CURRENCY

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        return _clean_reason(value)

    @model_validator(mode="after")
    def kind_rules(self) -> "EntryCreate":
        if self.kind in CASH_KINDS and self.method is None:
            raise ValueError("A payment or refund needs a method (cash, card, transfer or other)")
        if self.kind not in CASH_KINDS and self.method is not None:
            raise ValueError("Adjustments do not have a payment method")
        if self.kind != "payment" and not self.reason:
            raise ValueError("A reason is required for refunds and adjustments")
        return self


class ReverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)
    client_request_id: UUID

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = _clean_reason(value)
        if not cleaned:
            raise ValueError("Reason must not be blank")
        return cleaned


async def _patient(db: AsyncSession, clinic_id: UUID, patient_id: UUID) -> Patient:
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id))
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


async def _clinic_timezone(db: AsyncSession, clinic_id: UUID) -> str:
    clinic = await db.get(Clinic, clinic_id)
    return clinic.timezone if clinic else "UTC"


async def _names(db: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in rows}


async def _lock_entry(db: AsyncSession, clinic_id: UUID, entry_id: UUID) -> PatientFinancialEntry | None:
    """Load the movement to reverse with a row lock (PostgreSQL); concurrent reversals serialize here."""
    return await db.scalar(
        select(PatientFinancialEntry)
        .where(PatientFinancialEntry.id == entry_id, PatientFinancialEntry.clinic_id == clinic_id)
        .with_for_update()
    )


async def _after_idempotency_check() -> None:
    """Controlled point used by the PostgreSQL concurrency test; no-op in production."""


async def _by_request(db: AsyncSession, clinic_id: UUID, client_request_id: str) -> PatientFinancialEntry | None:
    return await db.scalar(select(PatientFinancialEntry).where(
        PatientFinancialEntry.clinic_id == clinic_id, PatientFinancialEntry.client_request_id == client_request_id))


def _same_movement(entry: PatientFinancialEntry, *, patient_id: UUID, kind: str, amount: Decimal,
                   method: str | None, appointment_id: UUID | None, reverses_entry_id: UUID | None,
                   reason: str | None) -> bool:
    # `reason` is the normalised text (stripped, blank -> None) on both sides, so a retry that only differs
    # in surrounding whitespace is still the same request, while another reason is a different movement (409).
    return (entry.patient_id == patient_id and entry.kind == kind and Decimal(entry.amount) == amount
            and entry.method == method and entry.appointment_id == appointment_id
            and entry.reverses_entry_id == reverses_entry_id and entry.reason == reason)


async def _entry_out(db: AsyncSession, entry: PatientFinancialEntry) -> dict:
    names = await _names(db, {entry.created_by_id})
    return {
        "id": str(entry.id), "patient_id": str(entry.patient_id), "kind": entry.kind,
        "amount": _money(entry.amount), "currency": entry.currency, "method": entry.method, "reason": entry.reason,
        "appointment_id": str(entry.appointment_id) if entry.appointment_id else None,
        "reverses_entry_id": str(entry.reverses_entry_id) if entry.reverses_entry_id else None,
        "client_request_id": entry.client_request_id, "created_at": _iso(entry.created_at),
        "created_by": {"id": str(entry.created_by_id), "name": names.get(entry.created_by_id)},
    }


def _replay_or_conflict(existing: PatientFinancialEntry, response: Response, **movement) -> None:
    if not _same_movement(existing, **movement):
        raise HTTPException(status.HTTP_409_CONFLICT, "client_request_id was already used for a different movement")
    response.status_code = status.HTTP_200_OK


@router.post("/patients/{patient_id}/entries", status_code=status.HTTP_201_CREATED)
async def create_entry(patient_id: UUID, payload: EntryCreate, response: Response,
                       db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))) -> dict:
    # Plain values taken before anything can roll the session back: rollback expires `user`, and reading an
    # expired attribute afterwards would try a lazy load outside the async greenlet (MissingGreenlet).
    clinic_id, actor_id, role = user.clinic_id, user.id, user.role
    allowed = CASH_ROLES if payload.kind in CASH_KINDS else ADJUST_ROLES
    if role not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot record this kind of movement")
    await _patient(db, clinic_id, patient_id)
    request_id = str(payload.client_request_id)
    movement = {"patient_id": patient_id, "kind": payload.kind, "amount": payload.amount, "method": payload.method,
                "appointment_id": payload.appointment_id, "reverses_entry_id": None, "reason": payload.reason}
    existing = await _by_request(db, clinic_id, request_id)
    if existing:
        _replay_or_conflict(existing, response, **movement)
        return await _entry_out(db, existing)
    if payload.appointment_id is not None:
        appointment = await db.scalar(select(Appointment).where(
            Appointment.id == payload.appointment_id, Appointment.clinic_id == clinic_id))
        if not appointment or appointment.patient_id != patient_id:
            raise HTTPException(422, "The visit does not belong to this patient")
    await _after_idempotency_check()
    entry = PatientFinancialEntry(
        clinic_id=clinic_id, patient_id=patient_id, appointment_id=payload.appointment_id, kind=payload.kind,
        amount=payload.amount, currency=CURRENCY, method=payload.method, reason=payload.reason,
        client_request_id=request_id, created_by_id=actor_id, created_at=utcnow())
    db.add(entry)
    try:
        await db.flush()
        db.add(AuditEvent(clinic_id=clinic_id, actor_id=actor_id, entity_type="financial_entry",
                          entity_id=entry.id, action=payload.kind[:40]))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _by_request(db, clinic_id, request_id)
        if not existing:
            raise
        _replay_or_conflict(existing, response, **movement)
        return await _entry_out(db, existing)
    return await _entry_out(db, entry)


@router.post("/entries/{entry_id}/reverse", status_code=status.HTTP_201_CREATED)
async def reverse_entry(entry_id: UUID, payload: ReverseRequest, response: Response,
                        db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*STAFF_ROLES))) -> dict:
    clinic_id, actor_id, role = user.clinic_id, user.id, user.role  # plain values: see create_entry
    if role not in CASH_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot reverse movements")
    request_id = str(payload.client_request_id)
    target = await _lock_entry(db, clinic_id, entry_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Movement not found")
    movement = {"patient_id": target.patient_id, "kind": "reversal", "amount": Decimal(target.amount), "method": None,
                "appointment_id": target.appointment_id, "reverses_entry_id": target.id, "reason": payload.reason}
    existing = await _by_request(db, clinic_id, request_id)
    if existing:
        _replay_or_conflict(existing, response, **movement)
        return await _entry_out(db, existing)
    if target.kind == "reversal":
        raise HTTPException(status.HTTP_409_CONFLICT, "A reversal cannot be reversed")
    if target.kind in ADJUST_KINDS and role not in ADJUST_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a manager can reverse an adjustment")
    already = await db.scalar(select(PatientFinancialEntry.id).where(PatientFinancialEntry.reverses_entry_id == target.id))
    if already:
        raise HTTPException(status.HTTP_409_CONFLICT, "This movement has already been reversed")
    reversal = PatientFinancialEntry(
        clinic_id=clinic_id, patient_id=target.patient_id, appointment_id=target.appointment_id, kind="reversal",
        amount=target.amount, currency=target.currency, method=None, reason=payload.reason,
        reverses_entry_id=target.id, client_request_id=request_id, created_by_id=actor_id, created_at=utcnow())
    db.add(reversal)
    try:
        await db.flush()
        db.add(AuditEvent(clinic_id=clinic_id, actor_id=actor_id, entity_type="financial_entry",
                          entity_id=reversal.id, action="reversal"))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _by_request(db, clinic_id, request_id)
        if existing:
            _replay_or_conflict(existing, response, **movement)
            return await _entry_out(db, existing)
        raise HTTPException(status.HTTP_409_CONFLICT, "This movement has already been reversed") from None
    return await _entry_out(db, reversal)


def _effective_price(procedure: PerformedProcedure) -> Decimal:
    price = procedure.current_price if procedure.current_price is not None else procedure.procedure_base_price
    return Decimal(price).quantize(CENT)


def _ledger_totals(entries: list[PatientFinancialEntry]) -> dict[str, Decimal]:
    reversed_ids = {e.reverses_entry_id for e in entries if e.kind == "reversal"}
    sums = {kind: ZERO for kind in ("payment", "refund", "adjustment_credit", "adjustment_debit")}
    for entry in entries:
        if entry.kind == "reversal" or entry.id in reversed_ids:
            continue
        sums[entry.kind] += Decimal(entry.amount)
    return sums


@router.get("/patients/{patient_id}/account")
async def patient_account(patient_id: UUID, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_roles(*STAFF_ROLES))) -> dict:
    await _patient(db, user.clinic_id, patient_id)
    timezone = await _clinic_timezone(db, user.clinic_id)
    procedures = (await db.scalars(select(PerformedProcedure).where(
        PerformedProcedure.clinic_id == user.clinic_id, PerformedProcedure.patient_id == patient_id,
    ).order_by(PerformedProcedure.created_at))).all()
    appointment_ids = {p.appointment_id for p in procedures}
    entries = (await db.scalars(select(PatientFinancialEntry).where(
        PatientFinancialEntry.clinic_id == user.clinic_id, PatientFinancialEntry.patient_id == patient_id,
    ).order_by(PatientFinancialEntry.created_at, PatientFinancialEntry.id))).all()
    appointment_ids |= {e.appointment_id for e in entries if e.appointment_id}
    appointments = {}
    if appointment_ids:
        appointments = {a.id: a for a in (await db.scalars(select(Appointment).where(
            Appointment.id.in_(appointment_ids), Appointment.clinic_id == user.clinic_id))).all()}

    visits: dict[UUID, dict] = {}
    services_total = ZERO
    in_progress = cancelled = 0
    for procedure in procedures:
        appointment = appointments.get(procedure.appointment_id)
        visit = visits.setdefault(procedure.appointment_id, {
            "appointment_id": str(procedure.appointment_id),
            "starts_at": _iso(appointment.starts_at) if appointment else None,
            "appointment_type": appointment.appointment_type if appointment else None,
            "appointment_status": appointment.status if appointment else None,
            "completed_total": ZERO, "procedures": []})
        counted = procedure.status == "completed"
        price = _effective_price(procedure)
        if counted:
            services_total += price
            visit["completed_total"] += price
        elif procedure.status in UNRESOLVED_STATUSES:
            in_progress += 1
        elif procedure.status == "cancelled":
            cancelled += 1
        visit["procedures"].append({
            "id": str(procedure.id), "code": procedure.procedure_code, "name": procedure.procedure_name,
            "tooth_fdi": procedure.tooth_fdi, "status": procedure.status, "counted": counted,
            "amount": _money(price) if counted else None,
            "price_overridden": Decimal(procedure.procedure_base_price).quantize(CENT) != price,
        })
    for visit in visits.values():
        visit["completed_total"] = _money(visit["completed_total"])

    sums = _ledger_totals(list(entries))
    balance = services_total + sums["adjustment_debit"] - sums["adjustment_credit"] - sums["payment"] + sums["refund"]
    reversals = {e.reverses_entry_id: e for e in entries if e.kind == "reversal"}
    names = await _names(db, {e.created_by_id for e in entries})
    entry_rows = []
    for entry in entries:
        reversal = reversals.get(entry.id)
        entry_rows.append({
            "id": str(entry.id), "kind": entry.kind, "amount": _money(entry.amount), "currency": entry.currency,
            "method": entry.method, "reason": entry.reason,
            "appointment_id": str(entry.appointment_id) if entry.appointment_id else None,
            "reverses_entry_id": str(entry.reverses_entry_id) if entry.reverses_entry_id else None,
            "created_at": _iso(entry.created_at),
            "created_by": {"id": str(entry.created_by_id), "name": names.get(entry.created_by_id)},
            "reversed_by": None if not reversal else {
                "id": str(reversal.id), "created_at": _iso(reversal.created_at),
                "by": names.get(reversal.created_by_id), "reason": reversal.reason},
        })
    return {
        "patient_id": str(patient_id), "currency": CURRENCY, "timezone": timezone,
        "totals": {
            "services_completed": _money(services_total), "payments": _money(sums["payment"]),
            "refunds": _money(sums["refund"]), "paid_net": _money(sums["payment"] - sums["refund"]),
            "adjustments_debit": _money(sums["adjustment_debit"]), "adjustments_credit": _money(sums["adjustment_credit"]),
            "balance": _money(balance),
            "balance_status": "due" if balance > 0 else "credit" if balance < 0 else "settled",
        },
        "services": {"visits": list(visits.values()), "in_progress_count": in_progress, "cancelled_count": cancelled},
        "entries": entry_rows,
        "permissions": {"can_record_cash": user.role in CASH_ROLES, "can_adjust": user.role in ADJUST_ROLES},
    }


@router.get("/appointments/{appointment_id}/summary")
async def visit_summary(appointment_id: UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(require_roles(*STAFF_ROLES))) -> dict:
    appointment = await db.scalar(select(Appointment).where(
        Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id))
    if not appointment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    procedures = (await db.scalars(select(PerformedProcedure).where(
        PerformedProcedure.clinic_id == user.clinic_id, PerformedProcedure.appointment_id == appointment_id))).all()
    entries = (await db.scalars(select(PatientFinancialEntry).where(
        PatientFinancialEntry.clinic_id == user.clinic_id, PatientFinancialEntry.patient_id == appointment.patient_id))).all()
    visit_entries = [e for e in entries if e.appointment_id == appointment_id]
    reversed_ids = {e.reverses_entry_id for e in entries if e.kind == "reversal"}
    paid = sum((Decimal(e.amount) * (1 if e.kind == "payment" else -1) for e in visit_entries
                if e.kind in CASH_KINDS and e.id not in reversed_ids), ZERO)
    completed = sum((_effective_price(p) for p in procedures if p.status == "completed"), ZERO)
    return {
        "appointment_id": str(appointment.id), "patient_id": str(appointment.patient_id), "currency": CURRENCY,
        "starts_at": _iso(appointment.starts_at), "services_completed": _money(completed),
        "completed_count": sum(1 for p in procedures if p.status == "completed"),
        "in_progress_count": sum(1 for p in procedures if p.status in UNRESOLVED_STATUSES),
        "paid_net_for_visit": _money(paid),
        "permissions": {"can_record_cash": user.role in CASH_ROLES},
    }


@router.get("/daily")
async def daily_report(day: date = Query(alias="date"), db: AsyncSession = Depends(get_db),
                       user: User = Depends(require_roles(*CASH_ROLES))) -> dict:
    timezone = await _clinic_timezone(db, user.clinic_id)
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    rows = (await db.scalars(select(PatientFinancialEntry).where(
        PatientFinancialEntry.clinic_id == user.clinic_id,
        PatientFinancialEntry.created_at >= start, PatientFinancialEntry.created_at < end,
        PatientFinancialEntry.kind.in_(("payment", "refund", "reversal")),
    ).order_by(PatientFinancialEntry.created_at, PatientFinancialEntry.id))).all()
    target_ids = {r.reverses_entry_id for r in rows if r.kind == "reversal"}
    targets = {}
    if target_ids:
        targets = {t.id: t for t in (await db.scalars(select(PatientFinancialEntry).where(
            PatientFinancialEntry.id.in_(target_ids), PatientFinancialEntry.clinic_id == user.clinic_id))).all()}
    patient_ids = {r.patient_id for r in rows}
    patients = {}
    if patient_ids:
        patients = {p.id: f"{p.first_name} {p.last_name}" for p in (await db.scalars(select(Patient).where(
            Patient.id.in_(patient_ids), Patient.clinic_id == user.clinic_id))).all()}
    names = await _names(db, {r.created_by_id for r in rows})
    by_method = {method: ZERO for method in ("cash", "card", "transfer", "other")}
    received = refunded = reversed_total = ZERO
    out = []
    for row in rows:
        if row.kind == "reversal":
            target = targets.get(row.reverses_entry_id)
            if not target or target.kind not in CASH_KINDS:
                continue  # reversals of adjustments do not move money
            method = target.method
            signed = -Decimal(row.amount) if target.kind == "payment" else Decimal(row.amount)
            reversed_total += signed
        else:
            method = row.method
            signed = Decimal(row.amount) if row.kind == "payment" else -Decimal(row.amount)
            if row.kind == "payment":
                received += signed
            else:
                refunded += -signed
        by_method[method] += signed
        out.append({
            "id": str(row.id), "kind": row.kind, "method": method, "signed_amount": _money(signed),
            "patient_id": str(row.patient_id), "patient_name": patients.get(row.patient_id),
            "reverses_entry_id": str(row.reverses_entry_id) if row.reverses_entry_id else None,
            "reason": row.reason, "created_at": _iso(row.created_at), "created_by": names.get(row.created_by_id),
        })
    return {
        "date": day.isoformat(), "timezone": timezone, "currency": CURRENCY,
        "range": {"start": _iso(start), "end": _iso(end)},
        "by_method": {key: _money(value) for key, value in by_method.items()},
        "received": _money(received), "refunded": _money(refunded), "reversed": _money(reversed_total),
        "net": _money(sum(by_method.values(), ZERO)), "entries": out,
    }
