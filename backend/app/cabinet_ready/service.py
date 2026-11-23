"""Server-side Cabinet Ready state. Reads never write; stock never confirms an item."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from app.database import (
    Appointment,
    Patient,
    PerformedProcedure,
    ProcedureCatalog,
    Room,
    TreatmentPlanItem,
    User,
    utcnow,
)
from app.equipment.models import Equipment, EquipmentDependency, EquipmentUnavailability
from app.equipment.router import effective_end, normalize_service_text
from app.materials.router import _procedure_demand, _requirements
from app.team.models import TeamLeaveRequest, TeamShift

ACTIVE_APPOINTMENTS = ("scheduled", "confirmed", "arrived", "in_progress")
STATE_LABELS = {"unknown": "Necunoscut", "in_preparation": "În pregătire", "blocked": "Blocat", "ready": "Pregătit"}
BLOCK_LABELS = {"material": "Lipsă de material", "equipment": "Echipament indisponibil", "leave": "Medic în concediu", "manual": "Blocaj manual"}
RequestRow = tuple[Appointment, Patient, User, Room]


def appointment_end(appointment: Appointment) -> datetime:
    return appointment.ends_at or appointment.starts_at + timedelta(minutes=appointment.duration_minutes)


def procedures_signature(procedures: list[dict]) -> str:
    return ",".join(sorted(f"{row['procedure_catalog_id']}:{row['quantity']}" for row in procedures))


async def latest_checklists(db: AsyncSession, clinic_id: UUID, procedure_ids: set[UUID]):
    if not procedure_ids:
        return {}, {}
    versions = (await db.scalars(select(CabinetChecklistVersion).where(
        CabinetChecklistVersion.clinic_id == clinic_id,
        CabinetChecklistVersion.procedure_catalog_id.in_(procedure_ids),
    ).order_by(CabinetChecklistVersion.version.desc()))).all()
    latest: dict[UUID, CabinetChecklistVersion] = {}
    for version in versions:
        latest.setdefault(version.procedure_catalog_id, version)
    items = (await db.scalars(select(CabinetChecklistItem).where(
        CabinetChecklistItem.clinic_id == clinic_id,
        CabinetChecklistItem.version_id.in_([row.id for row in latest.values()]),
    ).order_by(CabinetChecklistItem.position))).all() if latest else []
    by_version = defaultdict(list)
    for item in items:
        by_version[item.version_id].append(item)
    return latest, by_version


def planned_lines(procedures, latest, items_by_version, extras) -> list[dict]:
    """Checklist lines a new preparation would snapshot, once per procedure."""
    lines = []
    for procedure in procedures:
        version = latest.get(procedure["procedure_catalog_id"])
        if not version:
            continue
        for item in items_by_version.get(version.id, []):
            lines.append({"checklist_version_id": version.id, "particularity_id": None,
                          "procedure_catalog_id": procedure["procedure_catalog_id"], "procedure_name": procedure["name"],
                          "kind": item.kind, "label": item.label, "required": item.required, "prep_minutes": item.prep_minutes})
    for particularity in extras:
        if particularity.item_kind:
            lines.append({"checklist_version_id": None, "particularity_id": particularity.id, "procedure_catalog_id": None,
                          "procedure_name": None, "kind": particularity.item_kind, "label": particularity.item_label,
                          "required": bool(particularity.item_required), "prep_minutes": particularity.item_minutes or 0})
    return lines


def _covers(row_start: date, row_end: date, first: date, last: date) -> bool:
    return row_start <= last and row_end >= first


async def _material_shortages(db, user: User, rows: list[RequestRow], zone: ZoneInfo, now: datetime):
    """Reuse T18 FIFO: the window starts today so earlier demand consumes stock first."""
    days = [row[0].starts_at.astimezone(zone).date() for row in rows]
    last = max(days)
    first = min(min(days), now.astimezone(zone).date())
    first = max(first, last - timedelta(days=89))
    if min(days) < first:
        first = min(days)
        last = min(last, first + timedelta(days=89))
    requirement = await _requirements(db, user, first, last)
    shortages = defaultdict(list)
    for shortage in requirement["shortages"]:
        for entry in shortage["appointments"]:
            shortages[UUID(entry["appointment_id"])].append({
                "type": "material", "type_label": BLOCK_LABELS["material"],
                "text": f"Lipsesc {entry['shortage']} {shortage['unit']} din {shortage['item_name']}.",
                "check_with": "Stocuri și pagina Materiale",
            })
    return shortages


async def _equipment_signals(db, clinic_id: UUID, rows: list[RequestRow], demand: dict[UUID, list[dict]]):
    first = min(row[0].starts_at for row in rows)
    last = max(appointment_end(row[0]) for row in rows)
    outages = (await db.execute(select(EquipmentUnavailability, Equipment).join(
        Equipment, Equipment.id == EquipmentUnavailability.equipment_id,
    ).where(
        EquipmentUnavailability.clinic_id == clinic_id, Equipment.clinic_id == clinic_id,
        EquipmentUnavailability.starts_at < last, EquipmentUnavailability.ends_at > first,
    ).order_by(EquipmentUnavailability.starts_at, EquipmentUnavailability.id))).all()
    blockers, warnings = defaultdict(list), defaultdict(list)
    if not outages:
        return blockers, warnings
    equipment_ids = {equipment.id for _outage, equipment in outages}
    dependencies = (await db.scalars(select(EquipmentDependency).where(
        EquipmentDependency.clinic_id == clinic_id, EquipmentDependency.equipment_id.in_(equipment_ids),
    ))).all()
    rooms, services = defaultdict(set), defaultdict(set)
    for dependency in dependencies:
        if dependency.room_id:
            rooms[dependency.equipment_id].add(dependency.room_id)
        if dependency.procedure_id:
            services[dependency.equipment_id].add(dependency.procedure_id)
    service_ids = {value for values in services.values() for value in values}
    terms = defaultdict(set)
    linked = defaultdict(set)
    if service_ids:
        for procedure_id, name, code in (await db.execute(select(ProcedureCatalog.id, ProcedureCatalog.name, ProcedureCatalog.code).where(
            ProcedureCatalog.clinic_id == clinic_id, ProcedureCatalog.id.in_(service_ids)))).all():
            terms[procedure_id] = {normalize_service_text(value) for value in (name, code) if value and value.strip()}
        appointment_ids = [row[0].id for row in rows]
        for appointment_id, procedure_id in (await db.execute(select(PerformedProcedure.appointment_id, PerformedProcedure.procedure_catalog_id).where(
            PerformedProcedure.clinic_id == clinic_id, PerformedProcedure.appointment_id.in_(appointment_ids),
            PerformedProcedure.procedure_catalog_id.in_(service_ids), PerformedProcedure.status != "cancelled"))).all():
            linked[appointment_id].add(procedure_id)
        for appointment_id, procedure_id in (await db.execute(select(TreatmentPlanItem.appointment_id, TreatmentPlanItem.procedure_catalog_id).where(
            TreatmentPlanItem.clinic_id == clinic_id, TreatmentPlanItem.appointment_id.in_(appointment_ids),
            TreatmentPlanItem.procedure_catalog_id.in_(service_ids), TreatmentPlanItem.status == "active"))).all():
            linked[appointment_id].add(procedure_id)
        for appointment_id, procedures in demand.items():
            linked[appointment_id].update(row["procedure_catalog_id"] for row in procedures)
    responsible_ids = {equipment.responsible_id for _outage, equipment in outages}
    people = dict((await db.execute(select(User.id, User.full_name).where(User.clinic_id == clinic_id, User.id.in_(responsible_ids)))).all())
    for outage, equipment in outages:
        outage_end = effective_end(outage)
        if outage_end <= outage.starts_at:
            continue
        equipment_rooms = set(rooms[equipment.id])
        if equipment.room_id:
            equipment_rooms.add(equipment.room_id)
        equipment_services = services[equipment.id]
        equipment_terms = {term for service in equipment_services for term in terms[service]}
        if not equipment_rooms and not equipment_services:
            continue
        check_with = f"{people.get(equipment.responsible_id, 'Responsabil echipament')} (responsabil echipament)"
        for appointment, _patient, _doctor, room in rows:
            if appointment_end(appointment) <= outage.starts_at or appointment.starts_at >= outage_end:
                continue
            if room.id in equipment_rooms or linked[appointment.id] & equipment_services:
                blockers[appointment.id].append({"type": "equipment", "type_label": BLOCK_LABELS["equipment"],
                    "text": f"{equipment.name} nu este disponibil: {outage.reason.rstrip('.')}.", "check_with": check_with})
            elif any(term and term in normalize_service_text(appointment.appointment_type) for term in equipment_terms):
                warnings[appointment.id].append({"type": "equipment_possible",
                    "text": f"Posibil afectată de indisponibilitatea echipamentului {equipment.name}.", "check_with": check_with})
    return blockers, warnings


async def _team_signals(db, clinic_id: UUID, rows: list[RequestRow], zone: ZoneInfo):
    doctor_ids = {row[0].doctor_id for row in rows}
    first_at = min(row[0].starts_at for row in rows)
    last_at = max(appointment_end(row[0]) for row in rows)
    leaves = (await db.scalars(select(TeamLeaveRequest).where(
        TeamLeaveRequest.clinic_id == clinic_id, TeamLeaveRequest.status == "approved",
        TeamLeaveRequest.user_id.in_(doctor_ids),
        TeamLeaveRequest.starts_on <= (last_at + timedelta(days=1)).astimezone(zone).date(),
        TeamLeaveRequest.ends_on >= (first_at - timedelta(days=1)).astimezone(zone).date(),
    ))).all()
    shifts = (await db.execute(select(TeamShift.user_id, TeamShift.starts_at, TeamShift.ends_at).where(
        TeamShift.clinic_id == clinic_id, TeamShift.status == "active", TeamShift.user_id.in_(doctor_ids),
        TeamShift.starts_at < last_at, TeamShift.ends_at > first_at,
    ))).all()
    leaves_by_doctor, shifts_by_doctor = defaultdict(list), defaultdict(list)
    for leave in leaves:
        leaves_by_doctor[leave.user_id].append(leave)
    for user_id, starts_at, ends_at in shifts:
        shifts_by_doctor[user_id].append((starts_at, ends_at))
    blockers, warnings = defaultdict(list), defaultdict(list)
    for appointment, _patient, doctor, _room in rows:
        end = appointment_end(appointment)
        first_day = appointment.starts_at.astimezone(zone).date()
        last_day = (end - timedelta(microseconds=1)).astimezone(zone).date()
        for leave in leaves_by_doctor[appointment.doctor_id]:
            if _covers(leave.starts_on, leave.ends_on, first_day, last_day):
                blockers[appointment.id].append({"type": "leave", "type_label": BLOCK_LABELS["leave"],
                    "text": f"{doctor.full_name} are concediu aprobat în perioada {leave.starts_on.isoformat()} – {leave.ends_on.isoformat()}.",
                    "check_with": "Managerul clinicii"})
        if not any(start < end and stop > appointment.starts_at for start, stop in shifts_by_doctor[appointment.doctor_id]):
            warnings[appointment.id].append({"type": "no_shift", "text": f"{doctor.full_name} nu are tură în acest interval.",
                                              "check_with": "Managerul clinicii"})
    return blockers, warnings


def _change_explanation(preparation: CabinetPreparation, appointment: Appointment, signature: str) -> str | None:
    changes = []
    if preparation.room_id != appointment.room_id:
        changes.append("cabinetul")
    if preparation.starts_at != appointment.starts_at:
        changes.append("ora")
    if preparation.procedures_signature != signature:
        changes.append("procedurile")
    if not changes:
        return None
    return (f"S-a schimbat {', '.join(changes)} după începerea pregătirii. "
            "Confirmările anterioare rămân în istoric, dar nu mai contează. Reîncepe pregătirea.")


async def build_cards(db: AsyncSession, user: User, rows: list[RequestRow], zone: ZoneInfo, now: datetime | None = None) -> list[dict]:
    if not rows:
        return []
    now = now or utcnow()
    clinic_id = user.clinic_id
    appointment_ids = [row[0].id for row in rows]
    demand = await _procedure_demand(db, clinic_id, appointment_ids)
    procedure_ids = {entry["procedure_catalog_id"] for entries in demand.values() for entry in entries}
    latest, items_by_version = await latest_checklists(db, clinic_id, procedure_ids)

    preparations = (await db.scalars(select(CabinetPreparation).where(
        CabinetPreparation.clinic_id == clinic_id, CabinetPreparation.appointment_id.in_(appointment_ids),
    ).order_by(CabinetPreparation.revision))).all()
    current = {row.appointment_id: row for row in preparations}
    current_ids = [row.id for row in current.values()]
    prep_items = (await db.scalars(select(CabinetPreparationItem).where(
        CabinetPreparationItem.clinic_id == clinic_id, CabinetPreparationItem.preparation_id.in_(current_ids),
    ).order_by(CabinetPreparationItem.position))).all() if current_ids else []
    items_by_prep = defaultdict(list)
    for item in prep_items:
        items_by_prep[item.preparation_id].append(item)
    events = (await db.execute(select(CabinetConfirmationEvent, User.full_name).join(
        User, User.id == CabinetConfirmationEvent.actor_id,
    ).where(
        CabinetConfirmationEvent.clinic_id == clinic_id,
        CabinetConfirmationEvent.preparation_item_id.in_([item.id for item in prep_items]),
    ).order_by(CabinetConfirmationEvent.sequence))).all() if prep_items else []
    last_event = {event.preparation_item_id: (event, name) for event, name in events}

    particularities = (await db.scalars(select(CabinetParticularity).where(
        CabinetParticularity.clinic_id == clinic_id, CabinetParticularity.appointment_id.in_(appointment_ids),
    ).order_by(CabinetParticularity.proposed_at, CabinetParticularity.id))).all()
    decisions = {row.particularity_id: row for row in (await db.scalars(select(CabinetParticularityDecision).where(
        CabinetParticularityDecision.clinic_id == clinic_id,
        CabinetParticularityDecision.particularity_id.in_([row.id for row in particularities]),
    ))).all()} if particularities else {}
    particularities_by_appointment = defaultdict(list)
    for row in particularities:
        particularities_by_appointment[row.appointment_id].append(row)

    manual_rows = (await db.scalars(select(CabinetManualBlock).outerjoin(
        CabinetManualBlockClosure, CabinetManualBlockClosure.block_id == CabinetManualBlock.id,
    ).where(
        CabinetManualBlock.clinic_id == clinic_id, CabinetManualBlock.appointment_id.in_(appointment_ids),
        CabinetManualBlockClosure.id.is_(None),
    ).order_by(CabinetManualBlock.created_at, CabinetManualBlock.id))).all()
    manual = defaultdict(list)
    for block in manual_rows:
        manual[block.appointment_id].append({"id": str(block.id), "type": "manual", "type_label": BLOCK_LABELS["manual"],
                                             "text": block.reason, "check_with": block.check_with})
    material = await _material_shortages(db, user, rows, zone, now)
    equipment_blocks, equipment_warnings = await _equipment_signals(db, clinic_id, rows, demand)
    leave_blocks, shift_warnings = await _team_signals(db, clinic_id, rows, zone)

    cards = []
    for appointment, patient, doctor, room in rows:
        procedures = demand.get(appointment.id, [])
        signature = procedures_signature(procedures)
        preparation = current.get(appointment.id)
        explanation = _change_explanation(preparation, appointment, signature) if preparation else None
        active_preparation = preparation if preparation and not explanation else None
        approved = [row for row in particularities_by_appointment[appointment.id]
                    if row.id in decisions and decisions[row.id].decision == "approved"]
        without_checklist = [row["name"] for row in procedures if row["procedure_catalog_id"] not in latest]
        unknown_reasons = []
        if not procedures:
            unknown_reasons.append("Programarea nu are nicio procedură efectivă.")
        unknown_reasons.extend(f"Procedura {name} nu are checklist." for name in without_checklist)

        if active_preparation:
            lines = []
            for item in items_by_prep[active_preparation.id]:
                event_pair = last_event.get(item.id)
                confirmed = bool(event_pair and event_pair[0].action == "confirm")
                lines.append({"id": str(item.id), "kind": item.kind, "label": item.label, "required": item.required,
                              "prep_minutes": item.prep_minutes, "procedure_name": item.procedure_name,
                              "from_particularity": item.particularity_id is not None, "confirmed": confirmed,
                              "confirmed_by": event_pair[1] if confirmed else None,
                              "confirmed_at": event_pair[0].occurred_at.isoformat() if confirmed else None})
        else:
            lines = [{"id": None, "kind": line["kind"], "label": line["label"], "required": line["required"],
                      "prep_minutes": line["prep_minutes"], "procedure_name": line["procedure_name"],
                      "from_particularity": line["particularity_id"] is not None, "confirmed": False,
                      "confirmed_by": None, "confirmed_at": None}
                     for line in planned_lines(procedures, latest, items_by_version, approved)]
        required = [line for line in lines if line["required"]]
        confirmed_required = sum(1 for line in required if line["confirmed"])
        minutes = sum(line["prep_minutes"] for line in required)
        start_by = appointment.starts_at - timedelta(minutes=minutes)
        blockers = [*material.get(appointment.id, []), *equipment_blocks.get(appointment.id, []),
                    *leave_blocks.get(appointment.id, []), *manual.get(appointment.id, [])]
        warnings = [*equipment_warnings.get(appointment.id, []), *shift_warnings.get(appointment.id, [])]
        if blockers:
            state = "blocked"
        elif unknown_reasons:
            state = "unknown"
        elif not active_preparation or confirmed_required < len(required) or not required:
            state = "in_preparation"
        else:
            state = "ready"
        ready_at = None
        if state == "ready":
            ready_at = max(datetime.fromisoformat(line["confirmed_at"]) for line in required).isoformat()
        missing = [*(f"{row['type_label']}: {row['text'].rstrip('.')}. Verifică cu: {row['check_with']}." for row in blockers), *unknown_reasons]
        if explanation:
            missing.append(explanation)
        missing.extend(f"De confirmat: {line['label']}" for line in required if not line["confirmed"])
        cards.append({
            "appointment_id": str(appointment.id), "starts_at": appointment.starts_at.isoformat(),
            "ends_at": appointment_end(appointment).isoformat(),
            "local_time": appointment.starts_at.astimezone(zone).strftime("%H:%M"),
            "local_date": appointment.starts_at.astimezone(zone).date().isoformat(),
            "appointment_status": appointment.status, "room_id": str(room.id), "room_name": room.name,
            "doctor_id": str(doctor.id), "doctor_name": doctor.full_name,
            "patient_name": f"{patient.first_name} {patient.last_name}",
            "state": state, "state_label": STATE_LABELS[state], "confirmed_required": confirmed_required,
            "total_required": len(required), "counter": f"{confirmed_required} din {len(required)}",
            "blockers": blockers, "warnings": warnings, "missing": missing, "explanation": explanation,
            "unknown_reasons": unknown_reasons, "preparation_id": str(active_preparation.id) if active_preparation else None,
            "started": active_preparation is not None,
            "started_at": active_preparation.started_at.isoformat() if active_preparation else None,
            "ready_at": ready_at, "prep_minutes": minutes, "start_by": start_by.isoformat(),
            "start_by_local": start_by.astimezone(zone).strftime("%H:%M"),
            "late": active_preparation is None and bool(required) and now > start_by,
            "procedures": [{"procedure_catalog_id": str(row["procedure_catalog_id"]), "name": row["name"], "quantity": row["quantity"],
                            "has_checklist": row["procedure_catalog_id"] in latest} for row in procedures],
            "items": lines,
            "particularities": [{
                "id": str(row.id), "text": row.text, "status": decisions[row.id].decision if row.id in decisions else "pending",
                "item_label": row.item_label, "item_kind": row.item_kind, "item_required": row.item_required, "item_minutes": row.item_minutes,
            } for row in particularities_by_appointment[appointment.id]],
        })
    return cards


def reception_card(card: dict) -> dict:
    """Reception sees status, counter and block type only (F15 still open)."""
    keep = ("appointment_id", "starts_at", "ends_at", "local_time", "local_date", "appointment_status", "room_id", "room_name",
            "doctor_id", "doctor_name", "patient_name", "state", "state_label", "confirmed_required", "total_required", "counter",
            "started", "start_by", "start_by_local", "late")
    types = {row["type"]: row["type_label"] for row in card["blockers"]}
    return {**{key: card[key] for key in keep}, "blockers": [{"type": key, "type_label": label} for key, label in types.items()]}
