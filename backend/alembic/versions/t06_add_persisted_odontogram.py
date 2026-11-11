"""Persist tooth states, clinical findings and correction history for T06."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t06_add_persisted_odontogram"
down_revision = "merge_t02_t05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "odontogram_tooth_states",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tooth_fdi", sa.String(2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="unevaluated"),
        sa.Column("updated_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.UniqueConstraint("patient_id", "tooth_fdi", name="uq_odontogram_tooth_patient_fdi"),
    )
    op.create_index("ix_odontogram_tooth_clinic_patient", "odontogram_tooth_states", ["clinic_id", "patient_id"])
    op.create_table(
        "odontogram_tooth_revisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tooth_fdi", sa.String(2), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("current_status", sa.String(20), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
    )
    op.create_index("ix_odontogram_tooth_revisions_patient", "odontogram_tooth_revisions", ["clinic_id", "patient_id", "occurred_at"])
    op.create_table(
        "odontogram_findings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tooth_fdi", sa.String(2), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("surfaces", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("observed_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("author_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finalized_at", timestamp, nullable=True),
        sa.Column("idempotency_key", sa.String(36), nullable=True),
        sa.UniqueConstraint("clinic_id", "author_id", "idempotency_key", name="uq_odontogram_finding_retry"),
    )
    op.create_index("ix_odontogram_findings_clinic_patient", "odontogram_findings", ["clinic_id", "patient_id", "created_at"])
    op.create_table(
        "odontogram_finding_revisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finding_id", uuid, sa.ForeignKey("odontogram_findings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("previous_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
    )
    op.create_index("ix_odontogram_finding_revisions", "odontogram_finding_revisions", ["clinic_id", "finding_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("odontogram_finding_revisions")
    op.drop_table("odontogram_findings")
    op.drop_table("odontogram_tooth_revisions")
    op.drop_table("odontogram_tooth_states")
