"""T22 Cabinet Ready. Confirmations, blocks and decisions are append-only records."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow

ITEM_KINDS = ("material", "instrument", "document", "action")
_KIND_CHECK = "kind IN ('material','instrument','document','action')"


class CabinetChecklistVersion(Base):
    __tablename__ = "cabinet_checklist_versions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    procedure_catalog_id: Mapped[UUID] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("clinic_id", "procedure_catalog_id", "version", name="uq_cabinet_checklist_version"),
        CheckConstraint("version >= 1", name="ck_cabinet_checklist_version"),
    )


class CabinetChecklistItem(Base):
    __tablename__ = "cabinet_checklist_items"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("cabinet_checklist_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prep_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("version_id", "position", name="uq_cabinet_checklist_item_position"),
        CheckConstraint(_KIND_CHECK, name="ck_cabinet_checklist_item_kind"),
        CheckConstraint("prep_minutes BETWEEN 0 AND 240", name="ck_cabinet_checklist_item_minutes"),
    )


class CabinetPreparation(Base):
    """One started preparation of an appointment for one context (room, time, procedures)."""

    __tablename__ = "cabinet_preparations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    procedures_signature: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("appointment_id", "revision", name="uq_cabinet_preparation_revision"),)


class CabinetParticularity(Base):
    __tablename__ = "cabinet_particularities"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    item_kind: Mapped[str | None] = mapped_column(String(16))
    item_label: Mapped[str | None] = mapped_column(String(200))
    item_required: Mapped[bool | None] = mapped_column(Boolean)
    item_minutes: Mapped[int | None] = mapped_column(Integer)
    proposed_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("item_kind IS NULL OR item_kind IN ('material','instrument','document','action')", name="ck_cabinet_particularity_kind"),
        CheckConstraint("(item_kind IS NULL) = (item_label IS NULL) AND (item_kind IS NULL) = (item_required IS NULL) "
                        "AND (item_kind IS NULL) = (item_minutes IS NULL)", name="ck_cabinet_particularity_item"),
        CheckConstraint("item_minutes IS NULL OR item_minutes BETWEEN 0 AND 240", name="ck_cabinet_particularity_minutes"),
    )


class CabinetParticularityDecision(Base):
    __tablename__ = "cabinet_particularity_decisions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    particularity_id: Mapped[UUID] = mapped_column(ForeignKey("cabinet_particularities.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    decided_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("particularity_id", name="uq_cabinet_particularity_decision"),
        CheckConstraint("decision IN ('approved','rejected')", name="ck_cabinet_particularity_decision"),
    )


class CabinetPreparationItem(Base):
    """Snapshot of a checklist line; later template changes never rewrite it."""

    __tablename__ = "cabinet_preparation_items"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    preparation_id: Mapped[UUID] = mapped_column(ForeignKey("cabinet_preparations.id", ondelete="RESTRICT"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    checklist_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("cabinet_checklist_versions.id", ondelete="RESTRICT"))
    particularity_id: Mapped[UUID | None] = mapped_column(ForeignKey("cabinet_particularities.id", ondelete="RESTRICT"))
    procedure_catalog_id: Mapped[UUID | None] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"))
    procedure_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prep_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("preparation_id", "position", name="uq_cabinet_preparation_item_position"),
        CheckConstraint(_KIND_CHECK, name="ck_cabinet_preparation_item_kind"),
        CheckConstraint("(checklist_version_id IS NULL) <> (particularity_id IS NULL)", name="ck_cabinet_preparation_item_source"),
    )


class CabinetConfirmationEvent(Base):
    __tablename__ = "cabinet_confirmation_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    preparation_item_id: Mapped[UUID] = mapped_column(ForeignKey("cabinet_preparation_items.id", ondelete="RESTRICT"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("preparation_item_id", "sequence", name="uq_cabinet_confirmation_sequence"),
        CheckConstraint("action IN ('confirm','revoke')", name="ck_cabinet_confirmation_action"),
        CheckConstraint("action = 'confirm' OR reason IS NOT NULL", name="ck_cabinet_confirmation_reason"),
    )


class CabinetManualBlock(Base):
    __tablename__ = "cabinet_manual_blocks"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    check_with: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class CabinetManualBlockClosure(Base):
    __tablename__ = "cabinet_manual_block_closures"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    block_id: Mapped[UUID] = mapped_column(ForeignKey("cabinet_manual_blocks.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    closed_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("block_id", name="uq_cabinet_manual_block_closure"),)


class CabinetRequestKey(Base):
    """Idempotency ledger for every Cabinet Ready write, committed with the write itself."""

    __tablename__ = "cabinet_request_keys"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_request_id", name="uq_cabinet_request_key"),
        Index("ix_cabinet_request_keys_created", "clinic_id", "created_at"),
    )
