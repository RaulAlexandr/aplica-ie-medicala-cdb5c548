from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clinic: Mapped[Clinic] = relationship(back_populates="users")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clinic: Mapped[Clinic] = relationship(back_populates="rooms")
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_room_clinic_name"),)


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


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
