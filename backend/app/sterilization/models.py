from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


def _clinic():
    return mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)


class SterilizationSetType(Base):
    __tablename__ = "sterilization_set_types"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    expiry_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("clinic_id", "name_key", name="uq_sterilization_set_type_name"),
        CheckConstraint("expiry_days BETWEEN 1 AND 365", name="ck_sterilization_set_type_expiry"),
    )


class SterilizationPack(Base):
    __tablename__ = "sterilization_packs"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    set_type_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_set_types.id", ondelete="RESTRICT"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    code_key: Mapped[str] = mapped_column(String(60), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("clinic_id", "code_key", name="uq_sterilization_pack_code"),)


class SterilizationCycle(Base):
    __tablename__ = "sterilization_cycles"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    equipment_id: Mapped[UUID] = mapped_column(ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False, index=True)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    program: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    operator_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    pressure_kpa: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    hold_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    decided_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("equipment_id", "cycle_number", name="uq_sterilization_cycle_number"),
        CheckConstraint("status IN ('in_progress','finished','approved','rejected')", name="ck_sterilization_cycle_status"),
        CheckConstraint("cycle_number > 0", name="ck_sterilization_cycle_number_positive"),
        CheckConstraint("(decided_by_id IS NULL) = (decided_at IS NULL)", name="ck_sterilization_cycle_decision"),
        Index("ix_sterilization_cycles_clinic_started", "clinic_id", "started_at"),
    )


class SterilizationCyclePack(Base):
    """Which packs entered which cycle (fixed before the cycle starts)."""

    __tablename__ = "sterilization_cycle_packs"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    cycle_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False, index=True)
    pack_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_packs.id", ondelete="RESTRICT"), nullable=False, index=True)
    __table_args__ = (UniqueConstraint("cycle_id", "pack_id", name="uq_sterilization_cycle_pack"),)


class SterilizationIndicatorResult(Base):
    """Append-only indicator results; the latest per kind is the current one."""

    __tablename__ = "sterilization_indicator_results"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    cycle_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("kind IN ('chemical','biological')", name="ck_sterilization_indicator_kind"),
        CheckConstraint("result IN ('pending','pass','fail')", name="ck_sterilization_indicator_result"),
    )


class SterilizationRecall(Base):
    """One compromise record per cycle (a new row, the cycle itself is not rewritten)."""

    __tablename__ = "sterilization_recalls"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    cycle_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("cycle_id", name="uq_sterilization_recall_cycle"),)


class SterilizationPackEvent(Base):
    """Append-only pack ledger; the pack state is derived from the latest event."""

    __tablename__ = "sterilization_pack_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = _clinic()
    pack_id: Mapped[UUID] = mapped_column(ForeignKey("sterilization_packs.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cycle_id: Mapped[UUID | None] = mapped_column(ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), index=True)
    appointment_id: Mapped[UUID | None] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), index=True)
    performed_procedure_id: Mapped[UUID | None] = mapped_column(ForeignKey("performed_procedures.id", ondelete="RESTRICT"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("event_type IN ('entered_cycle','sterilized','cycle_rejected','used','compromised')", name="ck_sterilization_event_type"),
        # A pack sterilized by a cycle can be linked to a visit only once, even under concurrency.
        Index("uq_sterilization_pack_use", "pack_id", "cycle_id", unique=True,
              postgresql_where=text("event_type = 'used'"), sqlite_where=text("event_type = 'used'")),
        Index("ix_sterilization_events_pack_created", "pack_id", "created_at"),
    )
