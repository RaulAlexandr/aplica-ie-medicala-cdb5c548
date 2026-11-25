"""T25 clinic automation rules, immutable versions, execution log and cursor."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t25_automations"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def fk(table):
    return sa.ForeignKey(f"{table}.id", ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, fk("clinics"), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False), sa.Column("created_by_id", UUID, fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False), sa.CheckConstraint("current_version > 0", name="ck_automation_rule_version"),
    )
    op.create_index("ix_automation_rules_clinic", "automation_rules", ["clinic_id", "created_at"])
    op.create_table(
        "automation_rule_versions",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, fk("clinics"), nullable=False),
        sa.Column("rule_id", UUID, fk("automation_rules"), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("conditions", JSON, nullable=False), sa.Column("action", JSON, nullable=False), sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("cursor_at", TS), sa.Column("start_at", TS),
        sa.Column("created_by_id", UUID, fk("users"), nullable=False), sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("rule_id", "version", name="uq_automation_rule_version"),
        sa.CheckConstraint("version > 0", name="ck_automation_version_number"),
        sa.CheckConstraint("mode IN ('automatic', 'approval')", name="ck_automation_version_mode"),
    )
    op.create_index("ix_automation_versions_active", "automation_rule_versions", ["clinic_id", "is_active", "trigger"])
    op.create_table(
        "automation_executions",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, fk("clinics"), nullable=False),
        sa.Column("rule_id", UUID, fk("automation_rules"), nullable=False), sa.Column("version_id", UUID, fk("automation_rule_versions"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False), sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("occurred_at", TS, nullable=False), sa.Column("event_data", JSON, nullable=False),
        sa.Column("conditions_result", JSON, nullable=False), sa.Column("action_snapshot", JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("result", JSON), sa.Column("decided_by_id", UUID, fk("users")),
        sa.Column("decided_at", TS), sa.Column("decision_reason", sa.String(500)),
        sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("version_id", "event_type", "event_id", name="uq_automation_execution_event"),
        sa.CheckConstraint("status IN ('pending_approval','running','succeeded','failed_retryable','failed','rejected','stopped')", name="ck_automation_execution_status"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_automation_execution_attempts"),
    )
    op.create_index("ix_automation_exec_clinic_status", "automation_executions", ["clinic_id", "status", "created_at"])
    op.create_index("ix_automation_exec_version_time", "automation_executions", ["version_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_automation_exec_version_time", table_name="automation_executions")
    op.drop_index("ix_automation_exec_clinic_status", table_name="automation_executions")
    op.drop_table("automation_executions")
    op.drop_index("ix_automation_versions_active", table_name="automation_rule_versions")
    op.drop_table("automation_rule_versions")
    op.drop_index("ix_automation_rules_clinic", table_name="automation_rules")
    op.drop_table("automation_rules")
