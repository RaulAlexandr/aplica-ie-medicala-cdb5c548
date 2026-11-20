from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, mapped_column, relationship

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDType(TypeDecorator[UUID]):
    impl = PGUUID(as_uuid=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(36) if dialect.name == "sqlite" else PGUUID(as_uuid=True))

    def process_bind_param(self, value: UUID | None, dialect):
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, UUID):
            return value
        return UUID(value)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC instants on every dialect.

    PostgreSQL (timestamptz) keeps the DDL unchanged. SQLite stores the wall-clock text without an
    offset, so: values are converted to UTC on write (a +02:00 value would otherwise be stored as
    local wall time), and naive values read back from existing rows are interpreted as UTC. A naive
    value on write is treated as UTC (documented convention; the API rejects naive appointment input).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    @staticmethod
    def normalize(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_bind_param(self, value, dialect):
        return self.normalize(value)

    def process_result_value(self, value, dialect):
        return self.normalize(value)


class Base(DeclarativeBase):
    pass


@event.listens_for(Mapper, "mapper_configured")
def _normalize_utc_attributes(mapper, class_):
    """Normalize assigned values too, so in-memory objects serialize as UTC before any reload."""
    for prop in mapper.column_attrs:
        if isinstance(prop.columns[0].type, UTCDateTime):
            event.listen(getattr(class_, prop.key), "set", lambda target, value, old, initiator: UTCDateTime.normalize(value), retval=True)


class Clinic(Base):
    __tablename__ = "clinics"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Bucharest")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    users: Mapped[list["User"]] = relationship(back_populates="clinic")
    rooms: Mapped[list["Room"]] = relationship(back_populates="clinic")


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    specialization: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    clinic: Mapped[Clinic] = relationship(back_populates="users")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class StaffInvitation(Base):
    __tablename__ = "staff_invitations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    invited_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clinic: Mapped[Clinic] = relationship(back_populates="rooms")
    __table_args__ = (
        UniqueConstraint("clinic_id", "name", name="uq_room_clinic_name"),
        Index("uq_room_clinic_lower_name", "clinic_id", func.lower(name), unique=True),
    )


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(30))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320))
    address: Mapped[str | None] = mapped_column(String(300))
    emergency_contact: Mapped[str | None] = mapped_column(String(160))
    occupation: Mapped[str | None] = mapped_column(String(120))
    allergies: Mapped[str | None] = mapped_column(Text)
    medications: Mapped[str | None] = mapped_column(Text)
    chronic_diseases: Mapped[str | None] = mapped_column(Text)
    pregnancy_status: Mapped[str | None] = mapped_column(String(40))
    smoking_status: Mapped[str | None] = mapped_column(String(40))
    previous_surgeries: Mapped[str | None] = mapped_column(Text)
    relevant_medical_conditions: Mapped[str | None] = mapped_column(Text)
    medical_alerts: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (Index("ix_patients_clinic_name", "clinic_id", "last_name", "first_name"),)


class PatientRevision(Base):
    __tablename__ = "patient_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assistant_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    appointment_type: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (Index("ix_appointments_clinic_starts", "clinic_id", "starts_at"),)


class WorkingHours(Base):
    __tablename__ = "working_hours"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (Index("ix_working_hours_clinic_day", "clinic_id", "day_of_week"),)

class ResourceUnavailability(Base):
    __tablename__ = "resource_unavailability"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(240))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

class AppointmentHistory(Base):
    __tablename__ = "appointment_history"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class ProcedureCatalog(Base):
    __tablename__ = "procedure_catalog"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    code_lower: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(80))
    default_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "code_lower", name="uq_procedure_catalog_clinic_code"),
        Index("ix_procedure_catalog_clinic_active", "clinic_id", "is_active"),
        Index("ix_procedure_catalog_clinic_name", "clinic_id", "name"),
    )


class PerformedProcedure(Base):
    __tablename__ = "performed_procedures"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    procedure_catalog_id: Mapped[UUID] = mapped_column(ForeignKey("procedure_catalog.id"), nullable=False, index=True)
    executed_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    procedure_code: Mapped[str] = mapped_column(String(40), nullable=False)
    procedure_name: Mapped[str] = mapped_column(String(160), nullable=False)
    procedure_base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    procedure_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    procedure_default_duration: Mapped[int] = mapped_column(Integer, nullable=False)
    tooth_fdi: Mapped[str | None] = mapped_column(String(3), index=True)
    tooth_surface: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    segments: Mapped[list["ProcedureExecutionSegment"]] = relationship(back_populates="performed_procedure", cascade="all, delete-orphan")
    revisions: Mapped[list["PerformedProcedureRevision"]] = relationship(back_populates="performed_procedure", cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_performed_procedures_appointment", "appointment_id"),
        Index("ix_performed_procedures_patient", "patient_id"),
        Index("ix_performed_procedures_executor", "executed_by_id"),
        Index("ix_performed_procedures_status", "clinic_id", "status"),
    )


class ProcedureExecutionSegment(Base):
    __tablename__ = "procedure_execution_segments"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    performed_procedure_id: Mapped[UUID] = mapped_column(ForeignKey("performed_procedures.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    performed_procedure: Mapped["PerformedProcedure"] = relationship(back_populates="segments")
    __table_args__ = (
        Index("ix_segments_procedure", "performed_procedure_id"),
        Index("ix_segments_started", "started_at"),
    )


class PerformedProcedureRevision(Base):
    __tablename__ = "performed_procedure_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    performed_procedure_id: Mapped[UUID] = mapped_column(ForeignKey("performed_procedures.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)
    performed_procedure: Mapped["PerformedProcedure"] = relationship(back_populates="revisions")


class ClinicalNote(Base):
    __tablename__ = "clinical_notes"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    appointment_id: Mapped[UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    note_type: Mapped[str] = mapped_column(String(30), nullable=False, default="note")
    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tooth_fdi: Mapped[str | None] = mapped_column(String(3), index=True)
    tooth_surface: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    is_alert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revisions: Mapped[list["ClinicalNoteRevision"]] = relationship(back_populates="note", cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_clinical_notes_patient", "patient_id"),
        Index("ix_clinical_notes_appointment", "appointment_id"),
        Index("ix_clinical_notes_author", "author_id"),
        Index("ix_clinical_notes_clinic_status", "clinic_id", "status"),
    )


class ClinicalNoteRevision(Base):
    __tablename__ = "clinical_note_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    clinical_note_id: Mapped[UUID] = mapped_column(ForeignKey("clinical_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False, index=True)
    note: Mapped["ClinicalNote"] = relationship(back_populates="revisions")


class OdontogramToothState(Base):
    __tablename__ = "odontogram_tooth_states"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    tooth_fdi: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unevaluated")
    updated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("patient_id", "tooth_fdi", name="uq_odontogram_tooth_patient_fdi"),
        Index("ix_odontogram_tooth_clinic_patient", "clinic_id", "patient_id"),
    )


class OdontogramToothRevision(Base):
    __tablename__ = "odontogram_tooth_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    tooth_fdi: Mapped[str] = mapped_column(String(2), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    previous_status: Mapped[str] = mapped_column(String(20), nullable=False)
    current_status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_odontogram_tooth_revisions_patient", "clinic_id", "patient_id", "occurred_at"),)


class OdontogramFinding(Base):
    __tablename__ = "odontogram_findings"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    tooth_fdi: Mapped[str] = mapped_column(String(2), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    surfaces: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = (
        Index("ix_odontogram_findings_clinic_patient", "clinic_id", "patient_id", "created_at"),
        UniqueConstraint("clinic_id", "author_id", "idempotency_key", name="uq_odontogram_finding_retry"),
    )


class OdontogramFindingRevision(Base):
    __tablename__ = "odontogram_finding_revisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    finding_id: Mapped[UUID] = mapped_column(ForeignKey("odontogram_findings.id", ondelete="RESTRICT"), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    previous_value: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    current_value: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_odontogram_finding_revisions", "clinic_id", "finding_id", "occurred_at"),)


class PatientTimelineEvent(Base):
    __tablename__ = "patient_timeline_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[UUID | None] = mapped_column(UUIDType)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    source: Mapped[str | None] = mapped_column(String(50))
    metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        Index("ix_timeline_patient", "patient_id"),
        Index("ix_timeline_clinic", "clinic_id"),
        Index("ix_timeline_occurred", "occurred_at"),
        Index("ix_timeline_type", "clinic_id", "event_type"),
    )


class TreatmentPlan(Base):
    """T08 structured treatment plan. Content edits bump ``version``; acceptance freezes one version."""

    __tablename__ = "treatment_plans"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    accepted_version: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_treatment_plans_clinic_patient", "clinic_id", "patient_id", "created_at"),
        UniqueConstraint("clinic_id", "created_by_id", "idempotency_key", name="uq_treatment_plan_retry"),
    )


class TreatmentPlanStage(Base):
    __tablename__ = "treatment_plan_stages"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (Index("ix_treatment_plan_stages_plan", "plan_id", "position"),)


class TreatmentPlanItem(Base):
    """Planned procedure. Estimate fields are a catalog snapshot; links never create T04 records."""

    __tablename__ = "treatment_plan_items"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(ForeignKey("treatment_plan_stages.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    procedure_catalog_id: Mapped[UUID] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False)
    procedure_code: Mapped[str] = mapped_column(String(40), nullable=False)
    procedure_name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    tooth_fdi: Mapped[str | None] = mapped_column(String(3))
    tooth_surface: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    appointment_id: Mapped[UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"))
    appointment_linked_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    appointment_linked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    performed_procedure_id: Mapped[UUID | None] = mapped_column(ForeignKey("performed_procedures.id", ondelete="RESTRICT"))
    performed_linked_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    performed_linked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_treatment_plan_items_plan", "plan_id", "stage_id", "position"),
        UniqueConstraint("performed_procedure_id", name="uq_treatment_plan_item_performed"),
        UniqueConstraint("plan_id", "idempotency_key", name="uq_treatment_plan_item_retry"),
    )


class TreatmentPlanAcceptance(Base):
    """Internal staff record that a given plan version was accepted; not an e-signature."""

    __tablename__ = "treatment_plan_acceptances"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    __table_args__ = (UniqueConstraint("plan_id", "version", name="uq_treatment_plan_acceptance_version"),)


class TreatmentPlanEvent(Base):
    __tablename__ = "treatment_plan_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[UUID | None] = mapped_column(UUIDType)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (Index("ix_treatment_plan_events_plan", "plan_id", "occurred_at"),)


class ClinicalDocument(Base):
    """T11 protected document/image metadata. Binary content lives outside the database, under a
    server-generated storage key; the original filename is display metadata only."""

    __tablename__ = "clinical_documents"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    appointment_id: Mapped[UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"))
    tooth_fdi: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    clinical_date: Mapped[date | None] = mapped_column(Date)
    uploaded_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    archived_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    archive_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("ix_clinical_documents_patient", "clinic_id", "patient_id", "uploaded_at"),
        Index("ix_clinical_documents_appointment", "appointment_id"),
        UniqueConstraint("storage_key", name="uq_clinical_document_storage_key"),
        UniqueConstraint("clinic_id", "uploaded_by_id", "idempotency_key", name="uq_clinical_document_retry"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_clinical_document_status"),
        CheckConstraint("category IN ('photo', 'radiograph', 'document', 'other')", name="ck_clinical_document_category"),
        CheckConstraint("content_type IN ('image/jpeg', 'image/png', 'application/pdf')", name="ck_clinical_document_type"),
        CheckConstraint("size_bytes > 0", name="ck_clinical_document_size"),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL AND archived_by_id IS NOT NULL AND archive_reason IS NOT NULL)",
            name="ck_clinical_document_archive",
        ),
    )


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


class PeriodontalAssessment(Base):
    __tablename__ = "periodontal_assessments"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    clinical_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    previous_assessment_id: Mapped[UUID | None] = mapped_column(ForeignKey("periodontal_assessments.id", ondelete="RESTRICT"))
    relationship_kind: Mapped[str | None] = mapped_column(String(16))
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (Index("ix_periodontal_patient_date", "clinic_id", "patient_id", "clinical_date"),)


class PeriodontalAssessmentTooth(Base):
    __tablename__ = "periodontal_assessment_teeth"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(ForeignKey("periodontal_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    tooth_fdi: Mapped[str] = mapped_column(String(2), nullable=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mobility: Mapped[str | None] = mapped_column(String(8))
    furcation: Mapped[str | None] = mapped_column(String(8))
    __table_args__ = (UniqueConstraint("assessment_id", "tooth_fdi", name="uq_periodontal_assessment_tooth"),)


class PeriodontalSiteMeasurement(Base):
    __tablename__ = "periodontal_site_measurements"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    tooth_id: Mapped[UUID] = mapped_column(ForeignKey("periodontal_assessment_teeth.id", ondelete="CASCADE"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(24), nullable=False)
    probing_depth_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    recession_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    bop: Mapped[bool | None] = mapped_column(Boolean)
    plaque: Mapped[bool | None] = mapped_column(Boolean)
    suppuration: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (UniqueConstraint("tooth_id", "site", name="uq_periodontal_tooth_site"),)


# ---- T13: internal patient financial ledger (not fiscal invoicing) ----
# Immutable movements; services owed are a projection of completed T04 procedures, never copied here.
FINANCE_ENTRY_KINDS = ("payment", "refund", "adjustment_credit", "adjustment_debit", "reversal")
FINANCE_METHODS = ("cash", "card", "transfer", "other")


class PatientFinancialEntry(Base):
    __tablename__ = "patient_financial_entries"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    appointment_id: Mapped[UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RON")
    method: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    reverses_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patient_financial_entries.id", ondelete="RESTRICT"))
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_patient_financial_entries_patient", "clinic_id", "patient_id", "created_at"),
        Index("ix_patient_financial_entries_clinic_day", "clinic_id", "created_at"),
        Index("ix_patient_financial_entries_appointment", "appointment_id"),
        UniqueConstraint("clinic_id", "client_request_id", name="uq_financial_entry_client_request"),
        CheckConstraint(
            "kind IN ('payment', 'refund', 'adjustment_credit', 'adjustment_debit', 'reversal')",
            name="ck_financial_entry_kind"),
        CheckConstraint("amount > 0", name="ck_financial_entry_amount"),
        CheckConstraint("currency = 'RON'", name="ck_financial_entry_currency"),
        CheckConstraint(
            "(kind IN ('payment', 'refund') AND method IS NOT NULL AND method IN ('cash', 'card', 'transfer', 'other')) "
            "OR (kind NOT IN ('payment', 'refund') AND method IS NULL)",
            name="ck_financial_entry_method"),
        CheckConstraint(
            "kind = 'payment' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_financial_entry_reason"),
        CheckConstraint(
            "(kind = 'reversal') = (reverses_entry_id IS NOT NULL)",
            name="ck_financial_entry_reversal_target"),
    )


# A movement can be reversed at most once (partial unique index on the reversed entry).
Index(
    "uq_financial_entry_reversal_once",
    PatientFinancialEntry.reverses_entry_id,
    unique=True,
    postgresql_where=PatientFinancialEntry.reverses_entry_id.isnot(None),
    sqlite_where=PatientFinancialEntry.reverses_entry_id.isnot(None),
)
