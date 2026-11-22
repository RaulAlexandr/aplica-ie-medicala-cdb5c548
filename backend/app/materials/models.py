from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class MaterialRecipeVersion(Base):
    __tablename__ = "material_recipe_versions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    procedure_catalog_id: Mapped[UUID] = mapped_column(ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    client_request_id: Mapped[str | None] = mapped_column(String(36))
    request_signature: Mapped[str | None] = mapped_column(String(64))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "procedure_catalog_id", "version", name="uq_material_recipe_version"),
        UniqueConstraint("clinic_id", "client_request_id", name="uq_material_recipe_request"),
        CheckConstraint("version > 0", name="ck_material_recipe_version_positive"),
        Index("ix_material_recipe_procedure", "clinic_id", "procedure_catalog_id", "version"),
    )


class MaterialRecipeLine(Base):
    __tablename__ = "material_recipe_lines"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    recipe_version_id: Mapped[UUID] = mapped_column(ForeignKey("material_recipe_versions.id", ondelete="RESTRICT"), nullable=False)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    estimated_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    __table_args__ = (
        UniqueConstraint("recipe_version_id", "item_id", name="uq_material_recipe_item"),
        CheckConstraint("estimated_quantity > 0", name="ck_material_recipe_quantity_positive"),
        Index("ix_material_recipe_lines_version", "recipe_version_id"),
    )


class MaterialReservationEvent(Base):
    __tablename__ = "material_reservation_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_request_id", name="uq_material_reservation_request"),
        CheckConstraint("action IN ('reserve','release')", name="ck_material_reservation_action"),
        CheckConstraint("(action = 'reserve' AND quantity > 0) OR (action = 'release' AND quantity IS NULL)", name="ck_material_reservation_quantity"),
        Index("ix_material_reservation_current", "clinic_id", "appointment_id", "item_id", "occurred_at"),
    )


class MaterialConsumptionBatch(Base):
    __tablename__ = "material_consumption_batches"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    performed_procedure_id: Mapped[UUID] = mapped_column(ForeignKey("performed_procedures.id", ondelete="RESTRICT"), nullable=False)
    recipe_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("material_recipe_versions.id", ondelete="RESTRICT"))
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_request_id", name="uq_material_consumption_request"),
        Index("ix_material_consumption_procedure", "clinic_id", "performed_procedure_id", "created_at"),
    )


class MaterialConsumptionLine(Base):
    __tablename__ = "material_consumption_lines"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("material_consumption_batches.id", ondelete="RESTRICT"), nullable=False)
    inventory_movement_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_movements.id", ondelete="RESTRICT"), nullable=False)
    recipe_line_id: Mapped[UUID | None] = mapped_column(ForeignKey("material_recipe_lines.id", ondelete="RESTRICT"))
    recipe_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("material_recipe_versions.id", ondelete="RESTRICT"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_locations.id", ondelete="RESTRICT"), nullable=False)
    estimated_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    actual_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    __table_args__ = (
        UniqueConstraint("inventory_movement_id", name="uq_material_consumption_movement"),
        CheckConstraint("estimated_quantity >= 0", name="ck_material_consumption_estimated_nonnegative"),
        CheckConstraint("actual_quantity > 0", name="ck_material_consumption_actual_positive"),
        Index("ix_material_consumption_lines_batch", "batch_id"),
    )
