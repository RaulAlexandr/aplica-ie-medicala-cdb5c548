from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(100), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(120))
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    responsible_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="available")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint("state IN ('available','maintenance','broken','retired')", name="ck_equipment_state"), Index("ix_equipment_clinic_active", "clinic_id", "is_active"))


class EquipmentDocument(Base):
    __tablename__ = "equipment_documents"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    equipment_id: Mapped[UUID] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class EquipmentCheck(Base):
    __tablename__ = "equipment_checks"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    equipment_id: Mapped[UUID] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False, index=True)
    check_type: Mapped[str] = mapped_column(String(24), nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    completed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("check_type IN ('service','inspection')", name="ck_equipment_check_type"),)


class EquipmentUnavailability(Base):
    __tablename__ = "equipment_unavailabilities"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    equipment_id: Mapped[UUID] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reported_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ck_equipment_unavailability_interval"), CheckConstraint("(closed_at IS NULL) = (closed_by_id IS NULL)", name="ck_equipment_unavailability_closure"))


class EquipmentDependency(Base):
    __tablename__ = "equipment_dependencies"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    equipment_id: Mapped[UUID] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False, index=True)
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    procedure_id: Mapped[UUID | None] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("room_id IS NOT NULL OR procedure_id IS NOT NULL", name="ck_equipment_dependency_target"), UniqueConstraint("clinic_id", "equipment_id", "room_id", "procedure_id", name="uq_equipment_dependency"))
