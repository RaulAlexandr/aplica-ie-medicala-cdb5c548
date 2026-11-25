"""T23 sterilization: set types, packs, cycles, indicator results, recalls and the append-only pack ledger."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t23_sterilization"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _clinic():
    return sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)


def _user(name, nullable=False):
    return sa.Column(name, UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


def upgrade() -> None:
    op.create_table("sterilization_set_types",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("name_key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("expiry_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False), _user("created_by_id"),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("clinic_id", "name_key", name="uq_sterilization_set_type_name"),
        sa.CheckConstraint("expiry_days BETWEEN 1 AND 365", name="ck_sterilization_set_type_expiry"))
    op.create_index("ix_sterilization_set_types_clinic_id", "sterilization_set_types", ["clinic_id"])
    op.create_table("sterilization_packs",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("set_type_id", UUID, sa.ForeignKey("sterilization_set_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(60), nullable=False), sa.Column("code_key", sa.String(60), nullable=False),
        _user("created_by_id"), sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("clinic_id", "code_key", name="uq_sterilization_pack_code"))
    op.create_index("ix_sterilization_packs_clinic_id", "sterilization_packs", ["clinic_id"])
    op.create_index("ix_sterilization_packs_set_type_id", "sterilization_packs", ["set_type_id"])
    op.create_table("sterilization_cycles",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("equipment_id", UUID, sa.ForeignKey("equipment.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False), sa.Column("program", sa.String(120), nullable=False),
        sa.Column("started_at", TS, nullable=False), sa.Column("ended_at", TS), _user("operator_id"),
        sa.Column("temperature_c", sa.Numeric(5, 1)), sa.Column("pressure_kpa", sa.Numeric(7, 1)), sa.Column("hold_minutes", sa.Integer()),
        sa.Column("status", sa.String(16), nullable=False), _user("decided_by_id", True),
        sa.Column("decided_at", TS), sa.Column("decision_note", sa.Text()), sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("equipment_id", "cycle_number", name="uq_sterilization_cycle_number"),
        sa.CheckConstraint("status IN ('in_progress','finished','approved','rejected')", name="ck_sterilization_cycle_status"),
        sa.CheckConstraint("cycle_number > 0", name="ck_sterilization_cycle_number_positive"),
        sa.CheckConstraint("(decided_by_id IS NULL) = (decided_at IS NULL)", name="ck_sterilization_cycle_decision"))
    op.create_index("ix_sterilization_cycles_clinic_id", "sterilization_cycles", ["clinic_id"])
    op.create_index("ix_sterilization_cycles_equipment_id", "sterilization_cycles", ["equipment_id"])
    op.create_index("ix_sterilization_cycles_clinic_started", "sterilization_cycles", ["clinic_id", "started_at"])
    op.create_table("sterilization_cycle_packs",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("cycle_id", UUID, sa.ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pack_id", UUID, sa.ForeignKey("sterilization_packs.id", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("cycle_id", "pack_id", name="uq_sterilization_cycle_pack"))
    for column in ("clinic_id", "cycle_id", "pack_id"):
        op.create_index(f"ix_sterilization_cycle_packs_{column}", "sterilization_cycle_packs", [column])
    op.create_table("sterilization_indicator_results",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("cycle_id", UUID, sa.ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False), sa.Column("result", sa.String(8), nullable=False),
        sa.Column("note", sa.Text()), _user("recorded_by_id"), sa.Column("recorded_at", TS, nullable=False),
        sa.CheckConstraint("kind IN ('chemical','biological')", name="ck_sterilization_indicator_kind"),
        sa.CheckConstraint("result IN ('pending','pass','fail')", name="ck_sterilization_indicator_result"))
    for column in ("clinic_id", "cycle_id"):
        op.create_index(f"ix_sterilization_indicator_results_{column}", "sterilization_indicator_results", [column])
    op.create_table("sterilization_recalls",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("cycle_id", UUID, sa.ForeignKey("sterilization_cycles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), _user("recorded_by_id"), sa.Column("recorded_at", TS, nullable=False),
        sa.UniqueConstraint("cycle_id", name="uq_sterilization_recall_cycle"))
    op.create_index("ix_sterilization_recalls_clinic_id", "sterilization_recalls", ["clinic_id"])
    op.create_table("sterilization_pack_events",
        sa.Column("id", UUID, primary_key=True), _clinic(),
        sa.Column("pack_id", UUID, sa.ForeignKey("sterilization_packs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("cycle_id", UUID, sa.ForeignKey("sterilization_cycles.id", ondelete="RESTRICT")),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT")),
        sa.Column("performed_procedure_id", UUID, sa.ForeignKey("performed_procedures.id", ondelete="RESTRICT")),
        sa.Column("expires_at", TS), sa.Column("reason", sa.Text()), _user("actor_id"), sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("event_type IN ('entered_cycle','sterilized','cycle_rejected','used','compromised')", name="ck_sterilization_event_type"))
    for column in ("clinic_id", "pack_id", "cycle_id", "appointment_id"):
        op.create_index(f"ix_sterilization_pack_events_{column}", "sterilization_pack_events", [column])
    op.create_index("ix_sterilization_events_pack_created", "sterilization_pack_events", ["pack_id", "created_at"])
    op.create_index("uq_sterilization_pack_use", "sterilization_pack_events", ["pack_id", "cycle_id"], unique=True,
                    postgresql_where=sa.text("event_type = 'used'"))


def downgrade() -> None:
    for table in ("sterilization_pack_events", "sterilization_recalls", "sterilization_indicator_results",
                  "sterilization_cycle_packs", "sterilization_cycles", "sterilization_packs", "sterilization_set_types"):
        op.drop_table(table)
