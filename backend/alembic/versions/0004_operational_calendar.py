"""operational calendar availability and appointment history

Revision ID: 0004_operational_calendar
Revises: 0003_clinic_setup
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_operational_calendar"
down_revision = "0003_clinic_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)
    op.create_table(
        "working_hours",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_working_hours_day"),
        sa.CheckConstraint("end_time > start_time", name="ck_working_hours_interval"),
    )
    op.create_index("ix_working_hours_clinic_id", "working_hours", ["clinic_id"])
    op.create_index("ix_working_hours_clinic_day", "working_hours", ["clinic_id", "day_of_week"])
    op.create_table(
        "resource_unavailability",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("resource_id", uuid, nullable=False),
        sa.Column("starts_at", now, nullable=False),
        sa.Column("ends_at", now, nullable=False),
        sa.Column("reason", sa.String(240)),
        sa.Column("created_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", now, nullable=False),
        sa.CheckConstraint("resource_type IN ('doctor', 'assistant', 'room')", name="ck_unavailability_resource_type"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_unavailability_interval"),
    )
    op.create_index("ix_resource_unavailability_clinic_id", "resource_unavailability", ["clinic_id"])
    op.create_index("ix_resource_unavailability_resource_id", "resource_unavailability", ["resource_id"])
    op.create_table(
        "appointment_history",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", now, nullable=False),
    )
    op.create_index("ix_appointment_history_clinic_id", "appointment_history", ["clinic_id"])
    op.create_index("ix_appointment_history_appointment_id", "appointment_history", ["appointment_id"])
    op.create_index("ix_appointment_history_occurred_at", "appointment_history", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_appointment_history_occurred_at", table_name="appointment_history")
    op.drop_index("ix_appointment_history_appointment_id", table_name="appointment_history")
    op.drop_index("ix_appointment_history_clinic_id", table_name="appointment_history")
    op.drop_table("appointment_history")
    op.drop_index("ix_resource_unavailability_resource_id", table_name="resource_unavailability")
    op.drop_index("ix_resource_unavailability_clinic_id", table_name="resource_unavailability")
    op.drop_table("resource_unavailability")
    op.drop_index("ix_working_hours_clinic_day", table_name="working_hours")
    op.drop_index("ix_working_hours_clinic_id", table_name="working_hours")
    op.drop_table("working_hours")
