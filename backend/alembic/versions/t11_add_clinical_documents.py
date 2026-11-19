"""T11 clinical documents and images: protected file metadata linked to patient/visit/tooth.

Additive only, from the T08 head of the fixed base. No existing table or row is changed. Binary
content is stored outside the database (DOCUMENT_STORAGE_DIR); this table holds metadata only.
T09 (periodontal) also starts from an earlier head on its own branch; combining T09 and T11 needs
an explicit merge revision later, not an edit of this file.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t11_add_clinical_documents"
down_revision = "t08_add_treatment_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    ts = sa.DateTime(timezone=True)
    op.create_table(
        "clinical_documents",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("tooth_fdi", sa.String(3), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("storage_key", sa.String(64), nullable=False),
        sa.Column("clinical_date", sa.Date, nullable=True),
        sa.Column("uploaded_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("uploaded_at", ts, nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("archived_at", ts, nullable=True),
        sa.Column("archived_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("archive_reason", sa.Text, nullable=True),
        sa.UniqueConstraint("storage_key", name="uq_clinical_document_storage_key"),
        sa.UniqueConstraint("clinic_id", "uploaded_by_id", "idempotency_key", name="uq_clinical_document_retry"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_clinical_document_status"),
        sa.CheckConstraint("category IN ('photo', 'radiograph', 'document', 'other')", name="ck_clinical_document_category"),
        sa.CheckConstraint("content_type IN ('image/jpeg', 'image/png', 'application/pdf')", name="ck_clinical_document_type"),
        sa.CheckConstraint("size_bytes > 0", name="ck_clinical_document_size"),
        sa.CheckConstraint("tooth_fdi IS NULL OR tooth_fdi ~ '^[1-4][1-8]$'", name="ck_clinical_document_tooth"),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL AND archived_by_id IS NOT NULL AND archive_reason IS NOT NULL)",
            name="ck_clinical_document_archive",
        ),
    )
    op.create_index("ix_clinical_documents_patient", "clinical_documents", ["clinic_id", "patient_id", "uploaded_at"])
    op.create_index("ix_clinical_documents_appointment", "clinical_documents", ["appointment_id"])


def downgrade() -> None:
    op.drop_index("ix_clinical_documents_appointment", table_name="clinical_documents")
    op.drop_index("ix_clinical_documents_patient", table_name="clinical_documents")
    op.drop_table("clinical_documents")
