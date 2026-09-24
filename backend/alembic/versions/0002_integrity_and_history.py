"""patient revisions and PostgreSQL scheduling guards

Revision ID: 0002_integrity_and_history
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_integrity_and_history"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)
    op.create_table(
        "patient_revisions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("occurred_at", now, nullable=False),
    )
    op.create_index("ix_patient_revisions_clinic_id", "patient_revisions", ["clinic_id"])
    op.create_index("ix_patient_revisions_patient_id", "patient_revisions", ["patient_id"])
    op.create_index("ix_patient_revisions_occurred_at", "patient_revisions", ["occurred_at"])
    op.create_index("ix_patients_clinic_name", "patients", ["clinic_id", "last_name", "first_name"])
    op.create_index("ix_appointments_clinic_starts", "appointments", ["clinic_id", "starts_at"])
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    active = "status IN ('scheduled','confirmed','arrived','in_progress','completed')"
    interval = "starts_at + (duration_minutes * INTERVAL '1 minute')"
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_doctor_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, doctor_id WITH =,
        tstzrange(starts_at, {interval}, '[)') WITH &&) WHERE ({active})""")
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_room_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, room_id WITH =,
        tstzrange(starts_at, {interval}, '[)') WITH &&) WHERE ({active})""")
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_assistant_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, assistant_id WITH =,
        tstzrange(starts_at, {interval}, '[)') WITH &&) WHERE ({active} AND assistant_id IS NOT NULL)""")


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_assistant_no_overlap")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_room_no_overlap")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_doctor_no_overlap")
    op.drop_index("ix_appointments_clinic_starts", table_name="appointments")
    op.drop_index("ix_patients_clinic_name", table_name="patients")
    op.drop_index("ix_patient_revisions_occurred_at", table_name="patient_revisions")
    op.drop_index("ix_patient_revisions_patient_id", table_name="patient_revisions")
    op.drop_index("ix_patient_revisions_clinic_id", table_name="patient_revisions")
    op.drop_table("patient_revisions")
