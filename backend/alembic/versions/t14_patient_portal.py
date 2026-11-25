"""T14 patient portal: accounts, invitations, sessions, publications, requests and audit."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t14_patient_portal"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _fk(table: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "portal_accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("activated_at", TS),
        sa.Column("revoked_at", TS),
        sa.Column("revoked_by_id", UUID, _fk("users")),
        sa.Column("revoke_reason", sa.Text()),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("clinic_id", "email", name="uq_portal_account_email"),
        sa.CheckConstraint("status IN ('invited', 'active', 'revoked')", name="ck_portal_account_status"),
        sa.CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name="ck_portal_account_revoked"),
        sa.CheckConstraint("status != 'active' OR password_hash IS NOT NULL", name="ck_portal_account_password"),
    )
    op.create_table(
        "portal_invitations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
        sa.Column("revoked_at", TS),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_portal_invitation_token"),
    )
    op.create_index("ix_portal_invitations_account", "portal_invitations", ["account_id", "created_at"])
    op.create_table(
        "portal_account_patients",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("relationship", sa.String(40), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("revoked_by_id", UUID, _fk("users")),
        sa.UniqueConstraint("account_id", "patient_id", name="uq_portal_account_patient"),
    )
    op.create_index("ix_portal_account_patients_patient", "portal_account_patients", ["clinic_id", "patient_id"])
    op.create_table(
        "portal_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("refresh_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("refresh_hash", name="uq_portal_session_refresh"),
    )
    op.create_index("ix_portal_sessions_account", "portal_sessions", ["account_id"])
    op.create_table(
        "portal_login_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        sa.CheckConstraint("outcome IN ('success', 'failure', 'blocked')", name="ck_portal_login_outcome"),
    )
    op.create_index("ix_portal_login_attempts_key", "portal_login_attempts", ["clinic_id", "email", "occurred_at"])
    op.create_table(
        "portal_audit_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts")),
        sa.Column("staff_actor_id", UUID, _fk("users")),
        sa.Column("patient_id", UUID, _fk("patients")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(40)),
        sa.Column("entity_id", UUID),
        sa.Column("occurred_at", TS, nullable=False),
        sa.CheckConstraint("account_id IS NOT NULL OR staff_actor_id IS NOT NULL", name="ck_portal_audit_actor"),
    )
    op.create_index("ix_portal_audit_patient", "portal_audit_events", ["clinic_id", "patient_id", "occurred_at"])
    op.create_table(
        "portal_appointment_confirmations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("appointment_id", UUID, _fk("appointments"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("confirmed_at", TS, nullable=False),
        sa.UniqueConstraint("appointment_id", name="uq_portal_confirmation_appointment"),
    )
    op.create_table(
        "portal_reschedule_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("appointment_id", UUID, _fk("appointments"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("client_request_id", UUID, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("resolved_by_id", UUID, _fk("users")),
        sa.Column("resolved_at", TS),
        sa.Column("resolution_note", sa.Text()),
        sa.UniqueConstraint("account_id", "client_request_id", name="uq_portal_reschedule_retry"),
        sa.CheckConstraint("status IN ('open', 'rescheduled', 'rejected')", name="ck_portal_reschedule_status"),
        sa.CheckConstraint("(status = 'open') = (resolved_at IS NULL)", name="ck_portal_reschedule_resolved"),
    )
    op.create_index("uq_portal_reschedule_open", "portal_reschedule_requests", ["appointment_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"), sqlite_where=sa.text("status = 'open'"))
    op.create_index("ix_portal_reschedule_clinic", "portal_reschedule_requests", ["clinic_id", "status", "created_at"])
    op.create_table(
        "portal_published_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("document_id", UUID, _fk("clinical_documents"), nullable=False),
        sa.Column("published_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("published_at", TS, nullable=False),
        sa.Column("withdrawn_at", TS),
        sa.Column("withdrawn_by_id", UUID, _fk("users")),
    )
    op.create_index("uq_portal_document_active", "portal_published_documents", ["document_id"], unique=True,
                    postgresql_where=sa.text("withdrawn_at IS NULL"), sqlite_where=sa.text("withdrawn_at IS NULL"))
    op.create_index("ix_portal_documents_patient", "portal_published_documents", ["clinic_id", "patient_id", "published_at"])
    op.create_table(
        "portal_published_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("plan_id", UUID, _fk("treatment_plans"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("published_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("published_at", TS, nullable=False),
        sa.Column("withdrawn_at", TS),
        sa.Column("withdrawn_by_id", UUID, _fk("users")),
    )
    op.create_index("uq_portal_plan_active", "portal_published_plans", ["plan_id"], unique=True,
                    postgresql_where=sa.text("withdrawn_at IS NULL"), sqlite_where=sa.text("withdrawn_at IS NULL"))
    op.create_index("ix_portal_plans_patient", "portal_published_plans", ["clinic_id", "patient_id", "published_at"])
    op.create_table(
        "portal_form_requests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("patient_id", UUID, _fk("patients"), nullable=False),
        sa.Column("patient_form_id", UUID, _fk("patient_forms"), nullable=False),
        sa.Column("sent_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("sent_at", TS, nullable=False),
        sa.Column("client_request_id", UUID, nullable=False),
        sa.UniqueConstraint("patient_form_id", name="uq_portal_form_request_form"),
        sa.UniqueConstraint("sent_by_id", "client_request_id", name="uq_portal_form_request_retry"),
    )
    op.create_index("ix_portal_form_requests_patient", "portal_form_requests", ["clinic_id", "patient_id", "sent_at"])
    op.create_table(
        "portal_form_submissions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("patient_form_id", UUID, _fk("patient_forms"), nullable=False),
        sa.Column("account_id", UUID, _fk("portal_accounts"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("answers", JSONB, nullable=False),
        sa.Column("submitted_at", TS, nullable=False),
        sa.UniqueConstraint("patient_form_id", "revision_number", name="uq_portal_form_submission_rev"),
        sa.CheckConstraint("revision_number > 0", name="ck_portal_form_submission_rev"),
    )


def downgrade() -> None:
    for table in ("portal_form_submissions", "portal_form_requests", "portal_published_plans", "portal_published_documents",
                  "portal_reschedule_requests", "portal_appointment_confirmations", "portal_audit_events",
                  "portal_login_attempts", "portal_sessions", "portal_account_patients", "portal_invitations", "portal_accounts"):
        op.drop_table(table)
