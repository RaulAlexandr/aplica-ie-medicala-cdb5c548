from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    minimum_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_inventory_item_name"), CheckConstraint("minimum_quantity >= 0", name="ck_inventory_item_minimum"))


class InventoryLocation(Base):
    __tablename__ = "inventory_locations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_inventory_location_name"),)


class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(120), nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("clinic_id", "item_id", "code", name="uq_inventory_lot_code"),)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_locations.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    transfer_id: Mapped[UUID | None] = mapped_column(UUIDType)
    reverses_movement_id: Mapped[UUID | None] = mapped_column(ForeignKey("inventory_movements.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("clinic_id", "client_request_id", name="uq_inventory_movement_request"),
        UniqueConstraint("reverses_movement_id", name="uq_inventory_movement_reversal"),
        CheckConstraint("quantity > 0", name="ck_inventory_movement_quantity"),
        CheckConstraint("kind IN ('receipt','consumption','transfer_in','transfer_out','adjustment_in','adjustment_out','reversal_in','reversal_out')", name="ck_inventory_movement_kind"),
        Index("ix_inventory_movements_stock", "clinic_id", "lot_id", "location_id"),
    )
