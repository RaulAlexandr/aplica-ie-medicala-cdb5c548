"""T05: Add clinical notes, revisions, and patient timeline.

This migration adds support for:
- clinical_notes: Clinical notes and findings for patients
- clinical_note_revisions: Audit trail for note changes
- patient_timeline_events: Timeline of patient events across all modules
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t05_add_clinical_notes_timeline"
down_revision = "t04_add_performed_procedures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)

    # Create clinical_notes table
    op.create_table(
        "clinical_notes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=True),
        sa.Column("author_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("note_type", sa.String(30), nullable=False, server_default="note"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tooth_fdi", sa.String(3), nullable=True),
        sa.Column("tooth_surface", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("is_alert", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", now, nullable=False),
        sa.Column("updated_at", now, nullable=False),
        sa.Column("finalized_at", now, nullable=True),
        sa.Index("ix_clinical_notes_patient", "patient_id"),
        sa.Index("ix_clinical_notes_appointment", "appointment_id"),
        sa.Index("ix_clinical_notes_author", "author_id"),
        sa.Index("ix_clinical_notes_clinic_status", "clinic_id", "status"),
    )

    # Create clinical_note_revisions table
    op.create_table(
        "clinical_note_revisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("clinical_note_id", uuid, sa.ForeignKey("clinical_notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("occurred_at", now, nullable=False),
        sa.Index("ix_clinical_note_revisions_note", "clinical_note_id"),
        sa.Index("ix_clinical_note_revisions_occurred", "occurred_at"),
        sa.Index("ix_clinical_note_revisions_clinic", "clinic_id"),
    )

    # Create patient_timeline_events table
    op.create_table(
        "patient_timeline_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", uuid, nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("occurred_at", now, nullable=False),
        sa.Column("recorded_at", now, nullable=False),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Index("ix_timeline_patient", "patient_id"),
        sa.Index("ix_timeline_clinic", "clinic_id"),
        sa.Index("ix_timeline_occurred", "occurred_at"),
        sa.Index("ix_timeline_type", "clinic_id", "event_type"),
    )


def downgrade() -> None:
    op.drop_table("patient_timeline_events")
    op.drop_table("clinical_note_revisions")
    op.drop_table("clinical_notes")
