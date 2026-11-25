"""Add planned procedures directly to appointments."""
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1_appointment_procedures"
down_revision = "t16_waitlist_recalls"
branch_labels = None
depends_on = None


def _uuid_type():
    return postgresql.UUID(as_uuid=True) if op.get_bind().dialect.name == "postgresql" else sa.String(36)


def upgrade() -> None:
    uuid_type = _uuid_type()
    timestamp = sa.DateTime(timezone=True)
    op.create_table("appointment_procedures",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("clinic_id", uuid_type, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid_type, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("procedure_catalog_id", uuid_type, sa.ForeignKey("procedure_catalog.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("added_by_id", uuid_type, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("added_at", timestamp, nullable=False),
        sa.Column("removed_by_id", uuid_type, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("removed_at", timestamp),
        sa.CheckConstraint("quantity >= 1 AND quantity <= 32", name="ck_appointment_procedure_quantity"))
    op.create_index("ix_appointment_procedures_clinic_id", "appointment_procedures", ["clinic_id"])
    op.create_index("ix_appointment_procedures_appointment_id", "appointment_procedures", ["appointment_id"])
    op.create_index("uq_appointment_procedure_active", "appointment_procedures", ["appointment_id", "procedure_catalog_id"], unique=True,
                    postgresql_where=sa.text("removed_at IS NULL"), sqlite_where=sa.text("removed_at IS NULL"))
    op.create_table("appointment_procedure_revisions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("clinic_id", uuid_type, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid_type, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.UniqueConstraint("appointment_id", name="uq_appointment_procedure_revision_appointment"),
        sa.CheckConstraint("revision >= 0", name="ck_appointment_procedure_revision_nonnegative"))
    op.create_index("ix_appointment_procedure_revisions_clinic_id", "appointment_procedure_revisions", ["clinic_id"])
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, clinic_id FROM appointments")).all()
    if rows:
        bind.execute(sa.text("INSERT INTO appointment_procedure_revisions (id, clinic_id, appointment_id, revision, updated_at) VALUES (:id, :clinic_id, :appointment_id, 0, CURRENT_TIMESTAMP)"),
                     [{"id": str(uuid4()) if bind.dialect.name == "sqlite" else uuid4(), "clinic_id": str(row.clinic_id) if bind.dialect.name == "sqlite" else row.clinic_id,
                       "appointment_id": str(row.id) if bind.dialect.name == "sqlite" else row.id} for row in rows])


def downgrade() -> None:
    op.drop_index("ix_appointment_procedure_revisions_clinic_id", table_name="appointment_procedure_revisions")
    op.drop_table("appointment_procedure_revisions")
    op.drop_index("uq_appointment_procedure_active", table_name="appointment_procedures")
    op.drop_index("ix_appointment_procedures_appointment_id", table_name="appointment_procedures")
    op.drop_index("ix_appointment_procedures_clinic_id", table_name="appointment_procedures")
    op.drop_table("appointment_procedures")
