from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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


class Base(DeclarativeBase):
    pass


class Clinic(Base):
    __tablename__ = "clinics"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Bucharest")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
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
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    clinic: Mapped[Clinic] = relationship(back_populates="users")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StaffInvitation(Base):
    __tablename__ = "staff_invitations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    invited_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assistant_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    appointment_type: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (Index("ix_appointments_clinic_starts", "clinic_id", "starts_at"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
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
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    note: Mapped["ClinicalNote"] = relationship(back_populates="revisions")


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
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source: Mapped[str | None] = mapped_column(String(50))
    metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        Index("ix_timeline_patient", "patient_id"),
        Index("ix_timeline_clinic", "clinic_id"),
        Index("ix_timeline_occurred", "occurred_at"),
        Index("ix_timeline_type", "clinic_id", "event_type"),
    )


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
