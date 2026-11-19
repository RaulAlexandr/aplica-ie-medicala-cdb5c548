"""T08 treatment plans: stages, planned items, versioned acceptances and link history.

Additive only: no existing T00-T07 table or row is changed. Existing ``treatment_plan``
clinical notes are intentionally not migrated into the structured module.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t08_add_treatment_plans"
down_revision = "t06_add_persisted_odontogram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    ts = sa.DateTime(timezone=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "treatment_plans",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("accepted_version", sa.Integer, nullable=True),
        sa.Column("created_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=True),
        sa.Column("completed_at", ts, nullable=True),
        sa.Column("completed_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("cancelled_at", ts, nullable=True),
        sa.Column("cancelled_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("cancel_reason", sa.Text, nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
        sa.UniqueConstraint("clinic_id", "created_by_id", "idempotency_key", name="uq_treatment_plan_retry"),
        sa.CheckConstraint(
            "status IN ('draft', 'accepted', 'in_progress', 'completed', 'cancelled')",
            name="ck_treatment_plan_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_treatment_plan_version"),
    )
    op.create_index("ix_treatment_plans_clinic_patient", "treatment_plans", ["clinic_id", "patient_id", "created_at"])

    op.create_table(
        "treatment_plan_stages",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", uuid, sa.ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
    )
    op.create_index("ix_treatment_plan_stages_plan", "treatment_plan_stages", ["plan_id", "position"])

    op.create_table(
        "treatment_plan_items",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", uuid, sa.ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_id", uuid, sa.ForeignKey("treatment_plan_stages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("procedure_catalog_id", uuid, sa.ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("procedure_code", sa.String(40), nullable=False),
        sa.Column("procedure_name", sa.String(160), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("estimated_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("estimated_duration_minutes", sa.Integer, nullable=False),
        sa.Column("tooth_fdi", sa.String(3), nullable=True),
        sa.Column("tooth_surface", sa.String(100), nullable=True),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("cancel_reason", sa.Text, nullable=True),
        sa.Column("cancelled_at", ts, nullable=True),
        sa.Column("cancelled_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("appointment_linked_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("appointment_linked_at", ts, nullable=True),
        sa.Column("performed_procedure_id", uuid, sa.ForeignKey("performed_procedures.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("performed_linked_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("performed_linked_at", ts, nullable=True),
        sa.Column("idempotency_key", sa.String(36), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
        # One performed procedure can fulfil at most one planned item, across all plans.
        sa.UniqueConstraint("performed_procedure_id", name="uq_treatment_plan_item_performed"),
        sa.UniqueConstraint("plan_id", "idempotency_key", name="uq_treatment_plan_item_retry"),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name="ck_treatment_plan_item_status"),
        sa.CheckConstraint("priority IN ('urgent', 'high', 'normal', 'low')", name="ck_treatment_plan_item_priority"),
        sa.CheckConstraint("estimated_price >= 0", name="ck_treatment_plan_item_price"),
        sa.CheckConstraint("estimated_duration_minutes > 0", name="ck_treatment_plan_item_duration"),
    )
    op.create_index("ix_treatment_plan_items_plan", "treatment_plan_items", ["plan_id", "stage_id", "position"])

    op.create_table(
        "treatment_plan_acceptances",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", uuid, sa.ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("accepted_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("accepted_at", ts, nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("snapshot", jsonb, nullable=False),
        sa.UniqueConstraint("plan_id", "version", name="uq_treatment_plan_acceptance_version"),
    )

    op.create_table(
        "treatment_plan_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", uuid, sa.ForeignKey("treatment_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", uuid, nullable=True),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("plan_version", sa.Integer, nullable=False),
        sa.Column("details", jsonb, nullable=False),
        sa.Column("occurred_at", ts, nullable=False),
    )
    op.create_index("ix_treatment_plan_events_plan", "treatment_plan_events", ["plan_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("treatment_plan_events")
    op.drop_table("treatment_plan_acceptances")
    op.drop_table("treatment_plan_items")
    op.drop_table("treatment_plan_stages")
    op.drop_table("treatment_plans")
