"""T15 patient communication v1: consent, template versions, settings, outbox and its events."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t15_patient_comms"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)
CLINIC = lambda: sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
USER_FK = lambda name, nullable=False: sa.Column(name, UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


def upgrade() -> None:
    op.create_table("comm_consents",
        sa.Column("id", UUID, primary_key=True), CLINIC(),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False), sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False), USER_FK("recorded_by_id"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('sms','email')", name="ck_comm_consents_channel"))
    op.create_index("ix_comm_consents_clinic_id", "comm_consents", ["clinic_id"])
    op.create_index("ix_comm_consents_patient_channel", "comm_consents", ["patient_id", "channel", "recorded_at"])
    op.create_table("comm_template_versions",
        sa.Column("id", UUID, primary_key=True), CLINIC(),
        sa.Column("message_type", sa.String(24), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False), sa.Column("body", sa.Text(), nullable=False),
        USER_FK("created_by_id"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "message_type", "version", name="uq_comm_template_version"),
        sa.CheckConstraint("message_type IN ('confirmation','reminder','cancellation','reschedule')", name="ck_comm_template_type"))
    op.create_index("ix_comm_template_versions_clinic_id", "comm_template_versions", ["clinic_id"])
    op.create_table("comm_settings",
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("reminder_hours", sa.Integer(), nullable=False), sa.Column("quiet_start", sa.Time(), nullable=False),
        sa.Column("quiet_end", sa.Time(), nullable=False), sa.Column("clinic_phone", sa.String(40)),
        USER_FK("updated_by_id", True), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reminder_hours BETWEEN 1 AND 336", name="ck_comm_settings_hours"))
    op.create_table("comm_outbox",
        sa.Column("id", UUID, primary_key=True), CLINIC(),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("message_type", sa.String(24), nullable=False), sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("appointment_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(300)), sa.Column("reason", sa.String(64)), sa.Column("template_version", sa.Integer()),
        sa.Column("recipient", sa.String(320)), sa.Column("subject", sa.String(200)), sa.Column("body", sa.Text()),
        sa.Column("provider_ref", sa.String(80)), sa.Column("adapter", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("idempotency_key", name="uq_comm_outbox_idempotency"),
        sa.CheckConstraint("status IN ('pending','sent','delivered','failed','cancelled')", name="ck_comm_outbox_status"),
        sa.CheckConstraint("channel IN ('sms','email')", name="ck_comm_outbox_channel"))
    op.create_index("ix_comm_outbox_clinic_id", "comm_outbox", ["clinic_id"])
    op.create_index("ix_comm_outbox_due", "comm_outbox", ["status", "scheduled_for"])
    op.create_index("ix_comm_outbox_patient", "comm_outbox", ["patient_id", "created_at"])
    op.create_index("ix_comm_outbox_appointment", "comm_outbox", ["appointment_id"])
    op.create_table("comm_outbox_events",
        sa.Column("id", UUID, primary_key=True), CLINIC(),
        sa.Column("outbox_id", UUID, sa.ForeignKey("comm_outbox.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("from_status", sa.String(16)), sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(300)), USER_FK("actor_id", True), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_comm_outbox_events_clinic_id", "comm_outbox_events", ["clinic_id"])
    op.create_index("ix_comm_outbox_events_outbox_id", "comm_outbox_events", ["outbox_id"])


def downgrade() -> None:
    for table in ("comm_outbox_events", "comm_outbox", "comm_settings", "comm_template_versions", "comm_consents"):
        op.drop_table(table)
