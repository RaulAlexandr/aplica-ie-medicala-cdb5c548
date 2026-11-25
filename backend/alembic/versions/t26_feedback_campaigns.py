"""Feedback, public review requests and patient campaigns."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t26_feedback_campaigns"
down_revision = "t15_patient_comms"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.drop_constraint("ck_comm_template_type", "comm_template_versions", type_="check")
    op.create_check_constraint("ck_comm_template_type", "comm_template_versions", "message_type IN ('confirmation','reminder','cancellation','reschedule','feedback_request','review_invitation','campaign')")
    op.create_table("feedback_settings",
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("delay_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("negative_threshold", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("review_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_url", sa.String(1000)), sa.Column("hourly_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("updated_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("delay_minutes BETWEEN 0 AND 10080", name="ck_feedback_delay"),
        sa.CheckConstraint("negative_threshold BETWEEN 1 AND 5", name="ck_feedback_threshold"),
        sa.CheckConstraint("hourly_limit BETWEEN 1 AND 10000", name="ck_feedback_hourly_limit"))
    op.create_table("feedback_campaigns",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False), sa.Column("filter_type", sa.String(40), nullable=False),
        sa.Column("filter_date", sa.DateTime(timezone=True)), sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)), sa.Column("template_type", sa.String(24), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("launched_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("filter_type IN ('last_visit_before','recall_due','procedure_in_period','no_future_appointment')", name="ck_campaign_filter_type"),
        sa.CheckConstraint("status IN ('draft','scheduled','sending','sent','failed')", name="ck_campaign_status"))
    op.create_index("ix_feedback_campaigns_clinic_id", "feedback_campaigns", ["clinic_id"])
    op.create_index("ix_campaign_clinic_status", "feedback_campaigns", ["clinic_id", "status"])
    op.add_column("comm_outbox", sa.Column("campaign_id", UUID, sa.ForeignKey("feedback_campaigns.id", ondelete="RESTRICT")))
    op.create_table("feedback_requests",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)), sa.Column("outbox_id", UUID, sa.ForeignKey("comm_outbox.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "appointment_id", name="uq_feedback_visit"))
    op.create_index("ix_feedback_requests_clinic_id", "feedback_requests", ["clinic_id"])
    op.create_index("ix_feedback_requests_patient", "feedback_requests", ["clinic_id", "patient_id", "created_at"])
    op.create_table("patient_feedback",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_id", UUID, sa.ForeignKey("feedback_requests.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("doctor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False), sa.Column("comment", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_patient_feedback_rating"))
    op.create_index("ix_patient_feedback_clinic_id", "patient_feedback", ["clinic_id"])
    op.create_index("ix_patient_feedback_clinic_date", "patient_feedback", ["clinic_id", "created_at"])
    op.create_table("feedback_task_links",
        sa.Column("feedback_id", UUID, sa.ForeignKey("patient_feedback.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("task_id", UUID, sa.ForeignKey("collab_tasks.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False))
    op.create_table("campaign_preferences",
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("opted_out", sa.Boolean(), nullable=False), sa.Column("updated_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("campaign_unsubscribe_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_campaign_unsubscribe_patient", "campaign_unsubscribe_tokens", ["clinic_id", "patient_id"])


def downgrade() -> None:
    for table in ("campaign_unsubscribe_tokens", "campaign_preferences", "feedback_task_links", "patient_feedback", "feedback_requests"):
        op.drop_table(table)
    op.drop_column("comm_outbox", "campaign_id")
    op.drop_index("ix_campaign_clinic_status", table_name="feedback_campaigns")
    op.drop_index("ix_feedback_campaigns_clinic_id", table_name="feedback_campaigns")
    op.drop_table("feedback_campaigns")
    op.drop_table("feedback_settings")
    op.drop_constraint("ck_comm_template_type", "comm_template_versions", type_="check")
    op.create_check_constraint("ck_comm_template_type", "comm_template_versions", "message_type IN ('confirmation','reminder','cancellation','reschedule')")
