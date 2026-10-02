"""add_procedure_catalog"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'c2195d701d56'
down_revision = '0003_clinic_setup'
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)
    op.create_table(
        "procedure_catalog",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("code_lower", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("default_duration_minutes", sa.Integer, nullable=False),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RON"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", now, nullable=False),
        sa.Column("updated_at", now, nullable=False),
        sa.UniqueConstraint("clinic_id", "code_lower", name="uq_procedure_catalog_clinic_code"),
        sa.Index("ix_procedure_catalog_clinic_active", "clinic_id", "is_active"),
        sa.Index("ix_procedure_catalog_clinic_name", "clinic_id", "name"),
    )
    op.execute("UPDATE procedure_catalog SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    op.drop_table("procedure_catalog")
