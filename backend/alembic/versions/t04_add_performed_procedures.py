"""T04: Add performed procedures, execution segments, and revisions

This migration adds support for tracking performed procedures during visits:
- performed_procedures: Records of procedures executed during appointments
- procedure_execution_segments: Time segments for tracking procedure execution (run/pause)
- performed_procedure_revisions: Audit trail for changes to performed procedures
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t04_add_performed_procedures"
down_revision = "c2195d701d56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)

    # Create performed_procedures table
    op.create_table(
        "performed_procedures",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("procedure_catalog_id", uuid, sa.ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("executed_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("procedure_code", sa.String(40), nullable=False),
        sa.Column("procedure_name", sa.String(160), nullable=False),
        sa.Column("procedure_base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("procedure_currency", sa.String(3), nullable=False, server_default="RON"),
        sa.Column("procedure_default_duration", sa.Integer, nullable=False),
        sa.Column("tooth_fdi", sa.String(3), nullable=True),
        sa.Column("tooth_surface", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("current_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", now, nullable=False),
        sa.Column("updated_at", now, nullable=False),
        sa.Index("ix_performed_procedures_appointment", "appointment_id"),
        sa.Index("ix_performed_procedures_patient", "patient_id"),
        sa.Index("ix_performed_procedures_executor", "executed_by_id"),
        sa.Index("ix_performed_procedures_status", "clinic_id", "status"),
    )

    # Create procedure_execution_segments table
    op.create_table(
        "procedure_execution_segments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("performed_procedure_id", uuid, sa.ForeignKey("performed_procedures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_type", sa.String(20), nullable=False),
        sa.Column("started_at", now, nullable=False),
        sa.Column("ended_at", now, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("created_at", now, nullable=False),
        sa.Index("ix_segments_procedure", "performed_procedure_id"),
        sa.Index("ix_segments_started", "started_at"),
    )

    # Create performed_procedure_revisions table
    op.create_table(
        "performed_procedure_revisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("performed_procedure_id", uuid, sa.ForeignKey("performed_procedures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("occurred_at", now, nullable=False),
        sa.Index("ix_performed_procedure_revisions_procedure", "performed_procedure_id"),
        sa.Index("ix_performed_procedure_revisions_occurred", "occurred_at"),
        sa.Index("ix_performed_procedure_revisions_clinic", "clinic_id"),
    )


def downgrade() -> None:
    op.drop_table("performed_procedure_revisions")
    op.drop_table("procedure_execution_segments")
    op.drop_table("performed_procedures")
