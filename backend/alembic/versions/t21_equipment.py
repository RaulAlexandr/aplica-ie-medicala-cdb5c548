"""T21 equipment registry, checks, dependencies and downtime."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t21_equipment"
down_revision = "t16_waitlist_recalls"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table("equipment",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("equipment_type", sa.String(100), nullable=False),
        sa.Column("serial_number", sa.String(120)), sa.Column("room_id", UUID, sa.ForeignKey("rooms.id", ondelete="RESTRICT")),
        sa.Column("responsible_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('available','maintenance','broken','retired')", name="ck_equipment_state"))
    for table, column in (("equipment", "clinic_id"), ("equipment", "room_id")):
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_equipment_clinic_active", "equipment", ["clinic_id", "is_active"])
    op.create_table("equipment_documents",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", UUID, sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False), sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("reference", sa.String(500), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("added_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("added_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("clinic_id", "equipment_id"): op.create_index(f"ix_equipment_documents_{col}", "equipment_documents", [col])
    op.create_table("equipment_checks",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", UUID, sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("check_type", sa.String(24), nullable=False), sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("result", sa.Text()),
        sa.Column("author_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("completed_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("check_type IN ('service','inspection')", name="ck_equipment_check_type"))
    for col in ("clinic_id", "equipment_id"): op.create_index(f"ix_equipment_checks_{col}", "equipment_checks", [col])
    op.create_table("equipment_unavailabilities",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", UUID, sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False), sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False), sa.Column("reported_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.CheckConstraint("ends_at > starts_at", name="ck_equipment_unavailability_interval"),
        sa.CheckConstraint("(closed_at IS NULL) = (closed_by_id IS NULL)", name="ck_equipment_unavailability_closure"))
    for col in ("clinic_id", "equipment_id"): op.create_index(f"ix_equipment_unavailabilities_{col}", "equipment_unavailabilities", [col])
    op.create_table("equipment_dependencies",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("equipment_id", UUID, sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("room_id", UUID, sa.ForeignKey("rooms.id", ondelete="RESTRICT")),
        sa.Column("procedure_id", UUID, sa.ForeignKey("procedure_catalog.id", ondelete="RESTRICT")),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("room_id IS NOT NULL OR procedure_id IS NOT NULL", name="ck_equipment_dependency_target"),
        sa.UniqueConstraint("clinic_id", "equipment_id", "room_id", "procedure_id", name="uq_equipment_dependency"))
    for col in ("clinic_id", "equipment_id"): op.create_index(f"ix_equipment_dependencies_{col}", "equipment_dependencies", [col])


def downgrade() -> None:
    for table in ("equipment_dependencies", "equipment_unavailabilities", "equipment_checks", "equipment_documents", "equipment"):
        op.drop_table(table)
