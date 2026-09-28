"""patient revisions and PostgreSQL scheduling guards

Revision ID: 0002_integrity_and_history
Revises: 0001_initial
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

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
    # Exclusion constraints may only use immutable expressions. Materialize
    # interval endpoints and maintain them in a trigger rather than putting a
    # duration/time-zone calculation inside the index expression.
    op.add_column("appointments", sa.Column("ends_at", now, nullable=True))
    op.execute("""
        UPDATE appointments
        SET ends_at = starts_at + (duration_minutes * INTERVAL '1 minute')
    """)
    op.execute("""
        ALTER TABLE appointments
        ALTER COLUMN ends_at SET NOT NULL,
        ADD CONSTRAINT appointments_time_order CHECK (ends_at > starts_at)
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION set_appointment_ends_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.ends_at := NEW.starts_at + (NEW.duration_minutes * INTERVAL '1 minute');
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER appointments_set_ends_at
        BEFORE INSERT OR UPDATE OF starts_at, duration_minutes ON appointments
        FOR EACH ROW EXECUTE FUNCTION set_appointment_ends_at()
    """)
    # Never delete legacy data to make a migration pass. Abort with a useful
    # error so an operator can remediate the exact records and retry.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM appointments a
                JOIN appointments b
                  ON a.id < b.id
                 AND a.clinic_id = b.clinic_id
                 AND a.status IN ('scheduled','confirmed','arrived','in_progress','completed')
                 AND b.status IN ('scheduled','confirmed','arrived','in_progress','completed')
                 AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')
                 AND (a.doctor_id = b.doctor_id OR a.room_id = b.room_id OR (a.assistant_id IS NOT NULL AND a.assistant_id = b.assistant_id))
            ) THEN
                RAISE EXCEPTION 'Cannot add appointment overlap constraints: existing active overlaps require remediation';
            END IF;
        END
        $$;
    """)
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    active = "status IN ('scheduled','confirmed','arrived','in_progress','completed')"
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_doctor_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, doctor_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE ({active})""")
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_room_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, room_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE ({active})""")
    op.execute(f"""ALTER TABLE appointments ADD CONSTRAINT appointments_assistant_no_overlap
        EXCLUDE USING gist (clinic_id WITH =, assistant_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE ({active} AND assistant_id IS NOT NULL)""")


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_assistant_no_overlap")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_room_no_overlap")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_doctor_no_overlap")
    op.execute("DROP TRIGGER IF EXISTS appointments_set_ends_at ON appointments")
    op.execute("DROP FUNCTION IF EXISTS set_appointment_ends_at()")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_time_order")
    op.drop_column("appointments", "ends_at")
    op.drop_index("ix_appointments_clinic_starts", table_name="appointments")
    op.drop_index("ix_patients_clinic_name", table_name="patients")
    op.drop_index("ix_patient_revisions_occurred_at", table_name="patient_revisions")
    op.drop_index("ix_patient_revisions_patient_id", table_name="patient_revisions")
    op.drop_index("ix_patient_revisions_clinic_id", table_name="patient_revisions")
    op.drop_table("patient_revisions")
