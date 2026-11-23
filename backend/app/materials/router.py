from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.service import local_bounds
from app.auth.service import require_roles
from app.database import (
    Appointment,
    Clinic,
    Patient,
    PerformedProcedure,
    ProcedureCatalog,
    TreatmentPlanItem,
    User,
    get_db,
    utcnow,
)
from app.inventory.models import InventoryItem, InventoryLocation, InventoryLot, InventoryMovement
from app.inventory.router import CONSUME, MovementIn, _clinic_today, _write_without_commit
from app.materials.models import (
    MaterialConsumptionBatch,
    MaterialConsumptionLine,
    MaterialRecipeLine,
    MaterialRecipeVersion,
    MaterialReservationEvent,
)

router = APIRouter(prefix="/materials", tags=["materials"])
READ_ROLES = ("clinic_manager", "administrator", "doctor", "assistant", "reception")
MANAGERS = ("clinic_manager", "administrator")
ACTUALS_ROLES = tuple(role for role in READ_ROLES if role != "reception")
ACTIVE_APPOINTMENTS = ("scheduled", "confirmed", "arrived", "in_progress")


class RecipeLineIn(BaseModel):
    item_id: UUID
    estimated_quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class RecipeSaveIn(BaseModel):
    expected_version: int = Field(ge=0)
    lines: list[RecipeLineIn] = Field(max_length=200)
    client_request_id: UUID | None = None


class ReservationIn(BaseModel):
    appointment_id: UUID
    item_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    client_request_id: UUID


class ReservationReleaseIn(BaseModel):
    client_request_id: UUID


class ConsumptionLineIn(BaseModel):
    item_id: UUID
    lot_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class ConsumptionConfirmIn(BaseModel):
    client_request_id: UUID
    reason: str | None = Field(default=None, max_length=500)
    lines: list[ConsumptionLineIn] = Field(min_length=1, max_length=100)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if value and value.strip() else None


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _signature(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _clinic_window(db: AsyncSession, clinic_id: UUID, from_date: date | None, to_date: date | None):
    clinic = await db.get(Clinic, clinic_id)
    if not clinic:
        raise HTTPException(404, "Clinică inexistentă")
    try:
        zone = ZoneInfo(clinic.timezone)
    except Exception as exc:
        raise HTTPException(500, "Fusul orar al clinicii nu este valid.") from exc
    today = utcnow().astimezone(zone).date()
    start_day = from_date or today
    end_day = to_date or start_day + timedelta(days=13)
    if end_day < start_day:
        raise HTTPException(422, "Data de sfârșit trebuie să fie după data de început.")
    if (end_day - start_day).days >= 90:
        raise HTTPException(422, "Intervalul poate avea cel mult 90 de zile.")
    starts_at, _ = local_bounds(start_day, clinic.timezone)
    _, ends_at = local_bounds(end_day, clinic.timezone)
    return clinic, zone, start_day, end_day, starts_at, ends_at


async def _latest_recipes(db: AsyncSession, clinic_id: UUID, procedure_ids: set[UUID]):
    if not procedure_ids:
        return {}, {}
    versions = (await db.scalars(select(MaterialRecipeVersion).where(
        MaterialRecipeVersion.clinic_id == clinic_id,
        MaterialRecipeVersion.procedure_catalog_id.in_(procedure_ids),
    ).order_by(MaterialRecipeVersion.version.desc()))).all()
    latest = {}
    for version in versions:
        latest.setdefault(version.procedure_catalog_id, version)
    lines = (await db.scalars(select(MaterialRecipeLine).where(
        MaterialRecipeLine.clinic_id == clinic_id,
        MaterialRecipeLine.recipe_version_id.in_([row.id for row in latest.values()]),
    ).order_by(MaterialRecipeLine.item_id))).all() if latest else []
    by_version = defaultdict(list)
    for line in lines:
        by_version[line.recipe_version_id].append(line)
    return latest, by_version


async def _stock_rows(db: AsyncSession, clinic_id: UUID):
    incoming = InventoryMovement.kind.in_(("receipt", "transfer_in", "adjustment_in", "reversal_in"))
    outgoing = InventoryMovement.kind.in_(("consumption", "transfer_out", "adjustment_out", "reversal_out"))
    quantity = func.coalesce(func.sum(case((incoming, InventoryMovement.quantity), else_=0)), 0) - func.coalesce(
        func.sum(case((outgoing, InventoryMovement.quantity), else_=0)), 0)
    rows = (await db.execute(select(
        InventoryMovement.item_id, InventoryLot.id, InventoryLot.expires_on, InventoryLocation.id, quantity,
    ).join(InventoryLot, InventoryLot.id == InventoryMovement.lot_id).join(
        InventoryLocation, InventoryLocation.id == InventoryMovement.location_id,
    ).where(
        InventoryMovement.clinic_id == clinic_id,
        InventoryLocation.clinic_id == clinic_id,
        InventoryLocation.is_active.is_(True),
    ).group_by(InventoryMovement.item_id, InventoryLot.id, InventoryLot.expires_on, InventoryLocation.id))).all()
    return [
        {"item_id": item_id, "lot_id": lot_id, "expires_on": expires_on, "location_id": location_id, "quantity": _decimal(qty)}
        for item_id, lot_id, expires_on, location_id, qty in rows if _decimal(qty) > 0
    ]


def _available_on(stock, item_id: UUID, on_date: date) -> Decimal:
    return sum((row["quantity"] for row in stock if row["item_id"] == item_id and
                (row["expires_on"] is None or row["expires_on"] >= on_date)), Decimal(0))


async def _current_reservation_events(db: AsyncSession, clinic_id: UUID, *, active_only: bool = True):
    query = select(MaterialReservationEvent, Appointment.status).join(
        Appointment, Appointment.id == MaterialReservationEvent.appointment_id,
    ).where(MaterialReservationEvent.clinic_id == clinic_id, Appointment.clinic_id == clinic_id)
    if active_only:
        query = query.where(Appointment.status.in_(ACTIVE_APPOINTMENTS))
    rows = (await db.execute(query.order_by(MaterialReservationEvent.occurred_at, MaterialReservationEvent.id))).all()
    latest = {}
    for event, status in rows:
        latest[(event.appointment_id, event.item_id)] = (event, status)
    return {key: value for key, value in latest.items() if value[0].action == "reserve"}


async def _latest_reservation(db: AsyncSession, clinic_id: UUID, appointment_id: UUID, item_id: UUID):
    return await db.scalar(select(MaterialReservationEvent).where(
        MaterialReservationEvent.clinic_id == clinic_id,
        MaterialReservationEvent.appointment_id == appointment_id,
        MaterialReservationEvent.item_id == item_id,
    ).order_by(MaterialReservationEvent.occurred_at.desc(), MaterialReservationEvent.id.desc()).limit(1))


def _reservation_response(event: MaterialReservationEvent, *, retry=False):
    return {"id": str(event.id), "appointment_id": str(event.appointment_id), "item_id": str(event.item_id),
            "action": event.action, "quantity": str(event.quantity) if event.quantity is not None else None,
            "actor_id": str(event.actor_id), "occurred_at": event.occurred_at.isoformat(), "retry": retry}


async def _requirements(db: AsyncSession, user: User, from_date: date | None, to_date: date | None):
    clinic, zone, start_day, end_day, start_utc, end_utc = await _clinic_window(db, user.clinic_id, from_date, to_date)
    appointment_pairs = (await db.execute(select(Appointment, Patient).join(
        Patient, Patient.id == Appointment.patient_id,
    ).where(
        Appointment.clinic_id == user.clinic_id,
        Appointment.status.in_(ACTIVE_APPOINTMENTS),
        Appointment.starts_at >= start_utc,
        Appointment.starts_at < end_utc,
    ).order_by(Appointment.starts_at, Appointment.id))).all()
    appointment_ids = {appointment.id for appointment, _patient in appointment_pairs}
    plan_items = (await db.scalars(select(TreatmentPlanItem).where(
        TreatmentPlanItem.clinic_id == user.clinic_id,
        TreatmentPlanItem.appointment_id.in_(appointment_ids) if appointment_ids else False,
        TreatmentPlanItem.status != "cancelled",
        TreatmentPlanItem.cancelled_at.is_(None),
    ).order_by(TreatmentPlanItem.appointment_id, TreatmentPlanItem.position, TreatmentPlanItem.id))).all()
    linked_appointment_ids = {row.appointment_id for row in plan_items}
    performed_ids = {row.performed_procedure_id for row in plan_items if row.performed_procedure_id}
    completed_ids = set((await db.scalars(select(PerformedProcedure.id).where(
        PerformedProcedure.clinic_id == user.clinic_id,
        PerformedProcedure.id.in_(performed_ids) if performed_ids else False,
        PerformedProcedure.status.in_(("completed", "cancelled")),
    ))).all())
    plan_items = [row for row in plan_items if row.performed_procedure_id not in completed_ids]
    procedure_ids = {row.procedure_catalog_id for row in plan_items}
    latest, lines_by_version = await _latest_recipes(db, user.clinic_id, procedure_ids)
    catalogs = (await db.scalars(select(ProcedureCatalog).where(
        ProcedureCatalog.clinic_id == user.clinic_id,
        ProcedureCatalog.id.in_(procedure_ids) if procedure_ids else False,
    ))).all()
    catalog_map = {row.id: row for row in catalogs}
    stock = await _stock_rows(db, user.clinic_id)
    reservations = await _current_reservation_events(db, user.clinic_id)
    reserve_by_item = defaultdict(Decimal)
    reserve_by_appt_item = defaultdict(Decimal)
    for (appointment_id, item_id), (event, _status) in reservations.items():
        quantity = _decimal(event.quantity)
        reserve_by_item[item_id] += quantity
        reserve_by_appt_item[(appointment_id, item_id)] += quantity
    item_ids = {line.item_id for rows in lines_by_version.values() for line in rows}
    item_rows = (await db.scalars(select(InventoryItem).where(
        InventoryItem.clinic_id == user.clinic_id,
        InventoryItem.id.in_(item_ids) if item_ids else False,
    ))).all()
    item_map = {row.id: row for row in item_rows}
    plan_by_appt = defaultdict(list)
    for row in plan_items:
        plan_by_appt[row.appointment_id].append(row)
    appointment_output = []
    unknown_appointments = []
    unknown_procedures = []
    shortage_by_item = defaultdict(lambda: {"quantity": Decimal(0), "appointments": []})
    requirements_by_item = defaultdict(Decimal)
    uncovered_by_item = defaultdict(Decimal)
    demand_by_item = defaultdict(list)
    for appointment, patient in appointment_pairs:
        local_date = appointment.starts_at.astimezone(zone).date()
        planned = plan_by_appt.get(appointment.id, [])
        row = {"id": str(appointment.id), "starts_at": appointment.starts_at.isoformat(), "local_date": local_date.isoformat(),
               "status": appointment.status, "patient_name": f"{patient.first_name} {patient.last_name}", "procedures": []}
        if appointment.id not in linked_appointment_ids:
            unknown_appointments.append({"id": str(appointment.id), "starts_at": appointment.starts_at.isoformat(),
                                         "local_date": local_date.isoformat(), "patient_name": row["patient_name"]})
        appointment_lines = defaultdict(list)
        for planned_item in planned:
            recipe = latest.get(planned_item.procedure_catalog_id)
            catalog = catalog_map.get(planned_item.procedure_catalog_id)
            performed_id = str(planned_item.performed_procedure_id) if planned_item.performed_procedure_id else None
            if not recipe:
                unknown_procedures.append({"appointment_id": str(appointment.id), "procedure_catalog_id": str(planned_item.procedure_catalog_id),
                                           "procedure_name": planned_item.procedure_name, "patient_name": row["patient_name"]})
                row["procedures"].append({"procedure_name": planned_item.procedure_name, "recipe_missing": True,
                                          "performed_procedure_id": performed_id, "lines": []})
                continue
            requirement_lines = []
            for recipe_line in lines_by_version[recipe.id]:
                item = item_map.get(recipe_line.item_id)
                if not item:
                    continue
                need = _decimal(recipe_line.estimated_quantity)
                own_reserved = reserve_by_appt_item[(appointment.id, item.id)]
                other_reserved = max(Decimal(0), reserve_by_item[item.id] - own_reserved)
                raw_available = _available_on(stock, item.id, local_date)
                available = max(Decimal(0), raw_available - other_reserved)
                requirements_by_item[item.id] += need
                line_output = {"item_id": str(item.id), "item_name": item.name, "unit": item.unit,
                    "recipe_line_id": str(recipe_line.id), "estimated_quantity": format(need, ".3f"),
                    "available": format(available, ".3f"), "reserved": format(own_reserved, ".3f"),
                    "shortage": "0.000"}
                appointment_lines[item.id].append((line_output, need, planned_item.procedure_name))
                requirement_lines.append(line_output)
            row["procedures"].append({"procedure_catalog_id": str(planned_item.procedure_catalog_id),
                "procedure_name": catalog.name if catalog else planned_item.procedure_name,
                "recipe_version": recipe.version, "recipe_version_id": str(recipe.id),
                "performed_procedure_id": performed_id, "recipe_missing": False, "lines": requirement_lines})
        for item_id, item_lines in appointment_lines.items():
            demand_by_item[item_id].append({
                "appointment_id": appointment.id, "local_date": local_date,
                "patient_name": row["patient_name"],
                "required": sum((line[1] for line in item_lines), Decimal(0)),
                "reserved": reserve_by_appt_item[(appointment.id, item_id)], "lines": item_lines,
            })
        appointment_output.append(row)

    reservation_appointment_ids = {appointment_id for appointment_id, _item_id in reservations}
    reservation_appointments = {}
    if reservation_appointment_ids:
        reservation_rows = (await db.execute(select(Appointment.id, Appointment.starts_at).where(
            Appointment.clinic_id == user.clinic_id,
            Appointment.id.in_(reservation_appointment_ids),
        ))).all()
        reservation_appointments = {appointment_id: starts_at for appointment_id, starts_at in reservation_rows}
    lots_by_item = defaultdict(list)
    lot_map = {}
    for stock_row in stock:
        key = (stock_row["item_id"], stock_row["lot_id"])
        lot = lot_map.setdefault(key, {"expires_on": stock_row["expires_on"], "quantity": Decimal(0), "lot_id": stock_row["lot_id"]})
        lot["quantity"] += stock_row["quantity"]
    for (item_id, _lot_id), lot in lot_map.items():
        lots_by_item[item_id].append(lot)
    for lots in lots_by_item.values():
        lots.sort(key=lambda lot: (lot["expires_on"] is None, lot["expires_on"] or date.max, str(lot["lot_id"])))

    def take_from_lots(lots, quantity, on_date):
        remaining = quantity
        for lot in lots:
            if lot["quantity"] <= 0 or (lot["expires_on"] is not None and lot["expires_on"] < on_date):
                continue
            taken = min(lot["quantity"], remaining)
            lot["quantity"] -= taken
            remaining -= taken
            if remaining <= 0:
                break
        return quantity - remaining

    for item_id, item_demands in demand_by_item.items():
        lots = lots_by_item.get(item_id, [])
        reservations_for_item = sorted(
            ((appointment_id, event) for (appointment_id, reserved_item), (event, _status) in reservations.items()
             if reserved_item == item_id),
            key=lambda pair: (reservation_appointments.get(pair[0], datetime.max.replace(tzinfo=ZoneInfo("UTC"))), str(pair[0])),
        )
        reserved_allocations = {}
        for appointment_id, event in reservations_for_item:
            appointment_at = reservation_appointments.get(appointment_id)
            if appointment_at is None:
                continue
            reserved_allocations[appointment_id] = take_from_lots(
                lots, _decimal(event.quantity), appointment_at.astimezone(zone).date())
        for demand in item_demands:
            on_date = demand["local_date"]
            reserved_coverage = min(demand["required"], reserved_allocations.get(demand["appointment_id"], Decimal(0)))
            available = sum((lot["quantity"] for lot in lots
                             if lot["quantity"] > 0 and (lot["expires_on"] is None or lot["expires_on"] >= on_date)), Decimal(0))
            need_after_reservation = demand["required"] - reserved_coverage
            uncovered_by_item[item_id] += need_after_reservation
            free_coverage = take_from_lots(lots, need_after_reservation, on_date)
            shortage = need_after_reservation - free_coverage
            remaining = shortage
            for line_output, need, _procedure_name in reversed(demand["lines"]):
                assigned = min(need, remaining)
                line_output["available"] = format(available, ".3f")
                line_output["reserved"] = format(demand["reserved"], ".3f")
                line_output["shortage"] = format(assigned, ".3f")
                remaining -= assigned
            if shortage > 0:
                shortage_by_item[item_id]["quantity"] += shortage
                shortage_by_item[item_id]["appointments"].append({
                    "appointment_id": str(demand["appointment_id"]), "local_date": on_date.isoformat(),
                    "patient_name": demand["patient_name"],
                    "procedure_name": ", ".join(dict.fromkeys(entry[2] for entry in demand["lines"])),
                    "shortage": format(shortage, ".3f"),
                })
    shortages = []
    for item_id, data in shortage_by_item.items():
        item = item_map[item_id]
        shortages.append({"item_id": str(item_id), "item_name": item.name, "unit": item.unit,
                          "quantity": format(data["quantity"], ".3f"), "appointments": data["appointments"]})
    performed_start, _ = local_bounds(start_day - timedelta(days=7), clinic.timezone)
    performed_rows = [] if user.role == "reception" else await _performed_materials(db, user, performed_start, end_utc)
    return {
        "from_date": start_day.isoformat(), "to_date": end_day.isoformat(), "timezone": clinic.timezone,
        "appointments": appointment_output,
        "unknown": {"appointments_without_procedure": {"count": len(unknown_appointments), "appointments": unknown_appointments},
                    "procedures_without_recipe": {"count": len(unknown_procedures), "procedures": unknown_procedures}},
        "shortages": shortages, "needed_by_item": {str(key): format(value, ".3f") for key, value in requirements_by_item.items()},
        "uncovered_by_item": {str(key): format(value, ".3f") for key, value in uncovered_by_item.items()},
        "performed_procedures": performed_rows,
    }


async def _performed_materials(db: AsyncSession, user: User, start_utc: datetime, end_utc: datetime):
    clinic = await db.get(Clinic, user.clinic_id)
    rows = (await db.execute(select(PerformedProcedure, Appointment, Patient, ProcedureCatalog).join(
        Appointment, Appointment.id == PerformedProcedure.appointment_id,
    ).join(Patient, Patient.id == Appointment.patient_id).join(
        ProcedureCatalog, ProcedureCatalog.id == PerformedProcedure.procedure_catalog_id,
    ).where(
        PerformedProcedure.clinic_id == user.clinic_id,
        Appointment.clinic_id == user.clinic_id,
        Appointment.status.in_((*ACTIVE_APPOINTMENTS, "completed")),
        Appointment.starts_at >= start_utc,
        Appointment.starts_at < end_utc,
        PerformedProcedure.status != "cancelled",
    ).order_by(Appointment.starts_at, PerformedProcedure.id))).all()
    procedure_ids = {procedure.procedure_catalog_id for procedure, _appointment, _patient, _catalog in rows}
    latest, lines_by_version = await _latest_recipes(db, user.clinic_id, procedure_ids)
    performed_ids = {procedure.id for procedure, _appointment, _patient, _catalog in rows}
    consumption_confirmed_ids = set((await db.scalars(select(MaterialConsumptionBatch.performed_procedure_id).where(
        MaterialConsumptionBatch.clinic_id == user.clinic_id,
        MaterialConsumptionBatch.performed_procedure_id.in_(performed_ids) if performed_ids else False,
    ))).all())
    item_ids = {line.item_id for recipe_lines in lines_by_version.values() for line in recipe_lines}
    items = (await db.scalars(select(InventoryItem).where(InventoryItem.clinic_id == user.clinic_id,
        InventoryItem.id.in_(item_ids) if item_ids else False))).all()
    item_map = {row.id: row for row in items}
    result = []
    for procedure, appointment, patient, catalog in rows:
        recipe = latest.get(procedure.procedure_catalog_id)
        result.append({"id": str(procedure.id), "appointment_id": str(appointment.id),
            "starts_at": appointment.starts_at.isoformat(), "local_date": appointment.starts_at.astimezone(ZoneInfo(clinic.timezone)).date().isoformat(),
            "procedure_name": catalog.name, "patient_name": f"{patient.first_name} {patient.last_name}",
            "status": procedure.status, "recipe_version": recipe.version if recipe else None,
            "consumption_confirmed": procedure.id in consumption_confirmed_ids,
            "lines": [{"item_id": str(line.item_id), "item_name": item_map[line.item_id].name,
                       "unit": item_map[line.item_id].unit, "recipe_line_id": str(line.id),
                       "estimated_quantity": format(_decimal(line.estimated_quantity), ".3f")}
                      for line in lines_by_version.get(recipe.id, [])] if recipe else []})
    return result


def _active_reservation_amount(reservations, item_id: UUID, excluding: UUID | None = None):
    return sum((_decimal(event.quantity) for (appointment_id, current_item), (event, _status) in reservations.items()
                if current_item == item_id and appointment_id != excluding), Decimal(0))


@router.get("/recipes/{procedure_id}")
async def get_recipe(procedure_id: UUID, user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    catalog = await db.scalar(select(ProcedureCatalog).where(
        ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id,
    ))
    if not catalog:
        raise HTTPException(404, "Procedura nu există.")
    versions = (await db.scalars(select(MaterialRecipeVersion).where(
        MaterialRecipeVersion.clinic_id == user.clinic_id,
        MaterialRecipeVersion.procedure_catalog_id == procedure_id,
    ).order_by(MaterialRecipeVersion.version))).all()
    if not versions:
        return {"procedure_catalog_id": str(procedure_id), "procedure_name": catalog.name, "version": 0,
                "versions": [], "lines": []}
    current = versions[-1]
    rows = (await db.execute(select(MaterialRecipeLine, InventoryItem).join(
        InventoryItem, InventoryItem.id == MaterialRecipeLine.item_id,
    ).where(MaterialRecipeLine.recipe_version_id == current.id).order_by(InventoryItem.name))).all()
    return {"procedure_catalog_id": str(procedure_id), "procedure_name": catalog.name, "version": current.version,
            "version_id": str(current.id), "versions": [row.version for row in versions],
            "created_at": current.created_at.isoformat(), "created_by_id": str(current.created_by_id),
            "lines": [{"id": str(line.id), "item_id": str(line.item_id), "item_name": item.name,
                       "unit": item.unit, "estimated_quantity": format(_decimal(line.estimated_quantity), ".3f")}
                      for line, item in rows]}


@router.put("/recipes/{procedure_id}", status_code=201)
async def save_recipe(procedure_id: UUID, payload: RecipeSaveIn, user: User = Depends(require_roles(*MANAGERS)),
                      db: AsyncSession = Depends(get_db)):
    if len({line.item_id for line in payload.lines}) != len(payload.lines):
        raise HTTPException(422, "Rețeta nu poate conține articolul de două ori.")
    catalog = await db.scalar(select(ProcedureCatalog).where(
        ProcedureCatalog.id == procedure_id, ProcedureCatalog.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    if not catalog:
        raise HTTPException(404, "Procedura nu există.")
    signature = _signature({"procedure_catalog_id": procedure_id, "expected_version": payload.expected_version,
        "lines": sorted(({"item_id": line.item_id, "estimated_quantity": format(line.estimated_quantity.normalize(), "f")}
                         for line in payload.lines), key=lambda line: str(line["item_id"]))})
    request_id = str(payload.client_request_id) if payload.client_request_id else None
    if request_id:
        prior = await db.scalar(select(MaterialRecipeVersion).where(
            MaterialRecipeVersion.clinic_id == user.clinic_id,
            MaterialRecipeVersion.client_request_id == request_id,
        ))
        if prior:
            if prior.request_signature != signature:
                raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
            return {"id": str(prior.id), "procedure_catalog_id": str(procedure_id), "version": prior.version,
                    "line_count": await db.scalar(select(func.count(MaterialRecipeLine.id)).where(
                        MaterialRecipeLine.recipe_version_id == prior.id)), "created_at": prior.created_at.isoformat()}
    current_version = await db.scalar(select(func.max(MaterialRecipeVersion.version)).where(
        MaterialRecipeVersion.clinic_id == user.clinic_id,
        MaterialRecipeVersion.procedure_catalog_id == procedure_id,
    )) or 0
    if current_version != payload.expected_version:
        raise HTTPException(409, "Rețeta a fost actualizată de alt utilizator. Reîncarcă și încearcă din nou.")
    item_ids = {line.item_id for line in payload.lines}
    items = (await db.scalars(select(InventoryItem).where(
        InventoryItem.clinic_id == user.clinic_id, InventoryItem.id.in_(item_ids), InventoryItem.is_active.is_(True),
    ))).all() if item_ids else []
    if len(items) != len(item_ids):
        raise HTTPException(404, "Articol inexistent sau inactiv.")
    recipe = MaterialRecipeVersion(clinic_id=user.clinic_id, procedure_catalog_id=procedure_id,
                                   version=current_version + 1, client_request_id=request_id,
                                   request_signature=signature if request_id else None, created_by_id=user.id)
    db.add(recipe)
    await db.flush()
    db.add_all([MaterialRecipeLine(clinic_id=user.clinic_id, recipe_version_id=recipe.id,
        item_id=line.item_id, estimated_quantity=line.estimated_quantity) for line in payload.lines])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if request_id:
            prior = await db.scalar(select(MaterialRecipeVersion).where(
                MaterialRecipeVersion.clinic_id == user.clinic_id,
                MaterialRecipeVersion.client_request_id == request_id,
            ))
            if prior and prior.request_signature == signature:
                return {"id": str(prior.id), "procedure_catalog_id": str(procedure_id), "version": prior.version,
                        "line_count": await db.scalar(select(func.count(MaterialRecipeLine.id)).where(
                            MaterialRecipeLine.recipe_version_id == prior.id)), "created_at": prior.created_at.isoformat()}
        raise HTTPException(409, "Rețeta s-a schimbat simultan. Reîncarcă și încearcă din nou.") from exc
    return {"id": str(recipe.id), "procedure_catalog_id": str(procedure_id), "version": recipe.version,
            "line_count": len(payload.lines), "created_at": recipe.created_at.isoformat()}


@router.get("/requirements")
async def requirements(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None),
                       user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    return await _requirements(db, user, from_date, to_date)


@router.get("/replenishment")
async def replenishment(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None),
                        user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    requirement = await _requirements(db, user, from_date, to_date)
    clinic_today = await _clinic_today(db, user.clinic_id)
    stock = await _stock_rows(db, user.clinic_id)
    reservations = await _current_reservation_events(db, user.clinic_id)
    reserved = defaultdict(Decimal)
    for (_appointment_id, item_id), (event, _status) in reservations.items():
        reserved[item_id] += _decimal(event.quantity)
    items = (await db.scalars(select(InventoryItem).where(
        InventoryItem.clinic_id == user.clinic_id, InventoryItem.is_active.is_(True),
    ).order_by(InventoryItem.name))).all()
    needed = {UUID(key): _decimal(value) for key, value in requirement["needed_by_item"].items()}
    uncovered = {UUID(key): _decimal(value) for key, value in requirement["uncovered_by_item"].items()}
    result = []
    for item in items:
        gross = _available_on(stock, item.id, clinic_today)
        net_available = max(Decimal(0), gross - reserved[item.id])
        amount_needed = needed.get(item.id, Decimal(0))
        amount_uncovered = uncovered.get(item.id, Decimal(0))
        suggestion = max(Decimal(0), _decimal(item.minimum_quantity) + amount_uncovered - net_available)
        result.append({"item_id": str(item.id), "item_name": item.name, "unit": item.unit,
            "available": format(net_available, ".3f"), "reserved": format(reserved[item.id], ".3f"),
            "needed": format(amount_needed, ".3f"), "uncovered_needed": format(amount_uncovered, ".3f"),
            "minimum_quantity": format(_decimal(item.minimum_quantity), ".3f"),
            "suggested_quantity": format(suggestion, ".3f")})
    return {"from_date": requirement["from_date"], "to_date": requirement["to_date"],
            "timezone": requirement["timezone"], "items": result}


@router.post("/reservations", status_code=201)
async def reserve(payload: ReservationIn, user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    if user.role not in CONSUME:
        raise HTTPException(403, "Rolul nu poate rezerva materiale.")
    signature = _signature({"appointment_id": payload.appointment_id, "item_id": payload.item_id,
                            "quantity": format(payload.quantity.normalize(), "f"), "action": "reserve"})
    request_id = str(payload.client_request_id)
    prior = await db.scalar(select(MaterialReservationEvent).where(
        MaterialReservationEvent.clinic_id == user.clinic_id,
        MaterialReservationEvent.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        response = _reservation_response(prior, retry=True)
        await db.rollback()
        return response
    appointment = await db.scalar(select(Appointment).where(
        Appointment.id == payload.appointment_id, Appointment.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    item = await db.scalar(select(InventoryItem).where(
        InventoryItem.id == payload.item_id, InventoryItem.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    if not appointment or not item or not item.is_active:
        raise HTTPException(404, "Programare sau articol inexistent.")
    if appointment.status not in ACTIVE_APPOINTMENTS:
        raise HTTPException(409, "Programarea nu mai este activă.")
    prior = await db.scalar(select(MaterialReservationEvent).where(
        MaterialReservationEvent.clinic_id == user.clinic_id, MaterialReservationEvent.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        response = _reservation_response(prior, retry=True)
        await db.rollback()
        return response
    all_current = await _current_reservation_events(db, user.clinic_id)
    other_reserved = _active_reservation_amount(all_current, item.id, excluding=appointment.id)
    _clinic, zone, *_ = await _clinic_window(db, user.clinic_id, None, None)
    appointment_day = appointment.starts_at.astimezone(zone).date()
    available = max(Decimal(0), _available_on(await _stock_rows(db, user.clinic_id), item.id, appointment_day) - other_reserved)
    if payload.quantity > available:
        shortage = payload.quantity - available
        raise HTTPException(409, {"message": "Stoc insuficient pentru rezervare.", "shortage": format(shortage, ".3f"),
                                  "available": format(available, ".3f")})
    event = MaterialReservationEvent(clinic_id=user.clinic_id, appointment_id=appointment.id, item_id=item.id,
        action="reserve", quantity=payload.quantity, client_request_id=request_id, signature=signature,
        actor_id=user.id, occurred_at=utcnow())
    db.add(event)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        prior = await db.scalar(select(MaterialReservationEvent).where(
            MaterialReservationEvent.clinic_id == user.clinic_id,
            MaterialReservationEvent.client_request_id == request_id,
        ))
        if prior and prior.signature == signature:
            return _reservation_response(prior, retry=True)
        raise HTTPException(409, "Rezervarea s-a schimbat simultan.") from exc
    return _reservation_response(event)


@router.post("/reservations/{appointment_id}/{item_id}/release", status_code=201)
async def release_reservation(appointment_id: UUID, item_id: UUID, payload: ReservationReleaseIn,
                              user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    if user.role not in CONSUME:
        raise HTTPException(403, "Rolul nu poate elibera rezervări.")
    signature = _signature({"appointment_id": appointment_id, "item_id": item_id, "action": "release"})
    request_id = str(payload.client_request_id)
    prior = await db.scalar(select(MaterialReservationEvent).where(
        MaterialReservationEvent.clinic_id == user.clinic_id,
        MaterialReservationEvent.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        response = _reservation_response(prior, retry=True)
        await db.rollback()
        return response
    appointment = await db.scalar(select(Appointment).where(
        Appointment.id == appointment_id, Appointment.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    item = await db.scalar(select(InventoryItem).where(
        InventoryItem.id == item_id, InventoryItem.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    if not appointment or not item:
        raise HTTPException(404, "Programare sau articol inexistent.")
    prior = await db.scalar(select(MaterialReservationEvent).where(
        MaterialReservationEvent.clinic_id == user.clinic_id,
        MaterialReservationEvent.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        response = _reservation_response(prior, retry=True)
        await db.rollback()
        return response
    latest = await _latest_reservation(db, user.clinic_id, appointment_id, item_id)
    if not latest or latest.action != "reserve":
        raise HTTPException(409, "Nu există o rezervare activă pentru acest articol.")
    event = MaterialReservationEvent(clinic_id=user.clinic_id, appointment_id=appointment_id, item_id=item_id,
        action="release", quantity=None, client_request_id=request_id, signature=signature,
        actor_id=user.id, occurred_at=utcnow())
    db.add(event)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        prior = await db.scalar(select(MaterialReservationEvent).where(
            MaterialReservationEvent.clinic_id == user.clinic_id,
            MaterialReservationEvent.client_request_id == request_id,
        ))
        if prior and prior.signature == signature:
            return _reservation_response(prior, retry=True)
        raise HTTPException(409, "Eliberarea rezervării s-a schimbat simultan.") from exc
    return _reservation_response(event)


@router.post("/performed-procedures/{performed_id}/confirm", status_code=201)
async def confirm_consumption(performed_id: UUID, payload: ConsumptionConfirmIn,
                              user: User = Depends(require_roles(*READ_ROLES)), db: AsyncSession = Depends(get_db)):
    if user.role not in CONSUME:
        raise HTTPException(403, "Rolul nu poate confirma consumul.")
    lines_payload = sorted(payload.lines, key=lambda line: (str(line.lot_id), str(line.item_id), str(line.location_id)))
    signature = _signature({"performed_procedure_id": performed_id,
        "reason": payload.reason, "lines": [{"item_id": line.item_id, "lot_id": line.lot_id,
            "location_id": line.location_id, "quantity": format(line.quantity.normalize(), "f")} for line in lines_payload]})
    request_id = str(payload.client_request_id)
    prior = await db.scalar(select(MaterialConsumptionBatch).where(
        MaterialConsumptionBatch.clinic_id == user.clinic_id,
        MaterialConsumptionBatch.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        prior_id = prior.id
        rows = (await db.scalars(select(MaterialConsumptionLine).where(MaterialConsumptionLine.batch_id == prior_id))).all()
        response = {"id": str(prior_id), "movement_ids": [str(row.inventory_movement_id) for row in rows], "retry": True}
        await db.rollback()
        return response
    procedure = await db.scalar(select(PerformedProcedure).where(
        PerformedProcedure.id == performed_id, PerformedProcedure.clinic_id == user.clinic_id,
    ).with_for_update(key_share=True))
    if not procedure:
        raise HTTPException(404, "Manoperă inexistentă.")
    appointment = await db.scalar(select(Appointment).where(
        Appointment.id == procedure.appointment_id, Appointment.clinic_id == user.clinic_id,
    ))
    if not appointment:
        raise HTTPException(404, "Manoperă inexistentă.")
    if procedure.status == "cancelled" or appointment.status in ("cancelled", "no_show"):
        raise HTTPException(409, "Nu se poate confirma consumul pentru o programare sau manoperă anulată.")
    prior = await db.scalar(select(MaterialConsumptionBatch).where(
        MaterialConsumptionBatch.clinic_id == user.clinic_id,
        MaterialConsumptionBatch.client_request_id == request_id,
    ))
    if prior:
        if prior.signature != signature:
            raise HTTPException(409, "client_request_id a fost folosit cu alt conținut.")
        prior_id = prior.id
        await db.rollback()
        rows = (await db.scalars(select(MaterialConsumptionLine).where(MaterialConsumptionLine.batch_id == prior_id))).all()
        return {"id": str(prior_id), "movement_ids": [str(row.inventory_movement_id) for row in rows], "retry": True}
    previous_batches = (await db.scalars(select(MaterialConsumptionBatch).where(
        MaterialConsumptionBatch.clinic_id == user.clinic_id,
        MaterialConsumptionBatch.performed_procedure_id == performed_id,
    ))).all()
    if previous_batches and not payload.reason:
        raise HTTPException(422, "A doua confirmare pentru aceeași manoperă necesită un motiv.")
    recipe = await db.scalar(select(MaterialRecipeVersion).where(
        MaterialRecipeVersion.clinic_id == user.clinic_id,
        MaterialRecipeVersion.procedure_catalog_id == procedure.procedure_catalog_id,
    ).order_by(MaterialRecipeVersion.version.desc()).limit(1))
    recipe_lines = (await db.scalars(select(MaterialRecipeLine).where(
        MaterialRecipeLine.recipe_version_id == recipe.id,
        MaterialRecipeLine.clinic_id == user.clinic_id,
    ))).all() if recipe else []
    estimate_by_item = {line.item_id: line for line in recipe_lines}
    lot_ids = sorted({line.lot_id for line in lines_payload}, key=str)
    locked_lots = (await db.scalars(select(InventoryLot).where(
        InventoryLot.clinic_id == user.clinic_id, InventoryLot.id.in_(lot_ids),
    ).order_by(InventoryLot.id).with_for_update(key_share=True))).all()
    if len(locked_lots) != len(lot_ids):
        raise HTTPException(404, "Lot inexistent.")
    lot_map = {lot.id: lot for lot in locked_lots}
    for line in lines_payload:
        if lot_map[line.lot_id].item_id != line.item_id:
            raise HTTPException(404, "Lotul nu aparține articolului selectat.")
    batch = MaterialConsumptionBatch(clinic_id=user.clinic_id, performed_procedure_id=performed_id,
        recipe_version_id=recipe.id if recipe else None, client_request_id=request_id, signature=signature,
        reason=payload.reason, actor_id=user.id, created_at=utcnow())
    db.add(batch)
    try:
        await db.flush()
        request_namespace = UUID(request_id)
        movement_ids = []
        for index, line in enumerate(lines_payload):
            movement_request = uuid5(request_namespace, f"movement:{index}")
            movement_input = MovementIn(item_id=line.item_id, lot_id=line.lot_id, location_id=line.location_id,
                quantity=line.quantity, client_request_id=movement_request, reason="Consum confirmat prin materiale")
            try:
                movement_response, movement_row = await _write_without_commit(movement_input, "consumption", user, db)
            except HTTPException as exc:
                if exc.status_code == 409 and exc.detail == "Stoc insuficient":
                    item_name = await db.scalar(select(InventoryItem.name).where(
                        InventoryItem.id == line.item_id, InventoryItem.clinic_id == user.clinic_id))
                    raise HTTPException(409,
                        f"Stoc insuficient pentru {item_name} (lot {lot_map[line.lot_id].code}).") from exc
                raise
            if not movement_row:
                movement_row = await db.get(InventoryMovement, UUID(movement_response["id"]))
            recipe_line = estimate_by_item.get(line.item_id)
            db.add(MaterialConsumptionLine(clinic_id=user.clinic_id, batch_id=batch.id,
                inventory_movement_id=movement_row.id, recipe_line_id=recipe_line.id if recipe_line else None,
                recipe_version_id=recipe.id if recipe else None, item_id=line.item_id, lot_id=line.lot_id,
                location_id=line.location_id, estimated_quantity=recipe_line.estimated_quantity if recipe_line else Decimal(0),
                actual_quantity=line.quantity))
            movement_ids.append(str(movement_row.id))
        # Consumed reservations become an auditable released state in the same transaction as movements.
        for item_id in sorted({line.item_id for line in lines_payload}, key=str):
            reservation = await _latest_reservation(db, user.clinic_id, appointment.id, item_id)
            if reservation and reservation.action == "reserve":
                release_id = uuid5(request_namespace, f"release:{item_id}")
                db.add(MaterialReservationEvent(clinic_id=user.clinic_id, appointment_id=appointment.id, item_id=item_id,
                    action="release", quantity=None, client_request_id=str(release_id),
                    signature=_signature({"appointment_id": appointment.id, "item_id": item_id, "action": "release"}),
                    actor_id=user.id, occurred_at=utcnow()))
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Confirmarea materialelor a intrat în conflict; reîncearcă aceeași cheie.") from exc
    return {"id": str(batch.id), "movement_ids": movement_ids, "retry": False}


@router.get("/actuals")
async def actuals(from_date: date | None = Query(default=None), to_date: date | None = Query(default=None),
                  user: User = Depends(require_roles(*ACTUALS_ROLES)), db: AsyncSession = Depends(get_db)):
    _clinic, _zone, start_day, end_day, start_utc, end_utc = await _clinic_window(db, user.clinic_id, from_date, to_date)
    batches = (await db.scalars(select(MaterialConsumptionBatch).join(
        PerformedProcedure, PerformedProcedure.id == MaterialConsumptionBatch.performed_procedure_id,
    ).join(Appointment, Appointment.id == PerformedProcedure.appointment_id).where(
        MaterialConsumptionBatch.clinic_id == user.clinic_id,
        Appointment.clinic_id == user.clinic_id,
        Appointment.starts_at >= start_utc, Appointment.starts_at < end_utc,
    ).order_by(MaterialConsumptionBatch.created_at))).all()
    if not batches:
        return {"from_date": start_day.isoformat(), "to_date": end_day.isoformat(), "lines": []}
    batch_ids = [batch.id for batch in batches]
    consumption_lines = (await db.scalars(select(MaterialConsumptionLine).where(
        MaterialConsumptionLine.clinic_id == user.clinic_id,
        MaterialConsumptionLine.batch_id.in_(batch_ids),
    ))).all()
    movement_ids = [line.inventory_movement_id for line in consumption_lines]
    reversals = (await db.execute(select(InventoryMovement.reverses_movement_id, func.sum(InventoryMovement.quantity)).where(
        InventoryMovement.clinic_id == user.clinic_id,
        InventoryMovement.reverses_movement_id.in_(movement_ids) if movement_ids else False,
        InventoryMovement.kind == "reversal_in",
    ).group_by(InventoryMovement.reverses_movement_id))).all()
    reversal_by_movement = {movement_id: _decimal(quantity) for movement_id, quantity in reversals}
    recipe_version_by_procedure = {}
    for batch in batches:
        if batch.recipe_version_id:
            recipe_version_by_procedure.setdefault(batch.performed_procedure_id, batch.recipe_version_id)
    recipe_versions = set(recipe_version_by_procedure.values())
    recipe_rows = (await db.scalars(select(MaterialRecipeLine).where(
        MaterialRecipeLine.clinic_id == user.clinic_id,
        MaterialRecipeLine.recipe_version_id.in_(recipe_versions) if recipe_versions else False,
    ))).all()
    recipe_lines_by_version = defaultdict(list)
    for recipe_line in recipe_rows:
        recipe_lines_by_version[recipe_line.recipe_version_id].append(recipe_line)
    batch_map = {batch.id: batch for batch in batches}
    catalog = (await db.execute(select(PerformedProcedure.id, ProcedureCatalog.name).join(
        ProcedureCatalog, ProcedureCatalog.id == PerformedProcedure.procedure_catalog_id,
    ).where(PerformedProcedure.id.in_({batch.performed_procedure_id for batch in batches})))).all()
    procedure_names = dict(catalog)
    aggregates = {}
    for procedure_id, recipe_version_id in recipe_version_by_procedure.items():
        for recipe_line in recipe_lines_by_version[recipe_version_id]:
            aggregates[(procedure_id, recipe_line.item_id)] = {
                "estimated": _decimal(recipe_line.estimated_quantity), "actual": Decimal(0),
                "recipe_version": recipe_version_id,
            }
    for line in consumption_lines:
        batch = batch_map[line.batch_id]
        key = (batch.performed_procedure_id, line.item_id)
        row = aggregates.setdefault(key, {"estimated": Decimal(0), "actual": Decimal(0),
            "recipe_version": recipe_version_by_procedure.get(batch.performed_procedure_id)})
        row["actual"] += max(Decimal(0), _decimal(line.actual_quantity) - reversal_by_movement.get(line.inventory_movement_id, Decimal(0)))
    item_ids = {item_id for _procedure_id, item_id in aggregates}
    items = (await db.scalars(select(InventoryItem).where(
        InventoryItem.clinic_id == user.clinic_id,
        InventoryItem.id.in_(item_ids) if item_ids else False,
    ))).all()
    item_map = {item.id: item for item in items}
    result = [{"performed_procedure_id": str(procedure_id), "procedure_name": procedure_names.get(procedure_id, "Manoperă"),
        "item_id": str(item_id), "item_name": item_map[item_id].name, "unit": item_map[item_id].unit,
        "recipe_version_id": str(data["recipe_version"]) if data["recipe_version"] else None,
        "estimated_quantity": format(data["estimated"], ".3f"), "actual_quantity": format(data["actual"], ".3f")}
        for (procedure_id, item_id), data in sorted(aggregates.items(), key=lambda entry: (str(entry[0][0]), str(entry[0][1])))]
    return {"from_date": start_day.isoformat(), "to_date": end_day.isoformat(), "lines": result}
