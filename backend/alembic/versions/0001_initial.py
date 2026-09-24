"""initial milestone schema

Revision ID: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    now = sa.DateTime(timezone=True)
    op.create_table("clinics", sa.Column("id", uuid, primary_key=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("created_at", now, nullable=False))
    op.create_table("users", sa.Column("id", uuid, primary_key=True), sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id"), nullable=False), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("full_name", sa.String(160), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("is_active", sa.Boolean, nullable=False), sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"]))
    op.create_index("ix_users_clinic_id", "users", ["clinic_id"]); op.create_index("ix_users_email", "users", ["email"])
    op.create_table("refresh_tokens", sa.Column("id", uuid, primary_key=True), sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("token_hash", sa.String(128), nullable=False, unique=True), sa.Column("expires_at", now, nullable=False), sa.Column("revoked_at", now), sa.Column("created_at", now, nullable=False)); op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_table("rooms", sa.Column("id", uuid, primary_key=True), sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id"), nullable=False), sa.Column("name", sa.String(80), nullable=False), sa.Column("is_active", sa.Boolean, nullable=False), sa.UniqueConstraint("clinic_id", "name", name="uq_room_clinic_name")); op.create_index("ix_rooms_clinic_id", "rooms", ["clinic_id"])
    op.create_table("patients", sa.Column("id", uuid, primary_key=True), sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id"), nullable=False), sa.Column("first_name", sa.String(80), nullable=False), sa.Column("last_name", sa.String(80), nullable=False), sa.Column("date_of_birth", sa.Date), sa.Column("sex", sa.String(30)), sa.Column("phone", sa.String(40)), sa.Column("email", sa.String(320)), sa.Column("address", sa.String(300)), sa.Column("emergency_contact", sa.String(160)), sa.Column("occupation", sa.String(120)), sa.Column("allergies", sa.Text), sa.Column("medications", sa.Text), sa.Column("chronic_diseases", sa.Text), sa.Column("pregnancy_status", sa.String(40)), sa.Column("smoking_status", sa.String(40)), sa.Column("previous_surgeries", sa.Text), sa.Column("relevant_medical_conditions", sa.Text), sa.Column("medical_alerts", sa.Text), sa.Column("notes", sa.Text), sa.Column("created_at", now, nullable=False), sa.Column("updated_at", now, nullable=False)); op.create_index("ix_patients_clinic_id", "patients", ["clinic_id"])
    op.create_table("appointments", sa.Column("id", uuid, primary_key=True), sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id"), nullable=False), sa.Column("patient_id", uuid, sa.ForeignKey("patients.id"), nullable=False), sa.Column("doctor_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("assistant_id", uuid, sa.ForeignKey("users.id")), sa.Column("room_id", uuid, sa.ForeignKey("rooms.id"), nullable=False), sa.Column("starts_at", now, nullable=False), sa.Column("duration_minutes", sa.Integer, nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("appointment_type", sa.String(100), nullable=False), sa.Column("notes", sa.Text), sa.Column("created_at", now, nullable=False), sa.Column("updated_at", now, nullable=False)); op.create_index("ix_appointments_clinic_id", "appointments", ["clinic_id"])
    op.create_table("audit_events", sa.Column("id", uuid, primary_key=True), sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id"), nullable=False), sa.Column("actor_id", uuid, sa.ForeignKey("users.id"), nullable=False), sa.Column("entity_type", sa.String(60), nullable=False), sa.Column("entity_id", uuid, nullable=False), sa.Column("action", sa.String(40), nullable=False), sa.Column("occurred_at", now, nullable=False)); op.create_index("ix_audit_events_clinic_id", "audit_events", ["clinic_id"])


def downgrade() -> None:
    for table in ("audit_events", "appointments", "patients", "rooms", "refresh_tokens", "users", "clinics"): op.drop_table(table)
