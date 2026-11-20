"""Read-only operational and internal financial reports (T24 v1)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    Clinic,
    Patient,
    PatientFinancialEntry,
    PerformedProcedure,
    ProcedureExecutionSegment,
    Room,
    TreatmentPlan,
    TreatmentPlanAcceptance,
    TreatmentPlanItem,
    User,
    WorkingHours,
    get_db,
)

router = APIRouter(prefix="/reports", tags=["reports"])
REPORT_ROLES = ("clinic_manager", "administrator")
ACTIVE_APPOINTMENTS = {"scheduled", "confirmed", "arrived", "in_progress"}
INDICATORS = {
    "appointments_total", "appointments_cancelled", "appointments_no_show",
    "appointments_completed", "cancellation_rate", "no_show_rate",
    "room_occupancy", "procedure_estimated_duration",
    "procedure_measured_duration", "procedure_manual_duration",
    "treatment_items_uncontinued", "services_performed",
    "receipts_net_by_method", "financial_reversals", "consumptions",
}


def _local_time(value: datetime, zone: ZoneInfo) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(zone).isoformat()


async def _scope(
    db: AsyncSession, clinic_id: UUID, start: date, end: date,
    doctor_id: UUID | None, room_id: UUID | None,
) -> tuple[Clinic, ZoneInfo, datetime, datetime]:
    if end < start or (end - start).days + 1 > 366:
        raise HTTPException(422, "Period must be 1 to 366 inclusive calendar days")
    clinic = await db.get(Clinic, clinic_id)
    if clinic is None:
        raise HTTPException(404, "Clinic not found")
    zone = ZoneInfo(clinic.timezone)
    if doctor_id is not None:
        doctor = await db.scalar(select(User).where(User.id == doctor_id, User.clinic_id == clinic_id))
        if doctor is None:
            raise HTTPException(404, "Doctor not found")
    if room_id is not None:
        room = await db.scalar(select(Room).where(Room.id == room_id, Room.clinic_id == clinic_id))
        if room is None:
            raise HTTPException(404, "Room not found")
    lower = datetime.combine(start, time.min, zone).astimezone(ZoneInfo("UTC"))
    upper = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(ZoneInfo("UTC"))
    return clinic, zone, lower, upper


def _metric_definition(key: str) -> tuple[str, str, str]:
    definitions = {
        "appointments_total": ("Programări începute în perioada locală, toate statusurile.", "programări", "appointments"),
        "appointments_cancelled": ("Programări cu status cancelled.", "programări", "appointments"),
        "appointments_no_show": ("Programări cu status no_show.", "programări", "appointments"),
        "appointments_completed": ("Programări cu status completed.", "programări", "appointments"),
        "cancellation_rate": ("Programări cancelled / toate programările × 100; nedefinit la total zero.", "%", "appointments"),
        "no_show_rate": ("Programări no_show / toate programările × 100; nedefinit la total zero.", "%", "appointments"),
        "room_occupancy": ("Minute active programate / minute disponibile din WorkingHours pentru cabinet.", "minute și %", "appointments, working_hours"),
        "procedure_estimated_duration": ("Suma duratelor implicite capturate în snapshot pentru manopere completed.", "secunde", "performed_procedures"),
        "procedure_measured_duration": ("Suma duratelor din segmente pentru manopere completed fără override manual.", "secunde", "procedure_execution_segments"),
        "procedure_manual_duration": ("Numărul manoperelor completed cu marker de durată manuală; exclus din măsurare.", "manopere", "performed_procedures"),
        "treatment_items_uncontinued": ("Itemi activi, neefectuați, din planuri cu versiune acceptată; vechime la to.", "itemi și zile", "treatment_plan_items, treatment_plan_acceptances"),
        "services_performed": ("Suma current_price (fallback procedure_base_price) pentru manopere completed.", "RON", "performed_procedures"),
        "receipts_net_by_method": ("Payment +, refund −, reversal payment −, reversal refund +; reversals de ajustare excluse.", "RON pe metodă", "patient_financial_entries"),
        "financial_reversals": ("Mișcări reversal înregistrate în perioadă, însumate ca magnitudine.", "mișcări și RON", "patient_financial_entries"),
        "consumptions": ("Indisponibil până la T17.", "indisponibil", "T17"),
    }
    return definitions[key]


async def _records(
    key: str, db: AsyncSession, clinic_id: UUID, lower: datetime, upper: datetime,
    zone: ZoneInfo, doctor_id: UUID | None, room_id: UUID | None,
) -> tuple[list[dict[str, Any]], Any, str | None]:
    if key.startswith("appointments_") or key in {"cancellation_rate", "no_show_rate", "room_occupancy"}:
        if key == "room_occupancy" and room_id is None:
            raise HTTPException(422, "room_id is required for room_occupancy")
        query = select(Appointment, Patient).join(Patient, Patient.id == Appointment.patient_id).where(
            Appointment.clinic_id == clinic_id, Appointment.starts_at >= lower, Appointment.starts_at < upper,
        )
        if doctor_id:
            query = query.where(Appointment.doctor_id == doctor_id)
        if room_id:
            query = query.where(Appointment.room_id == room_id)
        rows = list((await db.execute(query.order_by(Appointment.starts_at, Appointment.id))).all())
        records = [{"id": str(a.id), "entity": "appointment", "date": _local_time(a.starts_at, zone), "status": a.status,
                    "amount": None, "patient_name": f"{p.first_name} {p.last_name}"} for a, p in rows]
        if key == "appointments_total": value = len(rows)
        elif key == "appointments_cancelled":
            rows = [(a, p) for a, p in rows if a.status == "cancelled"]
            records = [r for r in records if r["status"] == "cancelled"]
            value = len(rows)
        elif key == "appointments_no_show":
            rows = [(a, p) for a, p in rows if a.status == "no_show"]
            records = [r for r in records if r["status"] == "no_show"]
            value = len(rows)
        elif key == "appointments_completed":
            rows = [(a, p) for a, p in rows if a.status == "completed"]
            records = [r for r in records if r["status"] == "completed"]
            value = len(rows)
        elif key == "cancellation_rate":
            denominator = len(rows)
            if denominator:
                value = round(sum(a.status == "cancelled" for a, _ in rows) * 100 / denominator, 2)
            else:
                value = {"status": "undefined", "reason": "Nu există programări în perioadă."}
        elif key == "no_show_rate":
            denominator = len(rows)
            if denominator:
                value = round(sum(a.status == "no_show" for a, _ in rows) * 100 / denominator, 2)
            else:
                value = {"status": "undefined", "reason": "Nu există programări în perioadă."}
        else:
            active_rows = [(a, p) for a, p in rows if a.status in ACTIVE_APPOINTMENTS]
            records = [{"id": str(a.id), "entity": "appointment", "date": _local_time(a.starts_at, zone),
                        "status": a.status, "amount": str(a.duration_minutes),
                        "patient_name": f"{p.first_name} {p.last_name}"} for a, p in active_rows]
            schedules = list((await db.scalars(select(WorkingHours).where(
                WorkingHours.clinic_id == clinic_id, WorkingHours.is_active.is_(True),
            ))).all())
            if not schedules:
                return records, {"status": "undefined", "reason": "Programul cabinetului nu este configurat."}, "Programul cabinetului nu este configurat."
            available = 0
            working_records = []
            day = lower.astimezone(zone).date()
            last_day = (upper - timedelta(microseconds=1)).astimezone(zone).date()
            while day <= last_day:
                for hours in schedules:
                    if hours.day_of_week == day.weekday():
                        starts = datetime.combine(day, hours.start_time, zone).astimezone(ZoneInfo("UTC"))
                        ends = datetime.combine(day, hours.end_time, zone).astimezone(ZoneInfo("UTC"))
                        minutes = max(0, int((ends - starts).total_seconds() // 60))
                        available += minutes
                        working_records.append({"id": str(hours.id), "entity": "working_hours",
                            "date": datetime.combine(day, time.min, zone).isoformat(), "status": "active",
                            "amount": str(minutes), "patient_name": None})
                day += timedelta(days=1)
            if available == 0:
                return records, {"status": "undefined", "reason": "Nu există minute lucrătoare configurate în perioadă."}, "Nu există minute lucrătoare configurate în perioadă."
            records.extend(working_records)
            occupied = sum(a.duration_minutes for a, _ in active_rows)
            value = {"scheduled_minutes": occupied, "available_minutes": available,
                     "occupancy_percent": round(occupied * 100 / available, 2)}
        if isinstance(value, dict) and value.get("status") == "undefined":
            return records, value, str(value.get("reason", "Valoarea nu poate fi calculată."))
        return records, value, None

    if key.startswith("procedure_") or key == "services_performed":
        query = select(PerformedProcedure, Appointment, Patient).join(
            Appointment, Appointment.id == PerformedProcedure.appointment_id,
        ).join(Patient, Patient.id == PerformedProcedure.patient_id).where(
            PerformedProcedure.clinic_id == clinic_id, PerformedProcedure.status == "completed",
            Appointment.starts_at >= lower, Appointment.starts_at < upper,
        )
        if doctor_id: query = query.where(Appointment.doctor_id == doctor_id)
        if room_id: query = query.where(Appointment.room_id == room_id)
        rows = list((await db.execute(query.order_by(Appointment.starts_at, PerformedProcedure.id))).all())
        records = []
        for procedure, appointment, patient in rows:
            amount = procedure.current_price if procedure.current_price is not None else procedure.procedure_base_price
            manual = bool(procedure.notes and "Manual duration override:" in procedure.notes)
            records.append({"id": str(procedure.id), "entity": "performed_procedure", "date": _local_time(appointment.starts_at, zone),
                            "status": procedure.status, "amount": str(amount),
                            "patient_name": f"{patient.first_name} {patient.last_name}",
                            "estimated_seconds": procedure.procedure_default_duration * 60,
                            "manual_duration": manual})
        if key == "services_performed": value = str(sum((Decimal(r["amount"]) for r in records), Decimal("0.00")))
        elif key == "procedure_estimated_duration": value = sum(r["estimated_seconds"] for r in records)
        elif key == "procedure_manual_duration":
            records = [r for r in records if r["manual_duration"]]
            value = len(records)
        else:
            measured_ids = [UUID(r["id"]) for r in records if not r["manual_duration"]]
            segments = list((await db.scalars(select(ProcedureExecutionSegment).where(
                ProcedureExecutionSegment.performed_procedure_id.in_(measured_ids),
                ProcedureExecutionSegment.segment_type == "run",
            ))).all()) if measured_ids else []
            sums: dict[UUID, int] = {}
            for seg in segments: sums[seg.performed_procedure_id] = sums.get(seg.performed_procedure_id, 0) + (seg.duration_seconds or 0)
            records = [r | {"measured_seconds": sums.get(UUID(r["id"]), 0)} for r in records if not r["manual_duration"]]
            value = sum(r["measured_seconds"] for r in records)
        return records, value, None

    if key in {"receipts_net_by_method", "financial_reversals"}:
        query = select(PatientFinancialEntry, Patient).join(Patient, Patient.id == PatientFinancialEntry.patient_id).where(
            PatientFinancialEntry.clinic_id == clinic_id, PatientFinancialEntry.created_at >= lower,
            PatientFinancialEntry.created_at < upper,
        )
        if doctor_id or room_id:
            appointment_query = select(Appointment.id).where(Appointment.clinic_id == clinic_id)
            if doctor_id: appointment_query = appointment_query.where(Appointment.doctor_id == doctor_id)
            if room_id: appointment_query = appointment_query.where(Appointment.room_id == room_id)
            query = query.where(PatientFinancialEntry.appointment_id.in_(appointment_query))
        rows = list((await db.execute(query.order_by(PatientFinancialEntry.created_at, PatientFinancialEntry.id))).all())
        records = [{"id": str(e.id), "entity": "patient_financial_entry", "date": _local_time(e.created_at, zone), "status": e.kind,
                    "amount": str(e.amount), "method": e.method, "patient_name": f"{p.first_name} {p.last_name}"} for e, p in rows]
        entry_by_id = {e.id: e for e, _ in rows}
        if key == "financial_reversals":
            reversals = [(e, p) for e, p in rows if e.kind == "reversal"]
            reversal_targets = [e.reverses_entry_id for e, _ in reversals if e.reverses_entry_id]
            targets = list((await db.scalars(select(PatientFinancialEntry).where(
                PatientFinancialEntry.clinic_id == clinic_id, PatientFinancialEntry.id.in_(reversal_targets),
            ))).all()) if reversal_targets else []
            target_by_id = {e.id: e for e in targets}
            records = [{"id": str(e.id), "entity": "patient_financial_entry", "date": _local_time(e.created_at, zone), "status": e.kind,
                        "amount": str(e.amount), "reverses_kind": target_by_id[e.reverses_entry_id].kind if e.reverses_entry_id in target_by_id else "unknown",
                        "patient_name": f"{p.first_name} {p.last_name}"} for e, p in reversals]
            grouped: dict[str, Decimal] = {}
            for reversal, _patient in reversals:
                target = target_by_id.get(reversal.reverses_entry_id)
                kind = target.kind if target else "unknown"
                grouped[kind] = grouped.get(kind, Decimal("0.00")) + reversal.amount
            value = {"count": len(reversals), "amount": str(sum((e.amount for e, _ in reversals), Decimal("0.00"))),
                     "by_reversed_kind": {kind: str(amount) for kind, amount in sorted(grouped.items())}}
        else:
            reversal_ids = [e.reverses_entry_id for e, _ in rows if e.kind == "reversal" and e.reverses_entry_id]
            target_rows = list((await db.scalars(select(PatientFinancialEntry).where(
                PatientFinancialEntry.clinic_id == clinic_id, PatientFinancialEntry.id.in_(reversal_ids),
            ))).all()) if reversal_ids else []
            entry_by_id.update({e.id: e for e in target_rows})
            net: dict[str, Decimal] = {}
            contributing_ids: set[str] = set()
            for entry, _patient in rows:
                if entry.kind == "reversal":
                    target = entry_by_id.get(entry.reverses_entry_id)
                    if target is None: continue
                    if target.kind == "payment": method, sign = target.method or "other", Decimal(-1)
                    elif target.kind == "refund": method, sign = target.method or "other", Decimal(1)
                    else: continue
                elif entry.kind == "payment": method, sign = entry.method or "other", Decimal(1)
                elif entry.kind == "refund": method, sign = entry.method or "other", Decimal(-1)
                else: continue
                net[method] = net.get(method, Decimal("0.00")) + entry.amount * sign
                contributing_ids.add(str(entry.id))
            enriched_records = []
            for record in records:
                if record["id"] not in contributing_ids:
                    continue
                entry = entry_by_id[UUID(record["id"])]
                if entry.kind == "reversal":
                    target = entry_by_id.get(entry.reverses_entry_id)
                    record = record | {
                        "method": (target.method or "other") if target else "other",
                        "reverses_kind": target.kind if target else "unknown",
                    }
                enriched_records.append(record)
            records = enriched_records
            value = {method: str(amount) for method, amount in sorted(net.items())}
        return records, value, None

    if key == "treatment_items_uncontinued":
        query = select(TreatmentPlanItem, TreatmentPlan, Patient, Appointment).join(
            TreatmentPlan, TreatmentPlan.id == TreatmentPlanItem.plan_id,
        ).join(Patient, Patient.id == TreatmentPlan.patient_id).outerjoin(
            Appointment, Appointment.id == TreatmentPlanItem.appointment_id,
        ).where(
            TreatmentPlanItem.clinic_id == clinic_id, TreatmentPlan.status.in_(("accepted", "in_progress")),
            TreatmentPlanItem.status == "active", TreatmentPlanItem.performed_procedure_id.is_(None),
            TreatmentPlanItem.created_at < upper,
        )
        rows = list((await db.execute(query)).all())
        accepted = set((await db.scalars(select(TreatmentPlanAcceptance.plan_id).join(
            TreatmentPlan, TreatmentPlan.id == TreatmentPlanAcceptance.plan_id,
        ).where(
            TreatmentPlanAcceptance.clinic_id == clinic_id,
            TreatmentPlanAcceptance.version == TreatmentPlan.accepted_version,
        ))).all())
        rows = [(item, plan, patient, appointment) for item, plan, patient, appointment in rows if plan.id in accepted
                and (doctor_id is None or plan.created_by_id == doctor_id)
                and (room_id is None or (appointment is not None and appointment.room_id == room_id))]
        cutoff = upper.astimezone(zone).date() - timedelta(days=1)
        records = [{"id": str(item.id), "entity": "treatment_plan_item", "date": _local_time(item.created_at, zone), "status": item.status,
                    "amount": None, "patient_name": f"{patient.first_name} {patient.last_name}",
                    "age_days": (cutoff - item.created_at.astimezone(zone).date()).days}
                   for item, _plan, patient, _appointment in rows]
        return records, {"count": len(records), "ages_days": [r["age_days"] for r in records]}, None

    return [], {"status": "unavailable", "reason": "Indisponibil până la T17."}, None


async def _report(
    indicator: str, start: date, end: date, doctor_id: UUID | None,
    room_id: UUID | None, db: AsyncSession, user: User,
) -> dict[str, Any]:
    if indicator not in INDICATORS:
        raise HTTPException(404, "Report indicator not found")
    clinic, zone, lower, upper = await _scope(db, user.clinic_id, start, end, doctor_id, room_id)
    _records_result, value, reason = await _records(indicator, db, user.clinic_id, lower, upper, zone, doctor_id, room_id)
    definition, unit, source = _metric_definition(indicator)
    return {"indicator": indicator, "value": value, "unit": unit, "definition": definition,
            "period": {"from": start.isoformat(), "to": end.isoformat(), "timezone": clinic.timezone,
                       "semantics": "local calendar days, inclusive"},
            "filters": {"doctor_id": str(doctor_id) if doctor_id else None, "room_id": str(room_id) if room_id else None},
            "source": source, "undefined_reason": reason,
            "records_url": f"/api/reports/{indicator}/records?from={start.isoformat()}&to={end.isoformat()}"
                + (f"&doctor_id={doctor_id}" if doctor_id else "")
                + (f"&room_id={room_id}" if room_id else "")}


@router.get("/{indicator}")
async def get_report(
    indicator: str, start: date = Query(alias="from"), end: date = Query(alias="to"),
    doctor_id: UUID | None = None, room_id: UUID | None = None,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*REPORT_ROLES)),
) -> dict[str, Any]:
    return await _report(indicator, start, end, doctor_id, room_id, db, user)


@router.get("/{indicator}/records")
async def get_report_records(
    indicator: str, start: date = Query(alias="from"), end: date = Query(alias="to"),
    doctor_id: UUID | None = None, room_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*REPORT_ROLES)),
) -> dict[str, Any]:
    if indicator not in INDICATORS:
        raise HTTPException(404, "Report indicator not found")
    clinic, zone, lower, upper = await _scope(db, user.clinic_id, start, end, doctor_id, room_id)
    records, _value, _reason = await _records(indicator, db, user.clinic_id, lower, upper, zone, doctor_id, room_id)
    return {"indicator": indicator, "period": {"from": start.isoformat(), "to": end.isoformat(),
            "timezone": clinic.timezone, "semantics": "local calendar days, inclusive"}, "total": len(records),
            "limit": limit, "offset": offset, "records": records[offset:offset + limit]}
