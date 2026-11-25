"""T32 CSV imports, patient external identifiers and audit trail."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t32_import_export"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("patients", sa.Column("cnp", sa.String(13)))
    op.add_column("patients", sa.Column("external_id", sa.String(120)))
    op.create_index("ix_patients_clinic_external_id", "patients", ["clinic_id", "external_id"])
    op.create_index("ix_patients_clinic_cnp", "patients", ["clinic_id", "cnp"])
    op.create_table(
        "import_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "clinic_id", "kind", "file_hash", name="uq_import_run_clinic_kind_hash"
        ),
    )
    op.create_index("ix_import_runs_clinic_id", "import_runs", ["clinic_id"])
    op.create_table(
        "import_rows",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("run_id", UUID, sa.ForeignKey("import_runs.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("entity_type", sa.String(24)),
        sa.Column("entity_id", UUID),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "row_number", name="uq_import_row_run_number"),
    )
    op.create_index("ix_import_rows_clinic_id", "import_rows", ["clinic_id"])
    op.create_index("ix_import_rows_run_id", "import_rows", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_import_rows_run_id", table_name="import_rows")
    op.drop_index("ix_import_rows_clinic_id", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_runs_clinic_id", table_name="import_runs")
    op.drop_table("import_runs")
    op.drop_index("ix_patients_clinic_cnp", table_name="patients")
    op.drop_index("ix_patients_clinic_external_id", table_name="patients")
    op.drop_column("patients", "external_id")
    op.drop_column("patients", "cnp")
