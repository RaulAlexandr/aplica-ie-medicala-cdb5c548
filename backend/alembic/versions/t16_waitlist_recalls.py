"""T16 waitlist offers and patient recalls."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t16_waitlist_recalls"
down_revision = "t12_intake_forms"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

def upgrade() -> None:
    op.create_table("waitlist_entries",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False), sa.Column("desired_service", sa.String(120)),
        sa.Column("desired_duration_minutes", sa.Integer(), nullable=False), sa.Column("preferred_doctor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("windows", JSON, nullable=False), sa.Column("valid_from", sa.Date(), nullable=False), sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False), sa.Column("notes", sa.Text()), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("desired_duration_minutes BETWEEN 5 AND 1440", name="ck_waitlist_duration"), sa.CheckConstraint("valid_until >= valid_from", name="ck_waitlist_validity"),
        sa.CheckConstraint("status IN ('active','offered','booked','cancelled','expired')", name="ck_waitlist_status"))
    op.create_index("ix_waitlist_entries_clinic_id", "waitlist_entries", ["clinic_id"])
    op.create_index("ix_waitlist_entries_patient_id", "waitlist_entries", ["patient_id"])
    op.create_table("waitlist_offers",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("entry_id", UUID, sa.ForeignKey("waitlist_entries.id", ondelete="RESTRICT"), nullable=False), sa.Column("doctor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("room_id", UUID, sa.ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False), sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("duration_minutes BETWEEN 5 AND 1440", name="ck_waitlist_offer_duration"),
        sa.CheckConstraint("status IN ('active','confirmed','expired','cancelled')", name="ck_waitlist_offer_status"))
    op.create_index("ix_waitlist_offers_clinic_id", "waitlist_offers", ["clinic_id"])
    op.create_index("ix_waitlist_offers_entry_id", "waitlist_offers", ["entry_id"])
    op.create_index("uq_waitlist_active_interval", "waitlist_offers", ["clinic_id", "doctor_id", "room_id", "starts_at", "duration_minutes"], unique=True, postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.create_table("patient_recalls",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", UUID, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False), sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False), sa.Column("service", sa.String(120)), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending','contacted','scheduled','refused')", name="ck_recall_status"))
    op.create_index("ix_patient_recalls_clinic_id", "patient_recalls", ["clinic_id"])
    op.create_index("ix_patient_recalls_patient_id", "patient_recalls", ["patient_id"])
    op.create_table("patient_recall_history",
        sa.Column("id", UUID, primary_key=True), sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recall_id", UUID, sa.ForeignKey("patient_recalls.id", ondelete="RESTRICT"), nullable=False), sa.Column("actor_id", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('contacted','scheduled','refused')", name="ck_recall_history_action"))
    op.create_index("ix_patient_recall_history_clinic_id", "patient_recall_history", ["clinic_id"])
    op.create_index("ix_patient_recall_history_recall_id", "patient_recall_history", ["recall_id"])

def downgrade() -> None:
    op.drop_index("ix_patient_recall_history_recall_id", table_name="patient_recall_history")
    op.drop_index("ix_patient_recall_history_clinic_id", table_name="patient_recall_history")
    op.drop_table("patient_recall_history")
    op.drop_index("ix_patient_recalls_patient_id", table_name="patient_recalls")
    op.drop_index("ix_patient_recalls_clinic_id", table_name="patient_recalls")
    op.drop_table("patient_recalls")
    op.drop_index("uq_waitlist_active_interval", table_name="waitlist_offers")
    op.drop_index("ix_waitlist_offers_entry_id", table_name="waitlist_offers")
    op.drop_index("ix_waitlist_offers_clinic_id", table_name="waitlist_offers")
    op.drop_table("waitlist_offers")
    op.drop_index("ix_waitlist_entries_patient_id", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_entries_clinic_id", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
