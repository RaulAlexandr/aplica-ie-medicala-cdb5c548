"""Versioned procedure material recipes, reservations and actual usage."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "t18_procedure_materials"
down_revision = "t17_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_recipe_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("procedure_catalog_id", UUID, sa.ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(36)),
        sa.Column("request_signature", sa.String(64)),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "procedure_catalog_id", "version", name="uq_material_recipe_version"),
        sa.UniqueConstraint("clinic_id", "client_request_id", name="uq_material_recipe_request"),
        sa.CheckConstraint("version > 0", name="ck_material_recipe_version_positive"),
    )
    op.create_index("ix_material_recipe_procedure", "material_recipe_versions", ["clinic_id", "procedure_catalog_id", "version"])
    op.create_table(
        "material_recipe_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipe_version_id", UUID, sa.ForeignKey("material_recipe_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("item_id", UUID, sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("estimated_quantity", sa.Numeric(14, 3), nullable=False),
        sa.UniqueConstraint("recipe_version_id", "item_id", name="uq_material_recipe_item"),
        sa.CheckConstraint("estimated_quantity > 0", name="ck_material_recipe_quantity_positive"),
    )
    op.create_index("ix_material_recipe_lines_version", "material_recipe_lines", ["recipe_version_id"])
    op.create_table(
        "material_reservation_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("item_id", UUID, sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3)),
        sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "client_request_id", name="uq_material_reservation_request"),
        sa.CheckConstraint("action IN ('reserve','release')", name="ck_material_reservation_action"),
        sa.CheckConstraint("(action = 'reserve' AND quantity > 0) OR (action = 'release' AND quantity IS NULL)", name="ck_material_reservation_quantity"),
    )
    op.create_index("ix_material_reservation_current", "material_reservation_events", ["clinic_id", "appointment_id", "item_id", "occurred_at"])
    op.create_table(
        "material_consumption_batches",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("performed_procedure_id", UUID, sa.ForeignKey("performed_procedures.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipe_version_id", UUID, sa.ForeignKey("material_recipe_versions.id", ondelete="RESTRICT")),
        sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "client_request_id", name="uq_material_consumption_request"),
    )
    op.create_index("ix_material_consumption_procedure", "material_consumption_batches", ["clinic_id", "performed_procedure_id", "created_at"])
    op.create_table(
        "material_consumption_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_id", UUID, sa.ForeignKey("material_consumption_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inventory_movement_id", UUID, sa.ForeignKey("inventory_movements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recipe_line_id", UUID, sa.ForeignKey("material_recipe_lines.id", ondelete="RESTRICT")),
        sa.Column("recipe_version_id", UUID, sa.ForeignKey("material_recipe_versions.id", ondelete="RESTRICT")),
        sa.Column("item_id", UUID, sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lot_id", UUID, sa.ForeignKey("inventory_lots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("location_id", UUID, sa.ForeignKey("inventory_locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("estimated_quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("actual_quantity", sa.Numeric(14, 3), nullable=False),
        sa.UniqueConstraint("inventory_movement_id", name="uq_material_consumption_movement"),
        sa.CheckConstraint("estimated_quantity >= 0", name="ck_material_consumption_estimated_nonnegative"),
        sa.CheckConstraint("actual_quantity > 0", name="ck_material_consumption_actual_positive"),
    )
    op.create_index("ix_material_consumption_lines_batch", "material_consumption_lines", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_material_consumption_lines_batch", table_name="material_consumption_lines")
    op.drop_table("material_consumption_lines")
    op.drop_index("ix_material_consumption_procedure", table_name="material_consumption_batches")
    op.drop_table("material_consumption_batches")
    op.drop_index("ix_material_reservation_current", table_name="material_reservation_events")
    op.drop_table("material_reservation_events")
    op.drop_index("ix_material_recipe_lines_version", table_name="material_recipe_lines")
    op.drop_table("material_recipe_lines")
    op.drop_index("ix_material_recipe_procedure", table_name="material_recipe_versions")
    op.drop_table("material_recipe_versions")
