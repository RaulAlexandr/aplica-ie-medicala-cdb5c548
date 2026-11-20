"""T03 catalog allowed locations: procedure_catalog.allowed_location.

Additive only. Existing services receive 'any' (tooth optional, surfaces optional), which keeps the
current behaviour of T04/T07/T08 unchanged. Stored performed procedures and plan items are not touched.
"""

import sqlalchemy as sa

from alembic import op

revision = "t03_catalog_allowed_locations"
down_revision = "t13_patient_financial_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "procedure_catalog",
        sa.Column("allowed_location", sa.String(20), nullable=False, server_default="any"),
    )
    op.create_check_constraint(
        "ck_procedure_catalog_allowed_location",
        "procedure_catalog",
        "allowed_location IN ('any', 'no_tooth', 'tooth', 'tooth_surface')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_procedure_catalog_allowed_location", "procedure_catalog", type_="check")
    op.drop_column("procedure_catalog", "allowed_location")
