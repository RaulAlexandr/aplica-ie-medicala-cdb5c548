"""T05: Clinical Notes, Findings, and Patient Timeline."""

from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    AppointmentHistory,
    AuditEvent,
    ClinicalNote,
    ClinicalNoteRevision,
    Patient,
    PatientRevision,
    PatientTimelineEvent,
    PerformedProcedure,
    PerformedProcedureRevision,
    ProcedureExecutionSegment,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/clinical-notes", tags=["clinical_notes"])

# Roles
MANAGERS = ("clinic_manager", "administrator")
DOCTORS = ("clinic_manager", "doctor", "administrator")
STAFF_ROLES = ("clinic_manager", "doctor", "assistant", "reception", "administrator")

# Note types
class NoteType(str, Enum):
    NOTE = "note"
    FINDING = "finding"
    ALERT = "alert"
    DIAGNOSIS = "diagnosis"
    TREATMENT_PLAN = "treatment_plan"

# Note statuses
NOTE_STATUSES = {"draft", "finalized"}
NOTE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"finalized"},
    "finalized": set(),
}

# Timeline event types
TIMELINE_EVENT_TYPES = {
    "appointment",
    "performed_procedure",
    "clinical_note",
    "patient_revision",
    "alert",
}

# FDI tooth validation
FDI_PATTERN = r"^[1-4][1-8]$"
VALID_SURFACES = {
    "mesial", "distal", "buccal", "lingual", "palatal", "occlusal", "incisal",
    "vestibular", "labial", "approximal", "interproximal"
}


def validate_fdi_tooth(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    if not value:
        return None
    import re
    if not re.match(FDI_PATTERN, value):
        raise ValueError(f"Invalid FDI tooth code: {value}. Must be 11-18, 21-28, 31-38, or 41-48")
    return value


def validate_surface(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    surfaces = [s.strip().lower() for s in value.split(",")]
    for s in surfaces:
        if s not in VALID_SURFACES:
            raise ValueError(f"Invalid surface: {s}. Valid surfaces: {', '.join(sorted(VALID_SURFACES))}")
    return value


# Pydantic models
class ClinicalNoteCreate(BaseModel):
    patient_id: UUID
    appointment_id: UUID | None = None
    note_type: str = Field(default="note", min_length=1, max_length=30)
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=5000)
    tooth_fdi: str | None = Field(default=None, max_length=3)
    tooth_surface: str | None = Field(default=None, max_length=100)
    is_alert: bool = Field(default=False)

    @field_validator("tooth_fdi", mode="before")
    @classmethod
    def validate_tooth_fdi(cls, value: str | None) -> str | None:
        return validate_fdi_tooth(value)

    @field_validator("tooth_surface", mode="before")
    @classmethod
    def validate_tooth_surface(cls, value: str | None) -> str | None:
        return validate_surface(value)


class ClinicalNoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=5000)
    tooth_fdi: str | None = Field(default=None, max_length=3)
    tooth_surface: str | None = Field(default=None, max_length=100)
    is_alert: bool | None = None

    @field_validator("tooth_fdi", mode="before")
    @classmethod
    def validate_tooth_fdi(cls, value: str | None) -> str | None:
        return validate_fdi_tooth(value)

    @field_validator("tooth_surface", mode="before")
    @classmethod
    def validate_tooth_surface(cls, value: str | None) -> str | None:
        return validate_surface(value)


class ClinicalNoteFinalize(BaseModel):
    version_notes: str | None = Field(default=None, max_length=500)


class ClinicalNoteAddendum(BaseModel):
    addendum_content: str = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1, max_length=500)


class ClinicalNoteResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    patient_id: UUID
    appointment_id: UUID | None
    author_id: UUID
    author_name: str
    note_type: str
    title: str | None
    content: str
    tooth_fdi: str | None
    tooth_surface: str | None
    status: str
    is_alert: bool
    created_at: str
    updated_at: str
    finalized_at: str | None
    model_config = ConfigDict(from_attributes=True)


class ClinicalNoteRevisionResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    clinical_note_id: UUID
    actor_id: UUID
    actor_name: str
    field: str
    previous_value: str | None
    new_value: str | None
    reason: str | None
    occurred_at: str
    model_config = ConfigDict(from_attributes=True)


class TimelineEventResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    patient_id: UUID
    event_type: str
    entity_type: str | None
    entity_id: UUID | None
    title: str
    description: str | None
    actor_id: UUID | None
    actor_name: str | None
    occurred_at: str
    recorded_at: str
    source: str | None
    metadata: dict | None
    model_config = ConfigDict(from_attributes=True)


class PatientRecordResponse(BaseModel):
    patient: dict
    alerts: list[dict]
    clinical_notes: list[ClinicalNoteResponse]
    performed_procedures: list[dict]
    timeline: list[TimelineEventResponse]
    model_config = ConfigDict(from_attributes=True)


# Helper functions
def _note_response(note: ClinicalNote, author_name: str) -> ClinicalNoteResponse:
    return ClinicalNoteResponse.model_validate(
        {
            **note.__dict__,
            "author_name": author_name,
            "created_at": note.created_at.isoformat(),
            "updated_at": note.updated_at.isoformat(),
            "finalized_at": note.finalized_at.isoformat() if note.finalized_at else None,
        },
        from_attributes=True,
    )


def _sanitize_timeline_event(event: PatientTimelineEvent, user: User, db: AsyncSession = None) -> dict:
    """Sanitize timeline event to protect clinical data.

    Applies role-based access control:
    - Clinical notes: always sanitized (title, description, metadata)
    - Performed procedures: sanitized for non-clinical roles (assistant, reception)
    - Appointments: can be exposed as they contain less sensitive information

    For all events, only explicitly allowed fields are exposed in metadata.
    """
    result = {
        "id": event.id,
        "clinic_id": event.clinic_id,
        "patient_id": event.patient_id,
        "event_type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "actor_id": event.actor_id,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
        "source": event.source,
        "metadata": {},
    }

    # Define which roles can access clinical information
    # Clinical roles: doctors, managers, administrators
    # Non-clinical roles: assistant, reception
    CLINICAL_ROLES = {"clinic_manager", "doctor", "administrator"}
    is_clinical_role = user.role in CLINICAL_ROLES

    # Sanitize based on entity type
    if event.entity_type == "clinical_note":
        # Use controlled labels only; never turn note text or imported labels
        # into an action or title.
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        note_type = metadata.get("type")
        if not isinstance(note_type, str) or note_type not in {item.value for item in NoteType}:
            note_type = NoteType.NOTE.value

        if event.source == "clinical_note":
            action = "created"
        elif event.source == "clinical_note_finalized":
            action = "finalized"
        elif event.source == "clinical_note_revision":
            candidate_action = metadata.get("action")
            action = (
                candidate_action
                if isinstance(candidate_action, str) and candidate_action in {"corrected", "addendum"}
                else "activity"
            )
        else:
            action = "activity"

        labels = {
            "created": "Clinical note created",
            "finalized": "Clinical note finalized",
            "corrected": "Clinical note corrected",
            "addendum": "Clinical note addendum added",
            "activity": "Clinical note activity",
        }
        result["title"] = labels[action]
        result["description"] = labels[action]

        safe_metadata = {"type": note_type}
        if action == "created":
            # Note creation always starts as a draft. Do not expose the
            # mutable current status as if it were a historical snapshot.
            safe_metadata["status"] = "draft"
        elif action == "finalized":
            safe_metadata["status"] = "finalized"
        elif (
            action == "activity"
            and isinstance(metadata.get("status"), str)
            and metadata["status"] in NOTE_STATUSES
        ):
            safe_metadata["status"] = metadata["status"]
        if isinstance(metadata.get("is_alert"), bool):
            safe_metadata["is_alert"] = metadata["is_alert"]
        if action != "activity":
            safe_metadata["action"] = action
        result["metadata"] = safe_metadata
    elif event.entity_type == "performed_procedure":
        # Sanitize performed procedure events based on role
        if is_clinical_role:
            # Clinical roles can see procedure details
            result["title"] = event.title or ""
            result["description"] = event.description or ""
            # Filter metadata to only include safe fields
            if event.metadata:
                safe_metadata = {}
                # These fields are safe for clinical roles
                safe_fields = {"status", "code"}
                for field in safe_fields:
                    if field in event.metadata:
                        safe_metadata[field] = event.metadata[field]
                result["metadata"] = safe_metadata
        else:
            # Non-clinical roles (assistant, reception) get generic info only
            result["title"] = "procedure"
            result["description"] = "procedure"
            # Only expose status, not code or name
            if event.metadata and "status" in event.metadata:
                result["metadata"] = {"status": event.metadata["status"]}
    elif event.entity_type == "appointment":
        # Appointment events can be exposed with basic info
        result["title"] = event.title or ""
        result["description"] = event.description or ""
        # Filter metadata to only include safe fields
        if event.metadata:
            safe_metadata = {}
            # These fields are safe to expose
            safe_fields = {"status", "type"}
            for field in safe_fields:
                if field in event.metadata:
                    safe_metadata[field] = event.metadata[field]
            result["metadata"] = safe_metadata
    else:
        # For other event types, keep original fields but filter metadata
        result["title"] = event.title or ""
        result["description"] = event.description or ""
        # Filter metadata to only include explicitly allowed fields
        # By default, only expose simple non-sensitive fields
        if event.metadata:
            safe_metadata = {}
            # Only expose fields that are explicitly safe
            safe_fields = {"status", "type", "action"}
            for field in safe_fields:
                if field in event.metadata:
                    safe_metadata[field] = event.metadata[field]
            result["metadata"] = safe_metadata

    return result


def _timeline_event_response(event: PatientTimelineEvent, actor_name: str | None = None, user: User | None = None) -> TimelineEventResponse:
    event_dict = _sanitize_timeline_event(event, user) if user else {
        **event.__dict__,
        "occurred_at": event.occurred_at.isoformat(),
        "recorded_at": event.recorded_at.isoformat(),
    }
    event_dict["actor_name"] = actor_name
    return TimelineEventResponse.model_validate(event_dict, from_attributes=True)


def _can_access_draft(note: ClinicalNote, user: User) -> bool:
    """Check if user can access a draft note. Only the author can access drafts."""
    if note.status != "draft":
        return True
    return note.author_id == user.id


_TIMELINE_NAMESPACE = UUID("8b1ecfb9-0a51-4c22-84d7-81cab519c20e")
_GENERATED_TIMELINE_SOURCES = {
    "appointment", "appointment_history", "performed_procedure",
    "performed_procedure_revision", "procedure_execution_segment",
    "clinical_note", "clinical_note_finalized", "clinical_note_revision",
    "patient_revision",
}


def _projected_event(
    *, source: str, source_id: UUID, clinic_id: UUID, patient_id: UUID,
    event_type: str, entity_type: str, entity_id: UUID, title: str,
    occurred_at: datetime, recorded_at: datetime, actor_id: UUID | None,
    description: str | None = None, metadata: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid5(_TIMELINE_NAMESPACE, f"{source}:{source_id}"),
        clinic_id=clinic_id, patient_id=patient_id, event_type=event_type,
        entity_type=entity_type, entity_id=entity_id, title=title,
        description=description, actor_id=actor_id, occurred_at=occurred_at,
        recorded_at=recorded_at, source=source, metadata=metadata,
    )


def _timeline_timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _appointment_creation_time(
    created_history: AppointmentHistory, histories: list[AppointmentHistory],
) -> datetime:
    """Use persisted history for the creation time, with an explicit stable fallback.

    Legacy creation rows may have no start-time snapshot. Until a reschedule
    records ``previous.starts_at``, their creation-history timestamp is only a
    recording-time fallback, not a claim about the planned appointment date.
    Once that source history exists, its earliest previous start recovers the
    original planned date without consulting the mutable appointment row.
    """
    details = created_history.details or {}
    current = details.get("current") or {}
    starts_at = current.get("starts_at")
    if isinstance(starts_at, str):
        try:
            return datetime.fromisoformat(starts_at)
        except ValueError:
            pass

    previous_starts: list[tuple[float, str, datetime]] = []
    for history in histories:
        if history.action != "rescheduled":
            continue
        previous = (history.details or {}).get("previous") or {}
        starts_at = previous.get("starts_at")
        if not isinstance(starts_at, str):
            continue
        try:
            previous_starts.append((
                _timeline_timestamp(history.occurred_at),
                str(history.id),
                datetime.fromisoformat(starts_at),
            ))
        except ValueError:
            continue
    if previous_starts:
        return min(previous_starts, key=lambda item: (item[0], item[1]))[2]

    # The appointment's current start can change. Its creation-history timestamp
    # is a stable fallback, not a recovered planned start, when no snapshot exists.
    return created_history.occurred_at


async def _patient_timeline_events(
    db: AsyncSession, patient_id: UUID, user: User,
) -> list[SimpleNamespace | PatientTimelineEvent]:
    """Build a stable projection from persisted source histories and imports."""
    clinic_id = user.clinic_id
    events: list[SimpleNamespace | PatientTimelineEvent] = []
    legacy = await db.scalars(select(PatientTimelineEvent).where(
        PatientTimelineEvent.patient_id == patient_id,
        PatientTimelineEvent.clinic_id == clinic_id,
        or_(PatientTimelineEvent.source.is_(None),
            PatientTimelineEvent.source.notin_(_GENERATED_TIMELINE_SOURCES)),
    ))
    events.extend(legacy.all())

    appointments = (await db.scalars(select(Appointment).where(
        Appointment.patient_id == patient_id, Appointment.clinic_id == clinic_id,
    ))).all()
    appointment_ids = {row.id for row in appointments}
    histories = (await db.scalars(select(AppointmentHistory).where(
        AppointmentHistory.appointment_id.in_(appointment_ids),
        AppointmentHistory.clinic_id == clinic_id,
    ))).all() if appointment_ids else []
    by_appointment: dict[UUID, list[AppointmentHistory]] = {}
    for row in histories:
        by_appointment.setdefault(row.appointment_id, []).append(row)
    for appointment in appointments:
        for row in by_appointment.get(appointment.id, []):
            created = row.action == "created"
            snapshot = ((row.details or {}).get("current") or {}) if created else {}
            appointment_time = _appointment_creation_time(row, by_appointment.get(appointment.id, [])) if created else None
            appointment_type = snapshot.get("appointment_type", "appointment")
            action_title = {
                "status_changed": "Appointment status changed",
                "rescheduled": "Appointment rescheduled",
                "updated": "Appointment updated",
            }.get(row.action, "Appointment updated")
            events.append(_projected_event(
                source="appointment_history", source_id=row.id,
                clinic_id=clinic_id, patient_id=patient_id, event_type="appointment",
                entity_type="appointment", entity_id=appointment.id,
                title=f"Appointment: {appointment_type}" if created else action_title,
                occurred_at=appointment_time if created else row.occurred_at,
                recorded_at=row.occurred_at, actor_id=row.actor_id,
                metadata={"type": appointment_type} if created else {"action": row.action},
            ))

    procedures = (await db.scalars(select(PerformedProcedure).where(
        PerformedProcedure.patient_id == patient_id, PerformedProcedure.clinic_id == clinic_id,
    ))).all()
    procedure_ids = {row.id for row in procedures}
    proc_revisions = (await db.scalars(select(PerformedProcedureRevision).where(
        PerformedProcedureRevision.performed_procedure_id.in_(procedure_ids),
        PerformedProcedureRevision.clinic_id == clinic_id,
    ))).all() if procedure_ids else []
    by_procedure: dict[UUID, list[PerformedProcedureRevision]] = {}
    for row in proc_revisions:
        by_procedure.setdefault(row.performed_procedure_id, []).append(row)
    segments = (await db.scalars(select(ProcedureExecutionSegment).where(
        ProcedureExecutionSegment.performed_procedure_id.in_(procedure_ids),
        ProcedureExecutionSegment.segment_type == "run",
    ))).all() if procedure_ids else []
    for procedure in procedures:
        events.append(_projected_event(
            source="performed_procedure", source_id=procedure.id,
            clinic_id=clinic_id, patient_id=patient_id, event_type="performed_procedure",
            entity_type="performed_procedure", entity_id=procedure.id,
            title=f"Procedure: {procedure.procedure_name}",
            description=f"Recorded procedure ({procedure.procedure_code})",
            occurred_at=procedure.created_at, recorded_at=procedure.created_at,
            actor_id=procedure.executed_by_id,
            metadata={"status": "recorded", "code": procedure.procedure_code},
        ))
        for revision in by_procedure.get(procedure.id, []):
            events.append(_projected_event(
                source="performed_procedure_revision", source_id=revision.id,
                clinic_id=clinic_id, patient_id=patient_id, event_type="performed_procedure",
                entity_type="performed_procedure", entity_id=procedure.id,
                title=f"Procedure {revision.field} corrected",
                occurred_at=revision.occurred_at, recorded_at=revision.occurred_at,
                actor_id=revision.actor_id, metadata={"status": "updated", "action": "revision"},
            ))
    procedures_by_id = {row.id: row for row in procedures}
    for segment in segments:
        procedure = procedures_by_id[segment.performed_procedure_id]
        events.append(_projected_event(
            source="procedure_execution_segment", source_id=segment.id,
            clinic_id=clinic_id, patient_id=patient_id, event_type="performed_procedure",
            entity_type="performed_procedure", entity_id=procedure.id,
            title=f"Procedure execution: {procedure.procedure_name}",
            occurred_at=segment.started_at, recorded_at=segment.created_at,
            actor_id=procedure.executed_by_id,
            metadata={"status": "performed", "code": procedure.procedure_code},
        ))

    notes = (await db.scalars(select(ClinicalNote).where(
        ClinicalNote.patient_id == patient_id, ClinicalNote.clinic_id == clinic_id,
    ))).all()
    note_ids = {row.id for row in notes}
    note_revisions = (await db.scalars(select(ClinicalNoteRevision).where(
        ClinicalNoteRevision.clinical_note_id.in_(note_ids),
        ClinicalNoteRevision.clinic_id == clinic_id,
    ))).all() if note_ids else []
    by_note: dict[UUID, list[ClinicalNoteRevision]] = {}
    for row in note_revisions:
        by_note.setdefault(row.clinical_note_id, []).append(row)
    for note in notes:
        if not _can_access_draft(note, user):
            continue
        safe = {"type": note.note_type, "status": "draft", "is_alert": note.is_alert,
                "action": "created"}
        events.append(_projected_event(
            source="clinical_note", source_id=note.id,
            clinic_id=clinic_id, patient_id=patient_id, event_type="clinical_note",
            entity_type="clinical_note", entity_id=note.id,
            title=f"{note.note_type} note", description=f"{note.note_type} note",
            occurred_at=note.created_at, recorded_at=note.created_at,
            actor_id=note.author_id, metadata=safe,
        ))
        if note.finalized_at:
            events.append(_projected_event(
                source="clinical_note_finalized", source_id=note.id,
                clinic_id=clinic_id, patient_id=patient_id, event_type="clinical_note",
                entity_type="clinical_note", entity_id=note.id,
                title=f"{note.note_type} note finalized",
                description=f"{note.note_type} note finalized",
                occurred_at=note.finalized_at, recorded_at=note.finalized_at,
                actor_id=note.author_id,
                metadata={"type": note.note_type, "status": "finalized", "is_alert": note.is_alert,
                          "action": "finalized"},
            ))
        for revision in by_note.get(note.id, []):
            if (
                revision.field == "status"
                and revision.previous_value == "draft"
                and revision.new_value == "finalized"
            ):
                # The canonical finalized_at projection represents this transition.
                # Keep the detailed ClinicalNoteRevision persisted for audit/history.
                continue
            is_addendum = (
                revision.field == "content"
                and note.finalized_at is not None
                and _timeline_timestamp(revision.occurred_at) >= _timeline_timestamp(note.finalized_at)
            )
            action = "addendum" if is_addendum else "corrected"
            events.append(_projected_event(
                source="clinical_note_revision", source_id=revision.id,
                clinic_id=clinic_id, patient_id=patient_id, event_type="clinical_note",
                entity_type="clinical_note", entity_id=note.id,
                title=("Clinical note addendum added" if is_addendum else "Clinical note corrected"),
                description=("Clinical note addendum added" if is_addendum else "Clinical note corrected"),
                occurred_at=revision.occurred_at, recorded_at=revision.occurred_at,
                actor_id=revision.actor_id,
                metadata={"type": note.note_type, "action": action},
            ))

    # Patient revisions may contain protected clinical fields. Expose them only
    # to clinical roles; the general timeline endpoint also serves reception.
    if user.role in {"clinic_manager", "doctor", "administrator"}:
        patient_revisions = (await db.scalars(select(PatientRevision).where(
            PatientRevision.patient_id == patient_id, PatientRevision.clinic_id == clinic_id,
        ))).all()
        for row in patient_revisions:
            events.append(_projected_event(
                source="patient_revision", source_id=row.id,
                clinic_id=clinic_id, patient_id=patient_id, event_type="patient_revision",
                entity_type="patient_revision", entity_id=patient_id,
                title=f"Patient record {row.field} updated",
                occurred_at=row.occurred_at, recorded_at=row.occurred_at,
                actor_id=row.actor_id, metadata={"action": "revision"},
            ))

    note_by_id = {row.id: row for row in notes}
    return [
        event for event in events
        if event.entity_type != "clinical_note"
        or (
            event.entity_id in note_by_id
            and _can_access_draft(note_by_id[event.entity_id], user)
        )
    ]


async def _get_clinical_note(
    db: AsyncSession, note_id: UUID, user: User, for_update: bool = False
) -> ClinicalNote | None:
    query = select(ClinicalNote).where(
        ClinicalNote.id == note_id,
        ClinicalNote.clinic_id == user.clinic_id
    )
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def _validate_patient_access(
    db: AsyncSession, patient_id: UUID, user: User
) -> Patient | None:
    return await db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.clinic_id == user.clinic_id
        )
    )


async def _validate_appointment_access(
    db: AsyncSession, appointment_id: UUID, user: User, for_update: bool = False
) -> Appointment | None:
    query = select(Appointment).where(
        Appointment.id == appointment_id,
        Appointment.clinic_id == user.clinic_id
    )
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def _get_user_name(db: AsyncSession, user_id: UUID) -> str:
    user = await db.scalar(select(User).where(User.id == user_id))
    return user.full_name if user else "Unknown"


# Endpoints
@router.get("/patients/{patient_id}", response_model=list[ClinicalNoteResponse])
async def list_patient_notes(
    patient_id: UUID,
    note_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    is_alert: bool | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> list[ClinicalNoteResponse]:
    """List clinical notes for a patient."""
    patient = await _validate_patient_access(db, patient_id, user)
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")

    query = select(ClinicalNote).where(
        ClinicalNote.patient_id == patient_id,
        ClinicalNote.clinic_id == user.clinic_id
    ).order_by(desc(ClinicalNote.created_at))

    if note_type:
        query = query.where(ClinicalNote.note_type == note_type)
    if status and status in NOTE_STATUSES:
        query = query.where(ClinicalNote.status == status)
    if is_alert is not None:
        query = query.where(ClinicalNote.is_alert.is_(is_alert))

    # Apply draft filtering in SQL: only finalized notes OR drafts by this author
    # This ensures pagination works correctly with access control
    query = query.where(
        or_(
            ClinicalNote.status != "draft",
            (ClinicalNote.status == "draft") & (ClinicalNote.author_id == user.id)
        )
    )

    # Apply pagination at database level
    query = query.offset(offset).limit(limit)

    notes = (await db.scalars(query)).all()

    # Get author names
    results = []
    for note in notes:
        author_name = await _get_user_name(db, note.author_id)
        results.append(_note_response(note, author_name))

    return results


@router.get("/{note_id}", response_model=ClinicalNoteResponse)
async def get_clinical_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> ClinicalNoteResponse:
    """Get a specific clinical note."""
    note = await _get_clinical_note(db, note_id, user)
    if not note:
        raise HTTPException(404, "Clinical note not found or access denied")

    # Check draft access
    if not _can_access_draft(note, user):
        raise HTTPException(404, "Clinical note not found or access denied")

    author_name = await _get_user_name(db, note.author_id)
    return _note_response(note, author_name)


@router.post("", response_model=ClinicalNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_clinical_note(
    payload: ClinicalNoteCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> ClinicalNoteResponse:
    """Create a new clinical note or finding."""
    # Lock the appointment before validating its patient association. Appointment
    # reassignment uses the same row lock, so note creation cannot race it.
    if payload.appointment_id:
        appointment = await _validate_appointment_access(
            db, payload.appointment_id, user, for_update=True
        )
        if not appointment:
            raise HTTPException(422, "Appointment not found or access denied")
        if appointment.patient_id != payload.patient_id:
            raise HTTPException(422, "Appointment patient does not match note patient")

    # Validate patient after the appointment lock, matching the same lock order
    # used by appointment reassignment and performed-procedure creation.
    patient = await _validate_patient_access(db, payload.patient_id, user)
    if not patient:
        raise HTTPException(422, "Patient not found or access denied")

    # Create the note
    note = ClinicalNote(
        clinic_id=user.clinic_id,
        patient_id=payload.patient_id,
        appointment_id=payload.appointment_id,
        author_id=user.id,
        note_type=payload.note_type,
        title=payload.title,
        content=payload.content,
        tooth_fdi=payload.tooth_fdi,
        tooth_surface=payload.tooth_surface,
        status="draft",
        is_alert=payload.is_alert,
    )

    db.add(note)
    await db.flush()

    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="clinical_note",
        entity_id=note.id,
        action="created"
    ))

    await db.commit()
    await db.refresh(note)

    author_name = await _get_user_name(db, user.id)
    return _note_response(note, author_name)


@router.patch("/{note_id}", response_model=ClinicalNoteResponse)
async def update_clinical_note(
    note_id: UUID,
    payload: ClinicalNoteUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> ClinicalNoteResponse:
    """Update a draft clinical note."""
    note = await _get_clinical_note(db, note_id, user, for_update=True)
    if not note:
        raise HTTPException(404, "Clinical note not found or access denied")

    # Check draft access - only author can access drafts
    if not _can_access_draft(note, user):
        raise HTTPException(404, "Clinical note not found or access denied")

    # Cannot update finalized note
    if note.status == "finalized":
        raise HTTPException(409, "Cannot update a finalized note. Create an addendum instead.")

    # Only author can update drafts
    if note.author_id != user.id:
        raise HTTPException(403, "Only the author can update this draft note")

    changes = payload.model_dump(exclude_unset=True)
    old_values = {}
    for key, value in changes.items():
        old_values[key] = getattr(note, key)
        setattr(note, key, value)

    note.updated_at = utcnow()

    # Record changes in revisions
    for key, old_value in old_values.items():
        new_value = getattr(note, key)
        if old_value != new_value:
            db.add(ClinicalNoteRevision(
                clinic_id=user.clinic_id,
                clinical_note_id=note.id,
                actor_id=user.id,
                field=key,
                previous_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                reason="Note updated while in draft",
                occurred_at=utcnow()
            ))

    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="clinical_note",
        entity_id=note.id,
        action="updated"
    ))

    await db.commit()
    await db.refresh(note)

    author_name = await _get_user_name(db, note.author_id)
    return _note_response(note, author_name)


@router.post("/{note_id}/finalize", response_model=ClinicalNoteResponse)
async def finalize_clinical_note(
    note_id: UUID,
    payload: ClinicalNoteFinalize | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> ClinicalNoteResponse:
    """Finalize a clinical note (draft -> finalized)."""
    note = await _get_clinical_note(db, note_id, user, for_update=True)
    if not note:
        raise HTTPException(404, "Clinical note not found or access denied")

    # Check draft access - only author can access drafts
    if not _can_access_draft(note, user):
        raise HTTPException(404, "Clinical note not found or access denied")

    # Cannot finalize if already finalized
    if note.status == "finalized":
        raise HTTPException(409, "Note is already finalized")

    # Only author can finalize drafts
    if note.author_id != user.id:
        raise HTTPException(403, "Only the author can finalize this draft note")

    note.status = "finalized"
    note.finalized_at = utcnow()
    note.updated_at = utcnow()

    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="clinical_note",
        entity_id=note.id,
        action="finalized"
    ))

    if payload and payload.version_notes:
        db.add(ClinicalNoteRevision(
            clinic_id=user.clinic_id,
            clinical_note_id=note.id,
            actor_id=user.id,
            field="status",
            previous_value="draft",
            new_value="finalized",
            reason=payload.version_notes,
            occurred_at=utcnow()
        ))

    await db.commit()
    await db.refresh(note)

    author_name = await _get_user_name(db, note.author_id)
    return _note_response(note, author_name)


@router.post("/{note_id}/addendum", response_model=ClinicalNoteResponse)
async def add_clinical_note_addendum(
    note_id: UUID,
    payload: ClinicalNoteAddendum,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> ClinicalNoteResponse:
    """Add an addendum to a finalized clinical note."""
    note = await _get_clinical_note(db, note_id, user, for_update=True)
    if not note:
        raise HTTPException(404, "Clinical note not found or access denied")

    # Check draft access - only author can access drafts, no exception for managers
    if not _can_access_draft(note, user):
        raise HTTPException(404, "Clinical note not found or access denied")

    # Can only add addendum to finalized notes
    if note.status != "finalized":
        raise HTTPException(409, "Can only add addendum to finalized notes")

    # Only author or manager can add addendum
    if note.author_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the author or manager can add an addendum")

    # Append addendum to content
    old_content = note.content
    note.content = f"{old_content}\n\n---\nAddendum ({utcnow().isoformat()}): {payload.addendum_content}"
    note.updated_at = utcnow()

    db.add(ClinicalNoteRevision(
        clinic_id=user.clinic_id,
        clinical_note_id=note.id,
        actor_id=user.id,
        field="content",
        previous_value=old_content,
        new_value=note.content,
        reason=payload.reason,
        occurred_at=utcnow()
    ))

    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="clinical_note",
        entity_id=note.id,
        action="addendum_added"
    ))

    await db.commit()
    await db.refresh(note)

    author_name = await _get_user_name(db, note.author_id)
    return _note_response(note, author_name)


@router.get("/{note_id}/revisions", response_model=list[ClinicalNoteRevisionResponse])
async def get_note_revisions(
    note_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> list[ClinicalNoteRevisionResponse]:
    """Get revision history for a clinical note."""
    note = await _get_clinical_note(db, note_id, user)
    if not note:
        raise HTTPException(404, "Clinical note not found or access denied")

    # Check draft access
    if not _can_access_draft(note, user):
        raise HTTPException(404, "Clinical note not found or access denied")

    revisions = await db.scalars(
        select(ClinicalNoteRevision)
        .where(ClinicalNoteRevision.clinical_note_id == note_id)
        .order_by(ClinicalNoteRevision.occurred_at.desc())
    )

    results = []
    for rev in revisions:
        actor_name = await _get_user_name(db, rev.actor_id)
        results.append(ClinicalNoteRevisionResponse.model_validate(
            {
                **rev.__dict__,
                "actor_name": actor_name,
                "occurred_at": rev.occurred_at.isoformat(),
            },
            from_attributes=True,
        ))

    return results


# Timeline endpoints
@router.get("/patients/{patient_id}/timeline", response_model=list[TimelineEventResponse])
async def get_patient_timeline(
    patient_id: UUID,
    event_type: str | None = Query(default=None),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> list[TimelineEventResponse]:
    """Get timeline events for a patient."""
    patient = await _validate_patient_access(db, patient_id, user)
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")
    events = await _patient_timeline_events(db, patient_id, user)
    if event_type and event_type in TIMELINE_EVENT_TYPES:
        events = [event for event in events if event.event_type == event_type]
    if start_date:
        events = [event for event in events if _timeline_timestamp(event.occurred_at) >= _timeline_timestamp(start_date)]
    if end_date:
        events = [event for event in events if _timeline_timestamp(event.occurred_at) <= _timeline_timestamp(end_date)]

    def stable_order(event: SimpleNamespace | PatientTimelineEvent) -> tuple[float, float, str]:
        occurred = event.occurred_at
        recorded = event.recorded_at
        return (_timeline_timestamp(occurred), _timeline_timestamp(recorded), str(event.id))

    events.sort(key=stable_order, reverse=True)
    events = events[offset:offset + limit]

    results = []
    for event in events:
        actor_name = None
        if event.actor_id:
            actor_name = await _get_user_name(db, event.actor_id)
        results.append(_timeline_event_response(event, actor_name, user))

    return results


@router.get("/patients/{patient_id}/record", response_model=PatientRecordResponse)
async def get_patient_record(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> PatientRecordResponse:
    """Get complete patient record with notes, procedures, and timeline."""
    patient = await _validate_patient_access(db, patient_id, user)
    if not patient:
        raise HTTPException(404, "Patient not found or access denied")

    # Get patient data
    patient_data = {
        "id": str(patient.id),
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
        "sex": patient.sex,
        "phone": patient.phone,
        "email": patient.email,
        "allergies": patient.allergies,
        "medications": patient.medications,
        "chronic_diseases": patient.chronic_diseases,
        "pregnancy_status": patient.pregnancy_status,
        "smoking_status": patient.smoking_status,
        "medical_alerts": patient.medical_alerts,
        "relevant_medical_conditions": patient.relevant_medical_conditions,
    }

    # Get alerts (notes with is_alert=True) - only finalized
    alerts_query = select(ClinicalNote).where(
        ClinicalNote.patient_id == patient_id,
        ClinicalNote.clinic_id == user.clinic_id,
        ClinicalNote.is_alert.is_(True),
        ClinicalNote.status == "finalized"
    ).order_by(desc(ClinicalNote.created_at))

    alerts = (await db.scalars(alerts_query)).all()
    alert_list = []
    for alert in alerts:
        # Alerts are finalized, but still check access
        if _can_access_draft(alert, user):
            author_name = await _get_user_name(db, alert.author_id)
            alert_list.append(_note_response(alert, author_name).model_dump())

    # Get clinical notes
    notes_query = select(ClinicalNote).where(
        ClinicalNote.patient_id == patient_id,
        ClinicalNote.clinic_id == user.clinic_id
    ).order_by(desc(ClinicalNote.created_at))

    notes = (await db.scalars(notes_query)).all()
    notes_list = []
    for note in notes:
        if not _can_access_draft(note, user):
            continue
        author_name = await _get_user_name(db, note.author_id)
        notes_list.append(_note_response(note, author_name))

    # Get performed procedures
    procedures_query = select(PerformedProcedure).where(
        PerformedProcedure.patient_id == patient_id,
        PerformedProcedure.clinic_id == user.clinic_id
    ).order_by(desc(PerformedProcedure.created_at))

    procedures = (await db.scalars(procedures_query)).all()
    procedures_list = []
    for proc in procedures:
        procedures_list.append({
            "id": str(proc.id),
            "appointment_id": str(proc.appointment_id),
            "procedure_name": proc.procedure_name,
            "procedure_code": proc.procedure_code,
            "status": proc.status,
            "tooth_fdi": proc.tooth_fdi,
            "tooth_surface": proc.tooth_surface,
            "created_at": proc.created_at.isoformat(),
            "updated_at": proc.updated_at.isoformat(),
        })

    timeline = await _patient_timeline_events(db, patient_id, user)
    timeline.sort(
        key=lambda event: (
            _timeline_timestamp(event.occurred_at),
            _timeline_timestamp(event.recorded_at),
            str(event.id),
        ),
        reverse=True,
    )
    timeline_list = []
    for event in timeline:
        actor_name = None
        if event.actor_id:
            actor_name = await _get_user_name(db, event.actor_id)
        timeline_list.append(_timeline_event_response(event, actor_name, user))

    return PatientRecordResponse(
        patient=patient_data,
        alerts=alert_list,
        clinical_notes=notes_list,
        performed_procedures=procedures_list,
        timeline=timeline_list
    )


@router.post("/timeline/refresh", status_code=status.HTTP_200_OK)
async def refresh_timeline(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> dict:
    """Trigger timeline refresh for all patients in clinic.

    This endpoint regenerates timeline events from existing data.
    Restricted to managers/administrators only.
    Useful after bulk imports or when timeline gets out of sync.
    """
    # This remains manager-only for compatibility. Reads project source histories
    # directly, so synchronization has no destructive rebuild or new identities.
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="timeline",
        entity_id=user.clinic_id,
        action="refreshed",
    ))
    await db.commit()
    return {"status": "ok", "message": "Timeline is projected from persisted histories"}
