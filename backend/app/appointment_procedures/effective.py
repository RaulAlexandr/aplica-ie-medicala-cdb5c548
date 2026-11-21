from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointment_procedures.models import AppointmentProcedure
from app.database import ProcedureCatalog, TreatmentPlanItem


async def effective_procedures(db: AsyncSession, clinic_id: UUID, appointment_ids: list[UUID]) -> dict[UUID, list[dict]]:
    """Return manual and unperformed active plan procedures in two bounded queries."""
    if not appointment_ids:
        return {}
    manual_rows = (await db.execute(
        select(AppointmentProcedure.appointment_id, AppointmentProcedure.procedure_catalog_id,
               AppointmentProcedure.quantity, ProcedureCatalog.code, ProcedureCatalog.name)
        .join(ProcedureCatalog, ProcedureCatalog.id == AppointmentProcedure.procedure_catalog_id)
        .where(AppointmentProcedure.clinic_id == clinic_id, AppointmentProcedure.appointment_id.in_(appointment_ids),
               ProcedureCatalog.clinic_id == clinic_id, AppointmentProcedure.removed_at.is_(None))))
    manual: dict[UUID, dict[UUID, dict]] = defaultdict(dict)
    for appt_id, procedure_id, quantity, code, name in manual_rows:
        manual[appt_id][procedure_id] = {"quantity": quantity, "code": code, "name": name}
    plan_rows = (await db.execute(
        select(TreatmentPlanItem.appointment_id, TreatmentPlanItem.procedure_catalog_id,
               func.count(TreatmentPlanItem.id), func.min(TreatmentPlanItem.procedure_code),
               func.min(TreatmentPlanItem.procedure_name))
        .where(TreatmentPlanItem.clinic_id == clinic_id, TreatmentPlanItem.appointment_id.in_(appointment_ids),
               TreatmentPlanItem.status == "active", TreatmentPlanItem.performed_procedure_id.is_(None))
        .group_by(TreatmentPlanItem.appointment_id, TreatmentPlanItem.procedure_catalog_id)))
    plans: dict[UUID, dict[UUID, dict]] = defaultdict(dict)
    for appt_id, procedure_id, quantity, code, name in plan_rows:
        plans[appt_id][procedure_id] = {"quantity": int(quantity), "code": code, "name": name}
    result = {}
    for appt_id in appointment_ids:
        ids = set(manual[appt_id]) | set(plans[appt_id])
        lines = []
        for procedure_id in sorted(ids, key=str):
            m, p = manual[appt_id].get(procedure_id), plans[appt_id].get(procedure_id)
            lines.append({"procedure_catalog_id": procedure_id, "code": (m or p)["code"], "name": (m or p)["name"],
                          "quantity": max(m["quantity"] if m else 0, p["quantity"] if p else 0),
                          "manual_quantity": m["quantity"] if m else None,
                          "plan_quantity": p["quantity"] if p else None,
                          "source": "both" if m and p else "manual" if m else "plan"})
        result[appt_id] = lines
    return result
