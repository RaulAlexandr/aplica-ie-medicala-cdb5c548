"""T19 team profiles, protected documents, shifts, leave and attendance."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t19_team_shifts"
down_revision = "t16_waitlist_recalls"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _fk(table: str, ondelete: str = "RESTRICT") -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "team_profiles",
        sa.Column("user_id", UUID, _fk("users"), primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("job_title", sa.String(120)),
        sa.Column("employment_start", sa.Date()),
        sa.Column("emergency_contact_name", sa.String(160)),
        sa.Column("emergency_contact_phone", sa.String(40)),
        sa.Column("updated_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_team_profiles_clinic_id", "team_profiles", ["clinic_id"])

    op.create_table(
        "team_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("user_id", UUID, _fk("users"), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False),
        sa.Column("expires_on", sa.Date()),
        sa.Column("uploaded_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("uploaded_at", TS, nullable=False),
        sa.Column("idempotency_key", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("archived_at", TS),
        sa.Column("archived_by_id", UUID, _fk("users")),
        sa.Column("archive_reason", sa.String(500)),
        sa.UniqueConstraint("storage_key", name="uq_team_document_storage_key"),
        sa.UniqueConstraint("clinic_id", "uploaded_by_id", "idempotency_key", name="uq_team_document_retry"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_team_document_status"),
        sa.CheckConstraint("category IN ('contract', 'certificate', 'medical', 'other')", name="ck_team_document_category"),
        sa.CheckConstraint("content_type IN ('image/jpeg', 'image/png', 'application/pdf')", name="ck_team_document_type"),
        sa.CheckConstraint("size_bytes > 0", name="ck_team_document_size"),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL AND archived_by_id IS NOT NULL AND archive_reason IS NOT NULL)",
            name="ck_team_document_archive",
        ),
    )
    op.create_index("ix_team_documents_member", "team_documents", ["clinic_id", "user_id", "uploaded_at"])

    op.create_table(
        "team_shifts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("user_id", UUID, _fk("users"), nullable=False),
        sa.Column("room_id", UUID, _fk("rooms")),
        sa.Column("post", sa.String(16), nullable=False),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("ends_at", TS, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("cancelled_at", TS),
        sa.Column("cancelled_by_id", UUID, _fk("users")),
        sa.Column("cancel_reason", sa.String(500)),
        sa.CheckConstraint("ends_at > starts_at", name="ck_team_shift_interval"),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name="ck_team_shift_status"),
        sa.CheckConstraint("post IN ('doctor', 'assistant', 'front_desk')", name="ck_team_shift_post"),
        sa.CheckConstraint("(post = 'front_desk') = (room_id IS NULL)", name="ck_team_shift_room"),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL AND cancel_reason IS NOT NULL)",
            name="ck_team_shift_cancel",
        ),
    )
    op.create_index("ix_team_shifts_clinic_interval", "team_shifts", ["clinic_id", "starts_at", "ends_at"])
    op.create_index("ix_team_shifts_user", "team_shifts", ["user_id", "starts_at"])

    op.create_table(
        "team_leave_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("user_id", UUID, _fk("users"), nullable=False),
        sa.Column("leave_type", sa.String(16), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("decided_by_id", UUID, _fk("users")),
        sa.Column("decided_at", TS),
        sa.Column("decision_reason", sa.String(500)),
        sa.Column("unavailability_id", UUID, _fk("resource_unavailability", "SET NULL")),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_team_leave_interval"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')", name="ck_team_leave_status"),
        sa.CheckConstraint("leave_type IN ('annual', 'medical', 'unpaid', 'other')", name="ck_team_leave_type"),
    )
    op.create_index("ix_team_leave_member", "team_leave_requests", ["clinic_id", "user_id", "starts_on"])

    op.create_table(
        "team_attendance",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("user_id", UUID, _fk("users"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("clock_in_at", TS, nullable=False),
        sa.Column("clock_out_at", TS),
        sa.Column("closed_by_id", UUID, _fk("users")),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_team_attendance_interval"),
        sa.CheckConstraint("source IN ('clock', 'manager')", name="ck_team_attendance_source"),
    )
    op.create_index("ix_team_attendance_member", "team_attendance", ["clinic_id", "user_id", "clock_in_at"])
    op.create_index(
        "uq_team_attendance_open", "team_attendance", ["clinic_id", "user_id"], unique=True,
        postgresql_where=sa.text("clock_out_at IS NULL"), sqlite_where=sa.text("clock_out_at IS NULL"),
    )

    op.create_table(
        "team_attendance_corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("attendance_id", UUID, _fk("team_attendance"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("clock_in_at", TS, nullable=False),
        sa.Column("clock_out_at", TS),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("attendance_id", "revision", name="uq_team_attendance_revision"),
        sa.CheckConstraint("clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_team_correction_interval"),
        sa.CheckConstraint("length(reason) > 0", name="ck_team_correction_reason"),
    )
    op.create_index("ix_team_attendance_corrections_clinic_id", "team_attendance_corrections", ["clinic_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute("""ALTER TABLE team_shifts ADD CONSTRAINT ex_team_shift_person
            EXCLUDE USING gist (clinic_id WITH =, user_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status = 'active')""")
        op.execute("""ALTER TABLE team_shifts ADD CONSTRAINT ex_team_shift_room_post
            EXCLUDE USING gist (clinic_id WITH =, room_id WITH =, post WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE (status = 'active' AND room_id IS NOT NULL)""")
        op.execute("""ALTER TABLE team_leave_requests ADD CONSTRAINT ex_team_leave_approved
            EXCLUDE USING gist (clinic_id WITH =, user_id WITH =,
            daterange(starts_on, ends_on, '[]') WITH &&) WHERE (status = 'approved')""")


def downgrade() -> None:
    op.drop_table("team_attendance_corrections")
    op.drop_table("team_attendance")
    op.drop_table("team_leave_requests")
    op.drop_table("team_shifts")
    op.drop_table("team_documents")
    op.drop_table("team_profiles")
