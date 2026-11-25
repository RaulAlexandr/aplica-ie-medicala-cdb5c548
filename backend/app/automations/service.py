"""T25 polling evaluator. Event reads are batched by trigger and executions are unique per source."""

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.automations.models import AutomationExecution, AutomationRuleVersion
from app.collab.service import create_task
from app.database import (
    Appointment,
    AppointmentHistory,
    Patient,
    PerformedProcedure,
    Recall,
    Room,
    TreatmentPlan,
    TreatmentPlanEvent,
    TreatmentPlanItem,
    User,
    utcnow,
)
from app.inventory.models import InventoryItem, InventoryMovement

TRIGGERS = {
    "appointment_created", "appointment_cancelled", "appointment_no_show", "visit_closed",
    "procedure_completed", "recall_due", "stock_below_minimum", "plan_unbooked",
}
CONDITION_FIELDS = {"doctor_role", "appointment_type", "procedure_id", "days", "room_id"}
CONDITION_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}
FIELD_OPERATORS = {
    "doctor_role": {"eq", "ne"}, "appointment_type": {"eq", "ne"},
    "procedure_id": {"eq", "ne"}, "room_id": {"eq", "ne"},
    "days": {"eq", "ne", "gt", "gte", "lt", "lte"},
}
TARGET_ROLES = {"clinic_manager", "administrator", "doctor", "assistant", "reception"}
TEMPLATE_VARIABLES = {"event.type", "appointment.type", "patient.name", "procedure.name", "room.name", "days", "plan.title", "item.count", "stock.item"}
TEMPLATE_TOKEN = re.compile(r"\{\{([a-z.]+)\}\}")


def validate_definition(*, name: str, trigger: str, conditions: list[dict], action: dict, mode: str) -> None:
    if trigger not in TRIGGERS:
        raise ValueError("Declanșator necunoscut")
    if mode not in {"automatic", "approval"}:
        raise ValueError("Mod necunoscut")
    if not name.strip() or len(name) > 160:
        raise ValueError("Numele regulii este obligatoriu și are cel mult 160 de caractere")
    if not isinstance(conditions, list) or len(conditions) > 12:
        raise ValueError("Condițiile trebuie să fie o listă cu cel mult 12 reguli")
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {"field", "operator", "value"}:
            raise ValueError("Condiție invalidă")
        field, operator = condition["field"], condition["operator"]
        if not isinstance(field, str) or not isinstance(operator, str) or field not in CONDITION_FIELDS or operator not in FIELD_OPERATORS[field]:
            raise ValueError("Câmp sau operator nepermis")
        value = condition["value"]
        if field == "days":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3650:
                raise ValueError("Numărul de zile trebuie să fie între 0 și 3650")
        elif not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError("Valoare de condiție invalidă")
    if trigger == "plan_unbooked" and not any(item.get("field") == "days" and item.get("operator") in {"gt", "gte"} for item in conditions if isinstance(item, dict)):
        raise ValueError("Planurile neprogramate cer o condiție minimă pentru numărul de zile")
    if not isinstance(action, dict) or set(action) != {"target_type", "target_value", "title_template", "due_in_days"}:
        raise ValueError("Acțiunea trebuie să specifice responsabilul, titlul și termenul")
    target_type, target_value = action["target_type"], action["target_value"]
    if target_type == "role":
        if not isinstance(target_value, str) or target_value not in TARGET_ROLES:
            raise ValueError("Rolul responsabilului nu este permis")
    elif target_type == "user":
        try:
            UUID(target_value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Responsabilul trebuie să fie un utilizator valid") from exc
    else:
        raise ValueError("Tipul responsabilului nu este permis")
    title = action["title_template"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Șablonul titlului este obligatoriu și are cel mult 200 de caractere")
    tokens = TEMPLATE_TOKEN.findall(title)
    if "{{" in TEMPLATE_TOKEN.sub("", title) or any(token not in TEMPLATE_VARIABLES for token in tokens):
        raise ValueError("Șablonul conține variabile nepermise")
    due = action["due_in_days"]
    if isinstance(due, bool) or not isinstance(due, int) or not 0 <= due <= 365:
        raise ValueError("Termenul trebuie să fie între 0 și 365 de zile")


def condition_results(conditions: list[dict], data: dict[str, Any]) -> list[dict]:
    results = []
    for condition in conditions:
        left, right = data.get(condition["field"]), condition["value"]
        operator = condition["operator"]
        if operator == "eq":
            matched = str(left) == str(right)
        elif operator == "ne":
            matched = str(left) != str(right)
        elif operator == "gt":
            matched = left is not None and left > right
        elif operator == "gte":
            matched = left is not None and left >= right
        elif operator == "lt":
            matched = left is not None and left < right
        else:
            matched = left is not None and left <= right
        results.append({"field": condition["field"], "operator": operator, "expected": right, "actual": left, "matched": matched})
    return results


def _event(event_id: Any, trigger: str, occurred_at: datetime, data: dict) -> dict:
    payload = {key: (str(value) if isinstance(value, UUID) else value) for key, value in data.items()}
    payload.setdefault("event.type", trigger)
    return {"event_id": str(event_id), "event_type": trigger, "occurred_at": occurred_at, "data": payload}


async def fetch_events(db: AsyncSession, clinic_id: UUID, trigger: str, after: datetime, until: datetime | None = None) -> list[dict]:
    """Read source rows in one bounded query per trigger (never one query per event)."""
    now = until or utcnow()
    if trigger == "appointment_created":
        rows = (await db.execute(select(AppointmentHistory, Appointment, User.role, Room.name, Patient.first_name, Patient.last_name)
            .join(Appointment, Appointment.id == AppointmentHistory.appointment_id)
            .join(User, User.id == Appointment.doctor_id).join(Room, Room.id == Appointment.room_id).join(Patient, Patient.id == Appointment.patient_id)
            .where(AppointmentHistory.clinic_id == clinic_id, Appointment.clinic_id == clinic_id, User.clinic_id == clinic_id, Room.clinic_id == clinic_id, Patient.clinic_id == clinic_id,
                   AppointmentHistory.action == "created", AppointmentHistory.occurred_at > after, AppointmentHistory.occurred_at <= now)
            .order_by(AppointmentHistory.occurred_at, AppointmentHistory.id))).all()
        return [_event(h.id, trigger, h.occurred_at, {"appointment_id": a.id, "appointment.type": a.appointment_type, "appointment_type": a.appointment_type, "doctor_role": role, "room_id": a.room_id, "room.name": room, "patient.name": f"{first} {last}"}) for h, a, role, room, first, last in rows]
    if trigger in {"appointment_cancelled", "appointment_no_show", "visit_closed"}:
        target = {"appointment_cancelled": "cancelled", "appointment_no_show": "no_show", "visit_closed": "completed"}[trigger]
        rows = (await db.execute(select(AppointmentHistory, Appointment, User.role, Room.name, Patient.first_name, Patient.last_name)
            .join(Appointment, Appointment.id == AppointmentHistory.appointment_id)
            .join(User, User.id == Appointment.doctor_id).join(Room, Room.id == Appointment.room_id).join(Patient, Patient.id == Appointment.patient_id)
            .where(AppointmentHistory.clinic_id == clinic_id, Appointment.clinic_id == clinic_id, User.clinic_id == clinic_id, Room.clinic_id == clinic_id, Patient.clinic_id == clinic_id,
                   AppointmentHistory.action == "status_changed", AppointmentHistory.occurred_at > after,
                   AppointmentHistory.occurred_at <= now)
            .order_by(AppointmentHistory.appointment_id, AppointmentHistory.occurred_at, AppointmentHistory.id))).all()
        grouped: dict[UUID, list[tuple]] = {}
        for row in rows:
            grouped.setdefault(row[0].appointment_id, []).append(row)
        result = []
        for history_rows in grouped.values():
            for index, (history, appointment, role, room, first, last) in enumerate(history_rows):
                next_status = None
                if index + 1 < len(history_rows):
                    next_status = (history_rows[index + 1][0].details or {}).get("previous", {}).get("status")
                resulting_status = next_status or appointment.status
                if resulting_status == target:
                    result.append(_event(history.id, trigger, history.occurred_at, {"appointment_id": appointment.id,
                        "appointment.type": appointment.appointment_type, "appointment_type": appointment.appointment_type, "doctor_role": role,
                        "room_id": appointment.room_id, "room.name": room, "patient.name": f"{first} {last}"}))
        return sorted(result, key=lambda item: (item["occurred_at"], item["event_id"]))
    if trigger == "procedure_completed":
        rows = (await db.execute(select(PerformedProcedure, Appointment, User.role, Room.name, Patient.first_name, Patient.last_name)
            .join(Appointment, Appointment.id == PerformedProcedure.appointment_id).join(User, User.id == Appointment.doctor_id)
            .join(Room, Room.id == Appointment.room_id).join(Patient, Patient.id == PerformedProcedure.patient_id)
            .where(PerformedProcedure.clinic_id == clinic_id, Appointment.clinic_id == clinic_id, User.clinic_id == clinic_id, Room.clinic_id == clinic_id, Patient.clinic_id == clinic_id,
                   PerformedProcedure.status == "completed", PerformedProcedure.updated_at > after, PerformedProcedure.updated_at <= now)
            .order_by(PerformedProcedure.updated_at, PerformedProcedure.id))).all()
        return [_event(p.id, trigger, p.updated_at, {"appointment_id": a.id, "appointment.type": a.appointment_type, "appointment_type": a.appointment_type, "doctor_role": role, "room_id": a.room_id, "room.name": room, "patient.name": f"{first} {last}", "procedure_id": p.procedure_catalog_id, "procedure.name": p.procedure_name}) for p, a, role, room, first, last in rows]
    if trigger == "recall_due":
        rows = (await db.execute(select(Recall, Patient.first_name, Patient.last_name)
            .join(Patient, Patient.id == Recall.patient_id)
            .where(Recall.clinic_id == clinic_id, Patient.clinic_id == clinic_id, Recall.status == "pending", Recall.due_date <= now.date(), Recall.due_date > after.date())
            .order_by(Recall.due_date, Recall.id))).all()
        return [_event(r.id, trigger, datetime.combine(r.due_date, time.min, UTC), {"patient.name": f"{first} {last}", "days": (now.date() - r.due_date).days, "recall.reason": r.reason}) for r, first, last in rows]
    if trigger == "stock_below_minimum":
        incoming = InventoryMovement.kind.in_(["receipt", "transfer_in", "adjustment_in", "reversal_in"])
        outgoing = InventoryMovement.kind.in_(["consumption", "transfer_out", "adjustment_out", "reversal_out"])
        total = func.coalesce(func.sum(case((incoming, InventoryMovement.quantity), else_=0)), 0) - func.coalesce(func.sum(case((outgoing, InventoryMovement.quantity), else_=0)), 0)
        rows = (await db.execute(select(InventoryItem, total.label("quantity"), func.max(InventoryMovement.created_at).label("last_movement"))
            .outerjoin(InventoryMovement, (InventoryMovement.item_id == InventoryItem.id) & (InventoryMovement.clinic_id == clinic_id))
            .where(InventoryItem.clinic_id == clinic_id, InventoryItem.is_active.is_(True))
            .group_by(InventoryItem.id).having(total < InventoryItem.minimum_quantity)
            .order_by(InventoryItem.id))).all()
        result = []
        for item, quantity, moved in rows:
            occurred = moved or item.created_at
            if after < occurred <= now:
                result.append(_event(moved or f"item:{item.id}", trigger, occurred, {"stock.item": item.name, "days": 0, "quantity": str(quantity), "minimum_quantity": str(item.minimum_quantity)}))
        return result
    if trigger == "plan_unbooked":
        rows = (await db.execute(select(TreatmentPlanEvent, TreatmentPlan, func.count(TreatmentPlanItem.id))
            .join(TreatmentPlan, TreatmentPlan.id == TreatmentPlanEvent.plan_id)
            .join(TreatmentPlanItem, (TreatmentPlanItem.plan_id == TreatmentPlan.id) & (TreatmentPlanItem.clinic_id == clinic_id))
            .where(TreatmentPlanEvent.clinic_id == clinic_id, TreatmentPlan.clinic_id == clinic_id, TreatmentPlanEvent.action == "accepted", TreatmentPlanEvent.occurred_at > after,
                   TreatmentPlanEvent.occurred_at <= now, TreatmentPlanItem.status == "active", TreatmentPlanItem.appointment_id.is_(None))
            .group_by(TreatmentPlanEvent.id, TreatmentPlan.id).order_by(TreatmentPlanEvent.occurred_at, TreatmentPlanEvent.id))).all()
        return [_event(e.id, trigger, e.occurred_at, {"plan.title": plan.title, "item.count": int(count), "days": max(0, (now - e.occurred_at).days)}) for e, plan, count in rows]
    return []


def _render_title(template: str, data: dict) -> str:
    def replace(match):
        value = data.get(match.group(1))
        return "" if value is None else str(value)
    return TEMPLATE_TOKEN.sub(replace, template).strip()[:200]


async def resolve_assignee(db: AsyncSession, clinic_id: UUID, action: dict) -> User | None:
    query = select(User).where(User.clinic_id == clinic_id, User.is_active.is_(True))
    if action["target_type"] == "user":
        query = query.where(User.id == UUID(action["target_value"]))
    else:
        query = query.where(User.role == action["target_value"])
    return await db.scalar(query.order_by(User.id).limit(1))


async def create_task_action(db: AsyncSession, clinic_id: UUID, actor_id: UUID, action: dict, data: dict, assignee_id: UUID) -> dict:
    title = _render_title(action["title_template"], data)
    if not title:
        title = "Sarcină din automatizare"
    item = await create_task(db, clinic_id=clinic_id, actor_id=actor_id, assignee_id=assignee_id,
        title=title, due_at=utcnow() + timedelta(days=action["due_in_days"]), description="Sarcină creată de automatizare internă.")
    return {"task_id": str(item.id), "assignee_id": str(assignee_id), "title": title}


async def _perform(db: AsyncSession, execution: AutomationExecution, actor_id: UUID, assignee: User | None) -> None:
    execution.attempt_count += 1
    execution.status = "running"
    execution.reason = None
    try:
        if assignee is None:
            raise RuntimeError("Nu există o persoană activă pentru responsabilul ales")
        async with db.begin_nested():
            result = await create_task_action(db, execution.clinic_id, actor_id, execution.action_snapshot, execution.event_data, assignee.id)
        execution.result = result
        execution.status = "succeeded"
        execution.reason = None
    except Exception as exc:  # noqa: BLE001 - persist any action failure for bounded retry and audit
        execution.reason = str(exc)[:2000]
        execution.status = "failed" if execution.attempt_count >= 3 else "failed_retryable"
    execution.updated_at = utcnow()


async def evaluate(db: AsyncSession, clinic_id: UUID, actor_id: UUID) -> dict:
    versions = (await db.scalars(select(AutomationRuleVersion).where(AutomationRuleVersion.clinic_id == clinic_id, AutomationRuleVersion.is_active.is_(True))
                                 .order_by(AutomationRuleVersion.rule_id))).all()
    created = 0
    for version in versions:
        cursor = version.cursor_at or version.start_at or utcnow()
        assignee = await resolve_assignee(db, clinic_id, version.action)
        retryable = (await db.scalars(select(AutomationExecution).where(AutomationExecution.version_id == version.id, AutomationExecution.clinic_id == clinic_id,
                                              AutomationExecution.status == "failed_retryable").order_by(AutomationExecution.created_at).limit(500))).all()
        for run in retryable:
            await _perform(db, run, actor_id, assignee)
        # Plan delay conditions remain false until the configured age is reached. Revisit accepted
        # events since activation so the days condition can become true on a later evaluation.
        query_after = version.start_at or cursor if version.trigger == "plan_unbooked" else cursor
        events = await fetch_events(db, clinic_id, version.trigger, query_after)
        high_water = cursor
        for source in events:
            high_water = max(high_water, source["occurred_at"])
            checks = condition_results(version.conditions, source["data"])
            if not all(check["matched"] for check in checks):
                continue
            run = AutomationExecution(id=uuid4(), clinic_id=clinic_id, rule_id=version.rule_id, version_id=version.id,
                event_type=source["event_type"], event_id=source["event_id"], occurred_at=source["occurred_at"], event_data=source["data"],
                conditions_result=checks, action_snapshot=version.action, status="pending_approval" if version.mode == "approval" else "running",
                attempt_count=0, created_at=utcnow(), updated_at=utcnow())
            try:
                async with db.begin_nested():
                    db.add(run)
                    await db.flush()
            except IntegrityError:
                continue
            created += 1
            if version.mode == "automatic":
                await _perform(db, run, actor_id, assignee)
        version.cursor_at = high_water
    await db.commit()
    return {"created": created, "active_rules": len(versions)}


async def simulate(db: AsyncSession, clinic_id: UUID, trigger: str, conditions: list[dict], action: dict, days: int) -> dict:
    now = utcnow()
    sources = await fetch_events(db, clinic_id, trigger, now - timedelta(days=days), now)
    matches = []
    for source in sources:
        results = condition_results(conditions, source["data"])
        if all(item["matched"] for item in results):
            matches.append({"event_id": source["event_id"], "event_type": source["event_type"], "occurred_at": source["occurred_at"].isoformat(),
                           "title": _render_title(action["title_template"], source["data"]), "conditions": results})
    return {"matches": len(matches), "events": matches[:200], "days": days}
