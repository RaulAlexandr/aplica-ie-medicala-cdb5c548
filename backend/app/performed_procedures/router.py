from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import (
    Appointment,
    AuditEvent,
    Patient,
    PerformedProcedure,
    PerformedProcedureRevision,
    ProcedureCatalog,
    ProcedureExecutionSegment,
    User,
    get_db,
    utcnow,
)

router = APIRouter(prefix="/performed-procedures", tags=["performed_procedures"])

# Roles
MANAGERS = ("clinic_manager", "administrator")
DOCTORS = ("clinic_manager", "doctor", "administrator")
STAFF_ROLES = ("clinic_manager", "doctor", "assistant", "reception", "administrator")

# Status transitions for PerformedProcedure
PROCEDURE_STATUSES = {"pending", "running", "paused", "completed", "cancelled"}
PROCEDURE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled"},
    "running": {"paused", "completed", "cancelled"},
    "paused": {"running", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

# Segment types
class SegmentType(str, Enum):
    RUN = "run"
    PAUSE = "pause"

# FDI tooth validation - adult teeth: 11-18, 21-28, 31-38, 41-48
FDI_PATTERN = r"^[1-4][1-8]$"
# Surface validation - common dental surfaces
VALID_SURFACES = {
    "mesial", "distal", "buccal", "lingual", "palatal", "occlusal", "incisal",
    "vestibular", "labial", "approximal", "interproximal"
}
MAX_SURFACE_LENGTH = 100


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


def validate_price_value(value: object) -> Decimal:
    if isinstance(value, Decimal):
        pass
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Price cannot be empty")
        try:
            value = Decimal(value)
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("Price must be a valid decimal number") from exc
    elif isinstance(value, (int, float)):
        raise TypeError("Price must be provided as a string for exact decimal representation")
    else:
        raise TypeError("Price must be a decimal number")
    
    if value.is_infinite() or value.is_nan():
        raise ValueError("Price must be a finite number")
    
    if value < Decimal("0.00"):
        raise ValueError("Price must be at least 0.00")
    if value > Decimal("999999999.99"):
        raise ValueError("Price must be at most 999999999.99")
    
    if value.as_tuple().exponent < -2:
        raise ValueError("Price must have at most 2 decimal places")
    
    return value


# Pydantic models
class PerformedProcedureCreate(BaseModel):
    appointment_id: UUID
    procedure_catalog_id: UUID
    tooth_fdi: str | None = Field(default=None, max_length=3)
    tooth_surface: str | None = Field(default=None, max_length=MAX_SURFACE_LENGTH)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("tooth_fdi", mode="before")
    @classmethod
    def validate_tooth_fdi(cls, value: str | None) -> str | None:
        return validate_fdi_tooth(value)

    @field_validator("tooth_surface", mode="before")
    @classmethod
    def validate_tooth_surface(cls, value: str | None) -> str | None:
        return validate_surface(value)


class PerformedProcedureStatusTransition(BaseModel):
    transition: str = Field(description="Transition to apply: start, pause, resume, complete, cancel")
    notes: str | None = Field(default=None, max_length=500)


class ManualDurationUpdate(BaseModel):
    duration_seconds: int = Field(ge=0, le=24 * 3600, description="Manual duration in seconds")
    reason: str = Field(min_length=1, max_length=500, description="Reason for manual duration override")


class PriceOverride(BaseModel):
    new_price: Decimal = Field(ge=Decimal("0.00"), le=Decimal("999999999.99"))
    reason: str = Field(min_length=1, max_length=500, description="Reason for price override")

    @field_validator("new_price", mode="before")
    @classmethod
    def parse_price(cls, value: object) -> Decimal:
        return validate_price_value(value)


class UpdateProcedureNotes(BaseModel):
    notes: str = Field(max_length=1000, description="Procedure notes, maximum 1000 characters")


class PerformedProcedureResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    appointment_id: UUID
    patient_id: UUID
    procedure_catalog_id: UUID
    executed_by_id: UUID
    procedure_code: str
    procedure_name: str
    procedure_base_price: str
    procedure_currency: str
    procedure_default_duration: int
    tooth_fdi: str | None
    tooth_surface: str | None
    status: str
    current_price: str | None
    total_duration_seconds: int | None
    notes: str | None
    created_at: str
    updated_at: str
    model_config = ConfigDict(from_attributes=True)


class ProcedureSegmentResponse(BaseModel):
    id: UUID
    performed_procedure_id: UUID
    segment_type: str
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    created_at: str
    model_config = ConfigDict(from_attributes=True)


class PerformedProcedureRevisionResponse(BaseModel):
    id: UUID
    clinic_id: UUID
    performed_procedure_id: UUID
    actor_id: UUID
    field: str
    previous_value: str | None
    new_value: str | None
    reason: str | None
    occurred_at: str
    model_config = ConfigDict(from_attributes=True)


class VisitSummaryResponse(BaseModel):
    appointment_id: UUID
    patient_id: UUID
    patient_name: str
    appointment_starts_at: str
    appointment_status: str
    performed_procedures: list[PerformedProcedureResponse]
    can_close_visit: bool
    unresolved_procedures: list[dict]


def _response(procedure: PerformedProcedure, segments: list | None = None) -> PerformedProcedureResponse:
    # Calculate total duration from explicitly provided segments only
    # Avoid accessing procedure.segments in sync code to prevent lazy loading issues
    if segments is not None:
        total_duration = _calculate_total_duration(segments)
    else:
        total_duration = None
    
    return PerformedProcedureResponse.model_validate(
        {
            **procedure.__dict__,
            "current_price": str(procedure.current_price) if procedure.current_price is not None else None,
            "procedure_base_price": str(procedure.procedure_base_price),
            "total_duration_seconds": total_duration,
            "created_at": procedure.created_at.isoformat(),
            "updated_at": procedure.updated_at.isoformat(),
        },
        from_attributes=True,
    )


def _segment_response(segment: ProcedureExecutionSegment) -> ProcedureSegmentResponse:
    return ProcedureSegmentResponse.model_validate(
        {
            **segment.__dict__,
            "started_at": segment.started_at.isoformat(),
            "ended_at": segment.ended_at.isoformat() if segment.ended_at else None,
            "created_at": segment.created_at.isoformat(),
        },
        from_attributes=True,
    )


def _normalize_dt(dt: datetime | None) -> datetime | None:
    """Normalize datetime to UTC timezone-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume naive datetime is UTC
        return dt.replace(tzinfo=UTC)
    return dt


def _calculate_total_duration(segments: list[ProcedureExecutionSegment]) -> int | None:
    """Calculate total active duration from segments."""
    total = 0
    for segment in segments:
        if segment.segment_type == SegmentType.RUN and segment.ended_at:
            started = _normalize_dt(segment.started_at)
            ended = _normalize_dt(segment.ended_at)
            duration = (ended - started).total_seconds()
            total += int(duration)
        elif segment.segment_type == SegmentType.RUN and segment.duration_seconds:
            total += segment.duration_seconds
    return total if total > 0 else None


async def _get_performed_procedure(
    db: AsyncSession, procedure_id: UUID, user: User, for_update: bool = False
) -> PerformedProcedure | None:
    """Get a performed procedure by ID.
    
    Note: Does NOT load segments to avoid lazy loading issues.
    Use _get_active_segments separately or load explicitly in the caller.
    Lock order: User row (for doctor) -> PerformedProcedure row.
    """
    query = select(PerformedProcedure).where(
        PerformedProcedure.id == procedure_id,
        PerformedProcedure.clinic_id == user.clinic_id
    )
    if for_update:
        query = query.with_for_update()
    
    result = await db.execute(query)
    return result.unique().scalar_one_or_none()


async def _get_performed_procedure_with_segments(
    db: AsyncSession, procedure_id: UUID, user: User, for_update: bool = False
) -> tuple[PerformedProcedure | None, list[ProcedureExecutionSegment]]:
    """Get a performed procedure with its segments.
    
    Safe alternative to joinedload that avoids lazy loading issues.
    """
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update)
    if procedure:
        segments = await _get_active_segments(db, procedure_id)
    else:
        segments = []
    return procedure, segments


async def _validate_appointment_access(
    db: AsyncSession, appointment_id: UUID, user: User
) -> Appointment | None:
    return await db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.clinic_id == user.clinic_id
        )
    )


async def _validate_procedure_catalog_access(
    db: AsyncSession, catalog_id: UUID, user: User
) -> ProcedureCatalog | None:
    return await db.scalar(
        select(ProcedureCatalog).where(
            ProcedureCatalog.id == catalog_id,
            ProcedureCatalog.clinic_id == user.clinic_id
        )
    )


async def _check_doctor_has_running_procedure(
    db: AsyncSession, doctor_id: UUID, clinic_id: UUID, exclude_procedure_id: UUID | None = None
) -> bool:
    """Check if doctor already has a running procedure.
    
    Lock order (to prevent deadlocks):
    1. Lock the User row for the executing doctor (prevents concurrent start/resume for same doctor)
    2. Check for existing running procedures
    
    This ensures that two concurrent start/resume operations for the same doctor
    will be serialized by the lock on the User row.
    """
    # First, lock the doctor's user row to prevent concurrent operations
    await db.execute(
        select(User).where(
            User.id == doctor_id,
            User.clinic_id == clinic_id
        ).with_for_update()
    )
    
    # Then check for existing running procedures
    query = select(PerformedProcedure.id).where(
        PerformedProcedure.clinic_id == clinic_id,
        PerformedProcedure.executed_by_id == doctor_id,
        PerformedProcedure.status == "running"
    )
    if exclude_procedure_id:
        query = query.where(PerformedProcedure.id != exclude_procedure_id)
    result = await db.scalar(query)
    return result is not None


async def _get_active_segments(
    db: AsyncSession, procedure_id: UUID
) -> list[ProcedureExecutionSegment]:
    """Get all segments for a procedure, ordered by started_at."""
    segments = await db.scalars(
        select(ProcedureExecutionSegment)
        .where(ProcedureExecutionSegment.performed_procedure_id == procedure_id)
        .order_by(ProcedureExecutionSegment.started_at)
    )
    return list(segments)


async def _get_last_segment(
    db: AsyncSession, procedure_id: UUID
) -> ProcedureExecutionSegment | None:
    """Get the last segment for a procedure."""
    segments = await db.scalars(
        select(ProcedureExecutionSegment)
        .where(ProcedureExecutionSegment.performed_procedure_id == procedure_id)
        .order_by(ProcedureExecutionSegment.started_at.desc())
        .limit(1)
    )
    return segments.first() if segments else None


@router.get("", response_model=list[PerformedProcedureResponse])
async def list_performed_procedures(
    appointment_id: UUID | None = Query(default=None),
    patient_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> list[PerformedProcedureResponse]:
    query = select(PerformedProcedure).where(
        PerformedProcedure.clinic_id == user.clinic_id
    ).order_by(PerformedProcedure.created_at.desc()).offset(offset).limit(limit)
    
    if appointment_id:
        query = query.where(PerformedProcedure.appointment_id == appointment_id)
    if patient_id:
        query = query.where(PerformedProcedure.patient_id == patient_id)
    if status and status in PROCEDURE_STATUSES:
        query = query.where(PerformedProcedure.status == status)
    
    result = await db.execute(query)
    procedures = result.unique().scalars().all()
    
    # Eager load segments for all procedures in a single query to avoid N+1
    procedure_ids = [p.id for p in procedures]
    if procedure_ids:
        segments_result = await db.execute(
            select(ProcedureExecutionSegment).where(
                ProcedureExecutionSegment.performed_procedure_id.in_(procedure_ids)
            ).order_by(ProcedureExecutionSegment.performed_procedure_id, ProcedureExecutionSegment.started_at)
        )
        segments_by_proc = {}
        for seg in segments_result.unique().scalars().all():
            if seg.performed_procedure_id not in segments_by_proc:
                segments_by_proc[seg.performed_procedure_id] = []
            segments_by_proc[seg.performed_procedure_id].append(seg)
        
        responses = []
        for p in procedures:
            p_segments = segments_by_proc.get(p.id, [])
            responses.append(_response(p, segments=p_segments))
        return responses
    else:
        return [_response(p, segments=[]) for p in procedures]


@router.get("/{procedure_id}", response_model=PerformedProcedureResponse)
async def get_performed_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> PerformedProcedureResponse:
    procedure, segments = await _get_performed_procedure_with_segments(db, procedure_id, user, for_update=False)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    return _response(procedure, segments=segments)


@router.get("/{procedure_id}/segments", response_model=list[ProcedureSegmentResponse])
async def get_procedure_segments(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> list[ProcedureSegmentResponse]:
    procedure = await _get_performed_procedure(db, procedure_id, user)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    segments = await _get_active_segments(db, procedure_id)
    return [_segment_response(s) for s in segments]


@router.get("/{procedure_id}/revisions", response_model=list[PerformedProcedureRevisionResponse])
async def get_procedure_revisions(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> list[PerformedProcedureRevisionResponse]:
    procedure = await _get_performed_procedure(db, procedure_id, user)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    revisions = await db.scalars(
        select(PerformedProcedureRevision)
        .where(PerformedProcedureRevision.performed_procedure_id == procedure_id)
        .order_by(PerformedProcedureRevision.occurred_at.desc())
    )
    return [
        PerformedProcedureRevisionResponse.model_validate(
            {
                **r.__dict__,
                "occurred_at": r.occurred_at.isoformat(),
            },
            from_attributes=True,
        )
        for r in revisions
    ]


@router.post("", response_model=PerformedProcedureResponse, status_code=status.HTTP_201_CREATED)
async def create_performed_procedure(
    payload: PerformedProcedureCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> PerformedProcedureResponse:
    # Validate appointment exists and belongs to clinic
    appointment = await _validate_appointment_access(db, payload.appointment_id, user)
    if not appointment:
        raise HTTPException(422, "Appointment not found or access denied")
    
    # Validate procedure catalog exists and belongs to clinic
    catalog = await _validate_procedure_catalog_access(db, payload.procedure_catalog_id, user)
    if not catalog:
        raise HTTPException(422, "Procedure catalog not found or access denied")
    
    # Check if appointment patient matches
    patient = await db.scalar(
        select(Patient).where(Patient.id == appointment.patient_id, Patient.clinic_id == user.clinic_id)
    )
    if not patient:
        raise HTTPException(422, "Patient not found or access denied")
    
    # Create the performed procedure with snapshot of catalog data
    procedure = PerformedProcedure(
        clinic_id=user.clinic_id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        procedure_catalog_id=catalog.id,
        executed_by_id=appointment.doctor_id,
        procedure_code=catalog.code,
        procedure_name=catalog.name,
        procedure_base_price=catalog.base_price,
        procedure_currency=catalog.currency,
        procedure_default_duration=catalog.default_duration_minutes,
        tooth_fdi=payload.tooth_fdi,
        tooth_surface=payload.tooth_surface,
        status="pending",
        current_price=catalog.base_price,
        notes=payload.notes,
    )
    
    db.add(procedure)
    await db.flush()
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="created"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    return _response(procedure)


@router.post("/{procedure_id}/start", response_model=PerformedProcedureResponse)
async def start_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Check ownership - only the executing doctor or manager can start
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can start this procedure")
    
    # Validate transition
    if procedure.status not in PROCEDURE_TRANSITIONS or "running" not in PROCEDURE_TRANSITIONS[procedure.status]:
        raise HTTPException(409, f"Cannot start procedure from status: {procedure.status}")
    
    # Check if executed_by doctor already has a running procedure
    has_running = await _check_doctor_has_running_procedure(db, procedure.executed_by_id, user.clinic_id, procedure_id)
    if has_running:
        raise HTTPException(
            409,
            "Doctor already has a running procedure. Only one timer per doctor is allowed."
        )
    
    # Check if any other procedure is running for this appointment (with FOR UPDATE)
    existing_running = await db.scalar(
        select(PerformedProcedure.id).where(
            PerformedProcedure.appointment_id == procedure.appointment_id,
            PerformedProcedure.clinic_id == user.clinic_id,
            PerformedProcedure.status == "running",
            PerformedProcedure.id != procedure_id
        ).with_for_update()
    )
    if existing_running:
        raise HTTPException(
            409,
            "Another procedure is already running for this appointment"
        )
    
    # Create start segment
    now = utcnow()
    segment = ProcedureExecutionSegment(
        performed_procedure_id=procedure.id,
        segment_type=SegmentType.RUN,
        started_at=now,
        ended_at=None,
        duration_seconds=None,
    )
    db.add(segment)
    
    # Update procedure status
    procedure.status = "running"
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="started"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    # Load segments after commit
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/pause", response_model=PerformedProcedureResponse)
async def pause_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Check ownership
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can pause this procedure")
    
    # Validate transition
    if procedure.status not in PROCEDURE_TRANSITIONS or "paused" not in PROCEDURE_TRANSITIONS[procedure.status]:
        raise HTTPException(409, f"Cannot pause procedure from status: {procedure.status}")
    
    # Get last segment and close it
    last_segment = await _get_last_segment(db, procedure_id)
    if last_segment and last_segment.segment_type == SegmentType.RUN and last_segment.ended_at is None:
        last_segment.ended_at = utcnow()
        duration = (_normalize_dt(last_segment.ended_at) - _normalize_dt(last_segment.started_at)).total_seconds()
        last_segment.duration_seconds = int(duration)
    
    # Create pause segment
    now = utcnow()
    pause_segment = ProcedureExecutionSegment(
        performed_procedure_id=procedure.id,
        segment_type=SegmentType.PAUSE,
        started_at=now,
        ended_at=None,
        duration_seconds=None,
    )
    db.add(pause_segment)
    
    # Update procedure status
    procedure.status = "paused"
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="paused"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/resume", response_model=PerformedProcedureResponse)
async def resume_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Check ownership
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can resume this procedure")
    
    # Validate transition
    if procedure.status not in PROCEDURE_TRANSITIONS or "running" not in PROCEDURE_TRANSITIONS[procedure.status]:
        raise HTTPException(409, f"Cannot resume procedure from status: {procedure.status}")
    
    # Check if executed_by doctor already has a running procedure
    has_running = await _check_doctor_has_running_procedure(db, procedure.executed_by_id, user.clinic_id, procedure_id)
    if has_running:
        raise HTTPException(
            409,
            "Doctor already has a running procedure. Only one timer per doctor is allowed."
        )
    
    # Close the pause segment
    last_segment = await _get_last_segment(db, procedure_id)
    if last_segment and last_segment.segment_type == SegmentType.PAUSE and last_segment.ended_at is None:
        last_segment.ended_at = utcnow()
        duration = (_normalize_dt(last_segment.ended_at) - _normalize_dt(last_segment.started_at)).total_seconds()
        last_segment.duration_seconds = int(duration)
    
    # Create new run segment
    now = utcnow()
    run_segment = ProcedureExecutionSegment(
        performed_procedure_id=procedure.id,
        segment_type=SegmentType.RUN,
        started_at=now,
        ended_at=None,
        duration_seconds=None,
    )
    db.add(run_segment)
    
    # Update procedure status
    procedure.status = "running"
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="resumed"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/complete", response_model=PerformedProcedureResponse)
async def complete_procedure(
    procedure_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Check ownership
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can complete this procedure")
    
    # Validate transition
    if procedure.status not in PROCEDURE_TRANSITIONS or "completed" not in PROCEDURE_TRANSITIONS[procedure.status]:
        raise HTTPException(409, f"Cannot complete procedure from status: {procedure.status}")
    
    # Close the last segment
    last_segment = await _get_last_segment(db, procedure_id)
    if last_segment and last_segment.ended_at is None:
        last_segment.ended_at = utcnow()
        duration = (_normalize_dt(last_segment.ended_at) - _normalize_dt(last_segment.started_at)).total_seconds()
        last_segment.duration_seconds = int(duration)
    
    # Update procedure status
    now = utcnow()
    procedure.status = "completed"
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="completed"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/cancel", response_model=PerformedProcedureResponse)
async def cancel_procedure(
    procedure_id: UUID,
    payload: PerformedProcedureStatusTransition,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Check ownership - doctor or manager can cancel
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can cancel this procedure")
    
    # Validate transition
    if procedure.status not in PROCEDURE_TRANSITIONS or "cancelled" not in PROCEDURE_TRANSITIONS[procedure.status]:
        raise HTTPException(409, f"Cannot cancel procedure from status: {procedure.status}")
    
    # Close the last segment if running or paused
    last_segment = await _get_last_segment(db, procedure_id)
    if last_segment and last_segment.ended_at is None:
        last_segment.ended_at = utcnow()
        duration = (_normalize_dt(last_segment.ended_at) - _normalize_dt(last_segment.started_at)).total_seconds()
        last_segment.duration_seconds = int(duration)
    
    # Update procedure status
    now = utcnow()
    procedure.status = "cancelled"
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="cancelled"
    ))
    
    if payload.notes:
        db.add(PerformedProcedureRevision(
            clinic_id=user.clinic_id,
            performed_procedure_id=procedure.id,
            actor_id=user.id,
            field="status",
            previous_value=procedure.status,
            new_value="cancelled",
            reason=payload.notes,
            occurred_at=now
        ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/override-duration", response_model=PerformedProcedureResponse)
async def override_duration(
    procedure_id: UUID,
    payload: ManualDurationUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Only manager or the executing doctor can override duration
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can override duration")
    
    # Record the manual duration override in revisions
    now = utcnow()
    db.add(PerformedProcedureRevision(
        clinic_id=user.clinic_id,
        performed_procedure_id=procedure.id,
        actor_id=user.id,
        field="manual_duration_seconds",
        previous_value=str(procedure.notes) if procedure.notes else None,
        new_value=str(payload.duration_seconds),
        reason=payload.reason,
        occurred_at=now
    ))
    
    # Update notes to include manual duration info
    manual_note = f"Manual duration override: {payload.duration_seconds}s - {payload.reason}"
    if procedure.notes:
        procedure.notes = f"{procedure.notes}\n{manual_note}"
    else:
        procedure.notes = manual_note
    
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="duration_override"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.post("/{procedure_id}/override-price", response_model=PerformedProcedureResponse)
async def override_price(
    procedure_id: UUID,
    payload: PriceOverride,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*MANAGERS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    # Only manager can override price
    if user.role not in MANAGERS:
        raise HTTPException(403, "Only manager can override price")
    
    old_price = procedure.current_price
    now = utcnow()
    
    # Record the price change in revisions
    db.add(PerformedProcedureRevision(
        clinic_id=user.clinic_id,
        performed_procedure_id=procedure.id,
        actor_id=user.id,
        field="current_price",
        previous_value=str(old_price) if old_price is not None else None,
        new_value=str(payload.new_price),
        reason=payload.reason,
        occurred_at=now
    ))
    
    # Update the price
    procedure.current_price = payload.new_price
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="price_override"
    ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)


@router.get("/visit/{appointment_id}/summary", response_model=VisitSummaryResponse)
async def get_visit_summary(
    appointment_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*STAFF_ROLES)),
) -> VisitSummaryResponse:
    # Validate appointment
    appointment = await _validate_appointment_access(db, appointment_id, user)
    if not appointment:
        raise HTTPException(404, "Appointment not found or access denied")
    
    # Get patient
    patient = await db.scalar(
        select(Patient).where(Patient.id == appointment.patient_id, Patient.clinic_id == user.clinic_id)
    )
    if not patient:
        raise HTTPException(404, "Patient not found")
    
    # Get all performed procedures for this appointment
    result = await db.execute(
        select(PerformedProcedure).where(
            PerformedProcedure.appointment_id == appointment_id,
            PerformedProcedure.clinic_id == user.clinic_id
        ).order_by(PerformedProcedure.created_at)
    )
    procedures = result.unique().scalars().all()
    
    # Load all segments for these procedures
    procedure_ids = [p.id for p in procedures]
    segments_by_proc = {}
    if procedure_ids:
        segments_result = await db.execute(
            select(ProcedureExecutionSegment).where(
                ProcedureExecutionSegment.performed_procedure_id.in_(procedure_ids)
            ).order_by(ProcedureExecutionSegment.performed_procedure_id, ProcedureExecutionSegment.started_at)
        )
        for seg in segments_result.unique().scalars().all():
            if seg.performed_procedure_id not in segments_by_proc:
                segments_by_proc[seg.performed_procedure_id] = []
            segments_by_proc[seg.performed_procedure_id].append(seg)
    
    procedure_responses = []
    for p in procedures:
        p_segments = segments_by_proc.get(p.id, [])
        procedure_responses.append(_response(p, segments=p_segments))
    
    # Check for unresolved procedures (running or paused)
    unresolved = []
    can_close = True
    for p in procedure_responses:
        if p.status in ("running", "paused") or p.status == "pending":
            can_close = False
            unresolved.append({
                "id": str(p.id),
                "procedure_name": p.procedure_name,
                "status": p.status,
                "executed_by": str(p.executed_by_id)
            })
    
    return VisitSummaryResponse(
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        appointment_starts_at=appointment.starts_at.isoformat(),
        appointment_status=appointment.status,
        performed_procedures=procedure_responses,
        can_close_visit=can_close,
        unresolved_procedures=unresolved
    )


@router.patch("/{procedure_id}/notes", response_model=PerformedProcedureResponse)
async def update_procedure_notes(
    procedure_id: UUID,
    payload: UpdateProcedureNotes = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*DOCTORS, *MANAGERS)),
) -> PerformedProcedureResponse:
    procedure = await _get_performed_procedure(db, procedure_id, user, for_update=True)
    if not procedure:
        raise HTTPException(404, "Performed procedure not found or access denied")
    
    if procedure.executed_by_id != user.id and user.role not in MANAGERS:
        raise HTTPException(403, "Only the executing doctor or manager can update notes")
    
    old_notes = procedure.notes
    now = utcnow()
    
    procedure.notes = payload.notes
    procedure.updated_at = now
    
    db.add(AuditEvent(
        clinic_id=user.clinic_id,
        actor_id=user.id,
        entity_type="performed_procedure",
        entity_id=procedure.id,
        action="notes_updated"
    ))
    
    if old_notes != payload.notes:
        db.add(PerformedProcedureRevision(
            clinic_id=user.clinic_id,
            performed_procedure_id=procedure.id,
            actor_id=user.id,
            field="notes",
            previous_value=old_notes,
            new_value=payload.notes,
            reason="Notes updated",
            occurred_at=now
        ))
    
    await db.commit()
    await db.refresh(procedure)
    
    segments = await _get_active_segments(db, procedure_id)
    return _response(procedure, segments=segments)
