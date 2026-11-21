"""T17 inventory ledger."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "t17_inventory"
down_revision = "t16_waitlist_recalls"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("inventory_items", sa.Column("id",UUID,primary_key=True), sa.Column("clinic_id",UUID,sa.ForeignKey("clinics.id",ondelete="RESTRICT"),nullable=False), sa.Column("name",sa.String(160),nullable=False), sa.Column("unit",sa.String(32),nullable=False), sa.Column("minimum_quantity",sa.Numeric(14,3),nullable=False), sa.Column("is_active",sa.Boolean(),nullable=False), sa.UniqueConstraint("clinic_id","name",name="uq_inventory_item_name"), sa.CheckConstraint("minimum_quantity >= 0",name="ck_inventory_item_minimum"))
    op.create_table("inventory_locations", sa.Column("id",UUID,primary_key=True), sa.Column("clinic_id",UUID,sa.ForeignKey("clinics.id",ondelete="RESTRICT"),nullable=False), sa.Column("name",sa.String(120),nullable=False), sa.Column("is_active",sa.Boolean(),nullable=False), sa.UniqueConstraint("clinic_id","name",name="uq_inventory_location_name"))
    op.create_table("inventory_lots", sa.Column("id",UUID,primary_key=True), sa.Column("clinic_id",UUID,sa.ForeignKey("clinics.id",ondelete="RESTRICT"),nullable=False), sa.Column("item_id",UUID,sa.ForeignKey("inventory_items.id",ondelete="RESTRICT"),nullable=False), sa.Column("code",sa.String(120),nullable=False), sa.Column("expires_on",sa.Date()), sa.UniqueConstraint("clinic_id","item_id","code",name="uq_inventory_lot_code"))
    op.create_table("inventory_movements", sa.Column("id",UUID,primary_key=True), sa.Column("clinic_id",UUID,sa.ForeignKey("clinics.id",ondelete="RESTRICT"),nullable=False), sa.Column("item_id",UUID,sa.ForeignKey("inventory_items.id",ondelete="RESTRICT"),nullable=False), sa.Column("lot_id",UUID,sa.ForeignKey("inventory_lots.id",ondelete="RESTRICT"),nullable=False), sa.Column("location_id",UUID,sa.ForeignKey("inventory_locations.id",ondelete="RESTRICT"),nullable=False), sa.Column("kind",sa.String(20),nullable=False), sa.Column("quantity",sa.Numeric(14,3),nullable=False), sa.Column("client_request_id",sa.String(36),nullable=False), sa.Column("signature",sa.String(64),nullable=False), sa.Column("transfer_id",UUID), sa.Column("reverses_movement_id",UUID,sa.ForeignKey("inventory_movements.id",ondelete="RESTRICT")), sa.Column("reason",sa.Text(),nullable=False), sa.Column("created_by_id",UUID,sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.UniqueConstraint("clinic_id","client_request_id",name="uq_inventory_movement_request"), sa.UniqueConstraint("reverses_movement_id",name="uq_inventory_movement_reversal"), sa.CheckConstraint("quantity > 0",name="ck_inventory_movement_quantity"), sa.CheckConstraint("kind IN ('receipt','consumption','transfer_in','transfer_out','adjustment_in','adjustment_out','reversal_in','reversal_out')",name="ck_inventory_movement_kind"))
    op.create_index("ix_inventory_movements_stock", "inventory_movements", ["clinic_id","lot_id","location_id"])

def downgrade() -> None:
    op.drop_index("ix_inventory_movements_stock",table_name="inventory_movements")
    op.drop_table("inventory_movements"); op.drop_table("inventory_lots"); op.drop_table("inventory_locations"); op.drop_table("inventory_items")
