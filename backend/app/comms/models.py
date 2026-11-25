"""T15 patient communication: consent, versioned templates, per-clinic settings, outbox and its transitions."""
from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow

CHANNELS = ("sms", "email")
MESSAGE_TYPES = ("confirmation", "reminder", "cancellation", "reschedule", "feedback_request", "review_invitation", "campaign")
STATUSES = ("pending", "sent", "delivered", "failed", "cancelled")


class CommConsent(Base):
    """Append-only: the current consent of a patient on a channel is its newest row; withdrawal is a new row."""
    __tablename__ = "comm_consents"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("channel IN ('sms','email')", name="ck_comm_consents_channel"),
        Index("ix_comm_consents_patient_channel", "patient_id", "channel", "recorded_at"),
    )


class CommTemplateVersion(Base):
    """Immutable template version. The current text of (clinic, message type) is its highest version."""
    __tablename__ = "comm_template_versions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("clinic_id", "message_type", "version", name="uq_comm_template_version"),
        CheckConstraint("message_type IN ('confirmation','reminder','cancellation','reschedule','feedback_request','review_invitation','campaign')", name="ck_comm_template_type"),
    )


class CommSettings(Base):
    __tablename__ = "comm_settings"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True)
    reminder_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    quiet_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(20, 0))
    quiet_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 0))
    clinic_phone: Mapped[str | None] = mapped_column(String(40))
    updated_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint("reminder_hours BETWEEN 1 AND 336", name="ck_comm_settings_hours"),)


class CommOutbox(Base):
    __tablename__ = "comm_outbox"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("feedback_campaigns.id", ondelete="RESTRICT"))
    message_type: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    appointment_starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(String(64))
    template_version: Mapped[int | None] = mapped_column(Integer)
    recipient: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(String(80))
    adapter: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_comm_outbox_idempotency"),
        CheckConstraint("status IN ('pending','sent','delivered','failed','cancelled')", name="ck_comm_outbox_status"),
        CheckConstraint("channel IN ('sms','email')", name="ck_comm_outbox_channel"),
        Index("ix_comm_outbox_due", "status", "scheduled_for"),
        Index("ix_comm_outbox_patient", "patient_id", "created_at"),
        Index("ix_comm_outbox_appointment", "appointment_id"),
    )


class CommOutboxEvent(Base):
    """Every status transition of an outbox message, kept as written."""
    __tablename__ = "comm_outbox_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    outbox_id: Mapped[UUID] = mapped_column(ForeignKey("comm_outbox.id", ondelete="RESTRICT"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
