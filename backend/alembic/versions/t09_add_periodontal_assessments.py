"""Add immutable periodontal assessments and six-site measurements."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t09_add_periodontal_assessments"
down_revision = "t06_add_persisted_odontogram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "periodontal_assessments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("clinical_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("previous_assessment_id", uuid, sa.ForeignKey("periodontal_assessments.id", ondelete="RESTRICT")),
        sa.Column("relationship_kind", sa.String(16)),
        sa.Column("change_reason", sa.Text()),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("finalized_at", timestamp),
    )
    op.create_index("ix_periodontal_assessments_clinic_id", "periodontal_assessments", ["clinic_id"])
    op.create_index("ix_periodontal_assessments_patient_id", "periodontal_assessments", ["patient_id"])
    op.create_index("ix_periodontal_assessments_author_id", "periodontal_assessments", ["author_id"])
    op.create_index("ix_periodontal_patient_date", "periodontal_assessments", ["clinic_id", "patient_id", "clinical_date"])
    op.create_table(
        "periodontal_assessment_teeth",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("assessment_id", uuid, sa.ForeignKey("periodontal_assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tooth_fdi", sa.String(2), nullable=False),
        sa.Column("is_excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mobility", sa.String(8)),
        sa.Column("furcation", sa.String(8)),
        sa.UniqueConstraint("assessment_id", "tooth_fdi", name="uq_periodontal_assessment_tooth"),
    )
    op.create_index("ix_periodontal_assessment_teeth_assessment_id", "periodontal_assessment_teeth", ["assessment_id"])
    op.create_table(
        "periodontal_site_measurements",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tooth_id", uuid, sa.ForeignKey("periodontal_assessment_teeth.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site", sa.String(24), nullable=False),
        sa.Column("probing_depth_mm", sa.Numeric(7, 2)),
        sa.Column("recession_mm", sa.Numeric(7, 2)),
        sa.Column("bop", sa.Boolean()),
        sa.Column("plaque", sa.Boolean()),
        sa.Column("suppuration", sa.Boolean()),
        sa.UniqueConstraint("tooth_id", "site", name="uq_periodontal_tooth_site"),
    )
    op.create_index("ix_periodontal_site_measurements_tooth_id", "periodontal_site_measurements", ["tooth_id"])


def downgrade() -> None:
    op.drop_table("periodontal_site_measurements")
    op.drop_table("periodontal_assessment_teeth")
    op.drop_table("periodontal_assessments")
