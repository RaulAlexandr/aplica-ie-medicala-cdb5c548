from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.router import add_history
from app.appointments.service import appointment_end, clinic_timezone, local_bounds, local_to_utc
from app.auth.service import require_roles
from app.config import settings
from app.database import (
    Appointment,
    AuditEvent,
    Clinic,
    ImportRow,
    ImportRun,
    Patient,
    PatientRevision,
    ResourceUnavailability,
    Room,
    User,
    WorkingHours,
    get_db,
    utcnow,
)
from app.import_export.csv_tools import decode_csv

router = APIRouter(prefix="/import-export", tags=["import-export"])
MANAGERS = ("clinic_manager", "administrator")
PATIENT_FIELDS = {
    "first_name",
    "last_name",
    "date_of_birth",
    "phone",
    "email",
    "cnp",
    "external_id",
    "notes",
}
APPOINTMENT_FIELDS = {
    "patient_first_name",
    "patient_last_name",
    "patient_phone",
    "doctor",
    "room",
    "date",
    "time",
    "duration_minutes",
    "appointment_type",
    "notes",
}


def _mapping(raw: str | None, allowed: set[str]) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "Maparea coloanelor nu este JSON valid") from exc
    if not isinstance(value, dict) or any(
        key not in allowed or not isinstance(header, str) for key, header in value.items()
    ):
        raise HTTPException(422, "Maparea coloanelor conține câmpuri invalide")
    if len(set(value.values())) != len(value):
        raise HTTPException(422, "O coloană CSV poate fi asociată unui singur câmp")
    return value


async def _read(request: Request) -> tuple[str, bytes, str, list[dict[str, str]], str]:
    declared_size = request.headers.get("content-length")
    if declared_size and declared_size.isdigit() and int(declared_size) > settings.import_max_bytes:
        raise HTTPException(413, "Fișierul depășește limita de mărime configurată")
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > settings.import_max_bytes:
            raise HTTPException(413, "Fișierul depășește limita de mărime configurată")
    content = bytes(buffer)
    if not content:
        raise HTTPException(422, "Fișierul CSV este gol")
    try:
        delimiter, rows = decode_csv(content)
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Fișierul trebuie să fie codificat UTF-8") from exc
    if len(rows) > settings.import_max_rows:
        raise HTTPException(413, "Fișierul depășește limita de rânduri configurată")
    filename = unquote(request.headers.get("x-filename", "import.csv"))[:255]
    digest = hashlib.sha256(content).hexdigest()
    return delimiter, content, filename, rows, digest


def _value(row: dict[str, str], mapping: dict[str, str], field: str) -> str:
    return (row.get(mapping.get(field, ""), "") or "").strip()


def _date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("Dată invalidă")


def _patient_status(
    row: dict[str, str], mapping: dict[str, str]
) -> tuple[dict[str, Any], str | None]:
    data = {field: _value(row, mapping, field) or None for field in PATIENT_FIELDS}
    if not data["first_name"] or not data["last_name"]:
        return data, "Câmp obligatoriu lipsă: nume sau prenume"
    try:
        data["date_of_birth"] = _date(data["date_of_birth"] or "")
    except ValueError as exc:
        return data, str(exc)
    email = data["email"]
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return data, "Email invalid"
    if data["cnp"] and (not data["cnp"].isdigit() or len(data["cnp"]) != 13):
        return data, "CNP invalid"
    return data, None


def _patient_indexes(patients: list[Patient]) -> dict[str, dict[Any, Patient]]:
    indexes: dict[str, dict[Any, Patient]] = {"external": {}, "cnp": {}, "name_dob_phone": {}}
    for patient in patients:
        if patient.external_id:
            indexes["external"].setdefault(patient.external_id, patient)
        if patient.cnp:
            indexes["cnp"].setdefault(patient.cnp, patient)
        if patient.date_of_birth and patient.phone:
            key = (
                patient.first_name.casefold(),
                patient.last_name.casefold(),
                patient.date_of_birth,
                patient.phone.strip(),
            )
            indexes["name_dob_phone"].setdefault(key, patient)
    return indexes


def _patient_match(data: dict[str, Any], indexes: dict[str, dict[Any, Patient]]) -> Patient | None:
    if data.get("external_id"):
        return indexes["external"].get(data["external_id"])
    if data.get("cnp"):
        return indexes["cnp"].get(data["cnp"])
    if data.get("date_of_birth") and data.get("phone"):
        key = (
            data["first_name"].casefold(),
            data["last_name"].casefold(),
            data["date_of_birth"],
            data["phone"].strip(),
        )
        return indexes["name_dob_phone"].get(key)
    return None


async def _preview_patients(
    db: AsyncSession, clinic_id: UUID, rows: list[dict[str, str]], mapping: dict[str, str]
) -> list[dict[str, Any]]:
    patients = list((await db.scalars(select(Patient).where(Patient.clinic_id == clinic_id))).all())
    indexes = _patient_indexes(patients)
    result = []
    seen: set[tuple[Any, ...]] = set()
    for number, row in enumerate(rows, start=2):
        data, error = _patient_status(row, mapping)
        match = _patient_match(data, indexes) if not error else None
        identity = None
        if not error:
            if data["external_id"]:
                identity = ("external", data["external_id"])
            elif data["cnp"]:
                identity = ("cnp", data["cnp"])
            elif data["date_of_birth"] and data["phone"]:
                identity = (
                    "name_dob_phone",
                    data["first_name"].casefold(),
                    data["last_name"].casefold(),
                    data["date_of_birth"],
                    data["phone"],
                )
        duplicate_in_file = not match and identity is not None and identity in seen
        if identity is not None:
            seen.add(identity)
        diffs = (
            [
                field
                for field in data
                if field
                in {
                    "first_name",
                    "last_name",
                    "date_of_birth",
                    "phone",
                    "email",
                    "notes",
                    "cnp",
                    "external_id",
                }
                and data[field] not in (None, getattr(match, field, None))
            ]
            if match
            else []
        )
        status = (
            "error"
            if error
            else (
                "exists"
                if duplicate_in_file
                else "review"
                if match and diffs
                else "exists"
                if match
                else "new"
            )
        )
        message = error or (
            "Duplicat în același fișier"
            if duplicate_in_file
            else "De verificat: " + ", ".join(diffs)
            if diffs
            else None
        )
        result.append(
            {
                "row_number": number,
                "status": status,
                "message": message,
                "data": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in data.items()},
                "patient_id": str(match.id) if match else None,
            }
        )
    return result


@router.post("/patients/preview")
async def preview_patients(
    request: Request,
    columns: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    delimiter, _, filename, rows, digest = await _read(request)
    mapping = _mapping(columns, PATIENT_FIELDS)
    if not {"first_name", "last_name"}.issubset(mapping):
        raise HTTPException(422, "Mapează coloanele obligatorii nume și prenume")
    results = await _preview_patients(db, user.clinic_id, rows, mapping)
    return {
        "filename": filename,
        "sha256": digest,
        "delimiter": delimiter,
        "total": len(rows),
        "new": sum(r["status"] == "new" for r in results),
        "existing": sum(r["status"] in {"exists", "review"} for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "rows": results,
    }


async def _existing_run(
    db: AsyncSession, clinic_id: UUID, kind: str, digest: str
) -> ImportRun | None:
    return await db.scalar(
        select(ImportRun).where(
            ImportRun.clinic_id == clinic_id, ImportRun.kind == kind, ImportRun.file_hash == digest
        )
    )


@router.post("/patients/confirm")
async def confirm_patients(
    request: Request,
    columns: str | None = Query(None),
    update_rows: str = Query(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    _delimiter, _, filename, rows, digest = await _read(request)
    mapping = _mapping(columns, PATIENT_FIELDS)
    selected = {int(item) for item in update_rows.split(",") if item.strip().isdigit()}
    await db.execute(select(Clinic.id).where(Clinic.id == user.clinic_id).with_for_update())
    old = await _existing_run(db, user.clinic_id, "patients", digest)
    if old:
        return await _run_response(db, old)
    result = await _preview_patients(db, user.clinic_id, rows, mapping)
    update_ids = {
        UUID(item["patient_id"])
        for item in result
        if item["status"] == "review" and item["row_number"] in selected and item["patient_id"]
    }
    updated_patients = (
        (
            await db.scalars(
                select(Patient)
                .where(Patient.clinic_id == user.clinic_id, Patient.id.in_(update_ids))
                .with_for_update()
            )
        ).all()
        if update_ids
        else []
    )
    patients_by_id = {patient.id: patient for patient in updated_patients}
    run = ImportRun(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        kind="patients",
        file_hash=digest,
        filename=filename,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        duplicate = await _existing_run(db, user.clinic_id, "patients", digest)
        if duplicate:
            return await _run_response(db, duplicate)
        raise HTTPException(
            409, "Importul s-a suprapus cu o modificare concurentă; previzualizează din nou"
        )
    for item in result:
        status, data = item["status"], item["data"]
        if status in {"new", "review"} and data.get("date_of_birth"):
            data["date_of_birth"] = date.fromisoformat(data["date_of_birth"])
        entity_id = UUID(item["patient_id"]) if item["patient_id"] else None
        message = item["message"]
        if status == "new":
            patient = Patient(
                id=uuid4(),
                clinic_id=user.clinic_id,
                first_name=data["first_name"],
                last_name=data["last_name"],
                date_of_birth=data["date_of_birth"],
                phone=data["phone"],
                email=data["email"],
                notes=data["notes"],
                cnp=data["cnp"],
                external_id=data["external_id"],
            )
            db.add(patient)
            entity_id, status = patient.id, "created"
        elif status == "review" and item["row_number"] in selected:
            patient = patients_by_id.get(entity_id)
            if patient:
                for field in (
                    "first_name",
                    "last_name",
                    "date_of_birth",
                    "phone",
                    "email",
                    "notes",
                    "cnp",
                    "external_id",
                ):
                    new = data[field]
                    previous = getattr(patient, field)
                    if new is not None and new != previous:
                        db.add(
                            PatientRevision(
                                clinic_id=user.clinic_id,
                                patient_id=patient.id,
                                actor_id=user.id,
                                field=field,
                                previous_value=previous.isoformat()
                                if isinstance(previous, date)
                                else previous,
                                new_value=new.isoformat() if isinstance(new, date) else new,
                            )
                        )
                        setattr(patient, field, new)
                patient.updated_at = utcnow()
                status, message = "updated", "Revizie nouă înregistrată"
        db.add(
            ImportRow(
                clinic_id=user.clinic_id,
                run_id=run.id,
                row_number=item["row_number"],
                outcome=status,
                message=message,
                entity_type="patient" if entity_id else None,
                entity_id=entity_id,
                source={},
            )
        )
    db.add(
        AuditEvent(
            clinic_id=user.clinic_id,
            actor_id=user.id,
            entity_type="import_run",
            entity_id=run.id,
            action="created",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = await _existing_run(db, user.clinic_id, "patients", digest)
        if duplicate:
            return await _run_response(db, duplicate)
        raise HTTPException(
            409, "Importul s-a suprapus cu o modificare concurentă; previzualizează din nou"
        )
    return await _run_response(db, run)


def _run_payload(run: ImportRun, rows: list[ImportRow]) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "kind": run.kind,
        "filename": run.filename,
        "sha256": run.file_hash,
        "created_at": run.created_at.isoformat(),
        "created": sum(r.outcome == "created" for r in rows),
        "updated": sum(r.outcome == "updated" for r in rows),
        "rows": [
            {
                "row_number": r.row_number,
                "status": r.outcome,
                "message": r.message,
                "entity_type": r.entity_type,
                "entity_id": str(r.entity_id) if r.entity_id else None,
            }
            for r in rows
        ],
    }


async def _run_response(db: AsyncSession, run: ImportRun) -> dict[str, Any]:
    rows = (
        await db.scalars(
            select(ImportRow)
            .where(ImportRow.run_id == run.id, ImportRow.clinic_id == run.clinic_id)
            .order_by(ImportRow.row_number)
        )
    ).all()
    return _run_payload(run, list(rows))


@router.get("/runs")
async def list_runs(
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))
):
    runs = (
        await db.scalars(
            select(ImportRun)
            .where(ImportRun.clinic_id == user.clinic_id)
            .order_by(ImportRun.created_at.desc())
            .limit(100)
        )
    ).all()
    if not runs:
        return []
    rows = (
        await db.scalars(
            select(ImportRow)
            .where(
                ImportRow.clinic_id == user.clinic_id,
                ImportRow.run_id.in_([run.id for run in runs]),
            )
            .order_by(ImportRow.row_number)
        )
    ).all()
    by_run: dict[UUID, list[ImportRow]] = {}
    for row in rows:
        by_run.setdefault(row.run_id, []).append(row)
    return [_run_payload(run, by_run.get(run.id, [])) for run in runs]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))
):
    run = await db.scalar(
        select(ImportRun).where(ImportRun.id == run_id, ImportRun.clinic_id == user.clinic_id)
    )
    if not run:
        raise HTTPException(404, "Importul nu există")
    return await _run_response(db, run)


@router.get("/export/{kind}")
async def export_csv(
    kind: str,
    start: date,
    end: date,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    if kind not in {"patients", "appointments"} or end < start:
        raise HTTPException(422, "Tip sau perioadă invalidă")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    if kind == "patients":
        writer.writerow(
            [
                "Nume",
                "Prenume",
                "Data nașterii",
                "Telefon",
                "Email",
                "CNP",
                "Identificator extern",
                "Note",
            ]
        )
        clinic = await db.get(Clinic, user.clinic_id)
        zone = ZoneInfo(clinic.timezone)
        lower = local_bounds(start, clinic.timezone)[0]
        upper = local_bounds(end, clinic.timezone)[1]
        patients = (
            await db.scalars(
                select(Patient)
                .where(
                    Patient.clinic_id == user.clinic_id,
                    Patient.created_at >= lower,
                    Patient.created_at < upper,
                )
                .order_by(Patient.last_name, Patient.first_name)
            )
        ).all()
        for p in patients:
            writer.writerow(
                [
                    p.last_name,
                    p.first_name,
                    p.date_of_birth or "",
                    p.phone or "",
                    p.email or "",
                    p.cnp or "",
                    p.external_id or "",
                    p.notes or "",
                ]
            )
    else:
        writer.writerow(
            ["Pacient", "Medic", "Cabinet", "Început", "Durată minute", "Tip", "Status", "Note"]
        )
        clinic = await db.get(Clinic, user.clinic_id)
        zone = ZoneInfo(clinic.timezone)
        lower = datetime.combine(start, time.min, zone).astimezone(ZoneInfo("UTC"))
        upper = local_bounds(end, clinic.timezone)[1]
        appointments = await db.execute(
            select(Appointment, Patient, User, Room)
            .join(Patient, Patient.id == Appointment.patient_id)
            .join(User, User.id == Appointment.doctor_id)
            .join(Room, Room.id == Appointment.room_id)
            .where(
                Appointment.clinic_id == user.clinic_id,
                Appointment.starts_at >= lower,
                Appointment.starts_at < upper,
            )
            .order_by(Appointment.starts_at)
        )
        for appt, patient, doctor, room in appointments:
            writer.writerow(
                [
                    f"{patient.first_name} {patient.last_name}",
                    doctor.full_name,
                    room.name,
                    appt.starts_at.astimezone(zone).isoformat(),
                    appt.duration_minutes,
                    appt.appointment_type,
                    appt.status,
                    appt.notes or "",
                ]
            )
    export_id = uuid4()
    db.add(
        AuditEvent(
            clinic_id=user.clinic_id,
            actor_id=user.id,
            entity_type="csv_export",
            entity_id=export_id,
            action=f"exported_{kind}",
        )
    )
    await db.commit()
    body = "\ufeff" + output.getvalue()
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{start}-{end}.csv"'},
    )


@router.post("/appointments/preview")
async def preview_appointments(
    request: Request,
    columns: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    delimiter, _, filename, rows, digest = await _read(request)
    mapping = _mapping(columns, APPOINTMENT_FIELDS)
    required = {
        "patient_first_name",
        "patient_last_name",
        "doctor",
        "room",
        "date",
        "time",
        "duration_minutes",
        "appointment_type",
    }
    if not required.issubset(mapping):
        raise HTTPException(
            422, "Mapează pacientul, medicul, cabinetul, data, ora, durata și tipul"
        )
    result = await _preview_appointment_rows(db, user.clinic_id, rows, mapping)
    return {
        "filename": filename,
        "sha256": digest,
        "delimiter": delimiter,
        "total": len(rows),
        "new": sum(r["status"] == "new" for r in result),
        "existing": 0,
        "errors": sum(r["status"] == "error" for r in result),
        "rows": result,
    }


async def _preview_appointment_rows(
    db: AsyncSession, clinic_id: UUID, rows: list[dict[str, str]], mapping: dict[str, str]
) -> list[dict[str, Any]]:
    clinic, zone = await clinic_timezone(db, clinic_id)
    patients = list((await db.scalars(select(Patient).where(Patient.clinic_id == clinic_id))).all())
    users = list(
        (
            await db.scalars(
                select(User).where(
                    User.clinic_id == clinic_id,
                    User.is_active.is_(True),
                    User.role.in_(["doctor", "clinic_manager", "administrator"]),
                )
            )
        ).all()
    )
    rooms = list(
        (
            await db.scalars(
                select(Room).where(Room.clinic_id == clinic_id, Room.is_active.is_(True))
            )
        ).all()
    )
    existing = list(
        (await db.scalars(select(Appointment).where(Appointment.clinic_id == clinic_id))).all()
    )
    hours = list(
        (
            await db.scalars(
                select(WorkingHours).where(
                    WorkingHours.clinic_id == clinic_id, WorkingHours.is_active.is_(True)
                )
            )
        ).all()
    )
    unavailable = list(
        (
            await db.scalars(
                select(ResourceUnavailability).where(ResourceUnavailability.clinic_id == clinic_id)
            )
        ).all()
    )
    active_statuses = {"scheduled", "confirmed", "arrived", "in_progress", "completed"}
    planned: list[Appointment] = []
    result = []
    for number, row in enumerate(rows, 2):
        patient = next(
            (
                p
                for p in patients
                if p.first_name.casefold() == _value(row, mapping, "patient_first_name").casefold()
                and p.last_name.casefold() == _value(row, mapping, "patient_last_name").casefold()
                and (
                    not mapping.get("patient_phone")
                    or p.phone == _value(row, mapping, "patient_phone")
                )
            ),
            None,
        )
        doctor_name, room_name = (
            _value(row, mapping, "doctor").casefold(),
            _value(row, mapping, "room").casefold(),
        )
        doctor = next((p for p in users if p.full_name.casefold() == doctor_name), None)
        room = next((p for p in rooms if p.name.casefold() == room_name), None)
        try:
            day = _date(_value(row, mapping, "date"))
            start_time = datetime.strptime(_value(row, mapping, "time"), "%H:%M").time()
            duration = int(_value(row, mapping, "duration_minutes"))
            if not day or duration < 5 or duration > 1440:
                raise ValueError("Dată sau durată invalidă")
            start = local_to_utc(datetime.combine(day, start_time), clinic.timezone)
            errors = []
            if not patient:
                errors.append("Pacient necunoscut")
            if not doctor:
                errors.append("Medic necunoscut")
            if not room:
                errors.append("Cabinet necunoscut")
            if not _value(row, mapping, "appointment_type"):
                errors.append("Tip obligatoriu lipsă")
            if patient and doctor and room:
                end = appointment_end(start, duration)
                local_start, local_end = start.astimezone(zone), end.astimezone(zone)
                if local_start.date() != local_end.date():
                    errors.append("Programarea trebuie să se încadreze într-o zi locală")
                elif hours:
                    day_hours = [
                        item for item in hours if item.day_of_week == local_start.weekday()
                    ]
                    if not any(
                        item.start_time <= local_start.timetz().replace(tzinfo=None)
                        and item.end_time >= local_end.timetz().replace(tzinfo=None)
                        for item in day_hours
                    ):
                        errors.append("Ora este în afara programului clinicii")
                if any(
                    (
                        (item.resource_type == "doctor" and item.resource_id == doctor.id)
                        or (item.resource_type == "room" and item.resource_id == room.id)
                    )
                    and item.starts_at < end
                    and item.ends_at > start
                    for item in unavailable
                ):
                    errors.append("Resursă indisponibilă în intervalul ales")
                if any(
                    a.status in active_statuses
                    and a.starts_at < end
                    and (a.ends_at or appointment_end(a.starts_at, a.duration_minutes)) > start
                    and (a.doctor_id == doctor.id or a.room_id == room.id)
                    for a in [*existing, *planned]
                ):
                    errors.append("Conflict de programare")
                if not errors:
                    planned.append(
                        Appointment(
                            clinic_id=clinic_id,
                            patient_id=patient.id,
                            doctor_id=doctor.id,
                            room_id=room.id,
                            starts_at=start,
                            ends_at=end,
                            duration_minutes=duration,
                            status="scheduled",
                            appointment_type=_value(row, mapping, "appointment_type"),
                        )
                    )
            result.append(
                {
                    "row_number": number,
                    "status": "error" if errors else "new",
                    "message": "; ".join(errors) or None,
                    "data": {
                        "patient_id": str(patient.id) if patient else None,
                        "doctor_id": str(doctor.id) if doctor else None,
                        "room_id": str(room.id) if room else None,
                        "starts_at": start.isoformat(),
                        "duration_minutes": duration,
                        "appointment_type": _value(row, mapping, "appointment_type"),
                        "notes": _value(row, mapping, "notes") or None,
                    },
                }
            )
        except (ValueError, TypeError, HTTPException) as exc:
            message = (
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else "Dată, oră sau durată invalidă"
            )
            result.append({"row_number": number, "status": "error", "message": message, "data": {}})
    return result


@router.post("/appointments/confirm")
async def confirm_appointments(
    request: Request,
    columns: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
):
    _, _, filename, rows, digest = await _read(request)
    old = await _existing_run(db, user.clinic_id, "appointments", digest)
    if old:
        return await _run_response(db, old)
    mapping = _mapping(columns, APPOINTMENT_FIELDS)
    required = {
        "patient_first_name",
        "patient_last_name",
        "doctor",
        "room",
        "date",
        "time",
        "duration_minutes",
        "appointment_type",
    }
    if not required.issubset(mapping):
        raise HTTPException(
            422, "Mapează pacientul, medicul, cabinetul, data, ora, durata și tipul"
        )
    await db.execute(select(Clinic.id).where(Clinic.id == user.clinic_id).with_for_update())
    preview_rows = await _preview_appointment_rows(db, user.clinic_id, rows, mapping)
    run = ImportRun(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        kind="appointments",
        file_hash=digest,
        filename=filename,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        duplicate = await _existing_run(db, user.clinic_id, "appointments", digest)
        if duplicate:
            return await _run_response(db, duplicate)
        raise HTTPException(
            409, "Importul s-a suprapus cu o modificare concurentă; previzualizează din nou"
        )
    for item in preview_rows:
        entity_id = None
        status, message = item["status"], item["message"]
        if status == "new":
            data = item["data"]
            start = datetime.fromisoformat(data["starts_at"])
            appt = Appointment(
                id=uuid4(),
                clinic_id=user.clinic_id,
                patient_id=UUID(data["patient_id"]),
                doctor_id=UUID(data["doctor_id"]),
                room_id=UUID(data["room_id"]),
                starts_at=start,
                duration_minutes=data["duration_minutes"],
                status="scheduled",
                appointment_type=data["appointment_type"],
                notes=data["notes"],
                ends_at=appointment_end(start, data["duration_minutes"]),
            )
            db.add(appt)
            add_history(db, appt, user.id, "created")
            db.add(
                AuditEvent(
                    clinic_id=user.clinic_id,
                    actor_id=user.id,
                    entity_type="appointment",
                    entity_id=appt.id,
                    action="created",
                )
            )
            entity_id, status = appt.id, "created"
        db.add(
            ImportRow(
                clinic_id=user.clinic_id,
                run_id=run.id,
                row_number=item["row_number"],
                outcome=status,
                message=message,
                entity_type="appointment" if entity_id else None,
                entity_id=entity_id,
                source={},
            )
        )
    db.add(
        AuditEvent(
            clinic_id=user.clinic_id,
            actor_id=user.id,
            entity_type="import_run",
            entity_id=run.id,
            action="created",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        duplicate = await _existing_run(db, user.clinic_id, "appointments", digest)
        if duplicate:
            return await _run_response(db, duplicate)
        raise HTTPException(
            409, "Importul s-a suprapus cu o modificare concurentă; previzualizează din nou"
        )
    return await _run_response(db, run)
