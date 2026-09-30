"""clinic setup and staff onboarding

Revision ID: 0003_clinic_setup_staff_onboarding
Revises: 0002_integrity_and_history
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_clinic_setup_staff_onboarding"
down_revision = "0002_integrity_and_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)
    op.add_column("clinics", sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Bucharest"))
    op.add_column("clinics", sa.Column("updated_at", now, nullable=True))
    op.execute("UPDATE clinics SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("clinics", "updated_at", nullable=False, server_default=None)

    op.add_column("users", sa.Column("specialization", sa.String(160), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(40), nullable=True))
    op.add_column("users", sa.Column("deactivated_at", now, nullable=True))
    op.add_column("users", sa.Column("created_at", now, nullable=True))
    op.add_column("users", sa.Column("updated_at", now, nullable=True))
    op.execute("UPDATE users SET created_at = (SELECT created_at FROM clinics WHERE clinics.id = users.clinic_id), updated_at = created_at")
    op.alter_column("users", "created_at", nullable=False, server_default=None)
    op.alter_column("users", "updated_at", nullable=False, server_default=None)

    op.create_table(
        "staff_invitations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("invited_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", now, nullable=False),
        sa.Column("revoked_at", now, nullable=True),
        sa.Column("accepted_at", now, nullable=True),
        sa.Column("created_at", now, nullable=False),
        sa.CheckConstraint("role IN ('doctor', 'assistant', 'reception')", name="ck_staff_invitation_role"),
    )
    op.create_index("ix_staff_invitations_clinic_id", "staff_invitations", ["clinic_id"])
    op.create_index("ix_staff_invitations_email", "staff_invitations", ["email"])


def downgrade() -> None:
    op.drop_index("ix_staff_invitations_email", table_name="staff_invitations")
    op.drop_index("ix_staff_invitations_clinic_id", table_name="staff_invitations")
    op.drop_table("staff_invitations")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "created_at")
    op.drop_column("users", "deactivated_at")
    op.drop_column("users", "phone")
    op.drop_column("users", "specialization")
    op.drop_column("clinics", "updated_at")
    op.drop_column("clinics", "timezone")
