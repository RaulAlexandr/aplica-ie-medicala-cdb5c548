from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class FeedbackSettings(Base):
    __tablename__ = "feedback_settings"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    negative_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    review_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_url: Mapped[str | None] = mapped_column(String(1000))
    hourly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    updated_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("delay_minutes BETWEEN 0 AND 10080", name="ck_feedback_delay"), CheckConstraint("negative_threshold BETWEEN 1 AND 5", name="ck_feedback_threshold"), CheckConstraint("hourly_limit BETWEEN 1 AND 10000", name="ck_feedback_hourly_limit"))


class FeedbackRequest(Base):
    __tablename__ = "feedback_requests"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outbox_id: Mapped[UUID | None] = mapped_column(ForeignKey("comm_outbox.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("clinic_id", "appointment_id", name="uq_feedback_visit"), Index("ix_feedback_requests_patient", "clinic_id", "patient_id", "created_at"))


class PatientFeedback(Base):
    __tablename__ = "patient_feedback"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("feedback_requests.id", ondelete="RESTRICT"), nullable=False, unique=True)
    appointment_id: Mapped[UUID] = mapped_column(ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    doctor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="ck_patient_feedback_rating"), Index("ix_patient_feedback_clinic_date", "clinic_id", "created_at"))


class FeedbackTaskLink(Base):
    __tablename__ = "feedback_task_links"
    feedback_id: Mapped[UUID] = mapped_column(ForeignKey("patient_feedback.id", ondelete="RESTRICT"), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("collab_tasks.id", ondelete="RESTRICT"), nullable=False, unique=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)


class CampaignPreference(Base):
    __tablename__ = "campaign_preferences"
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), primary_key=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), primary_key=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class CampaignUnsubscribeToken(Base):
    __tablename__ = "campaign_unsubscribe_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    __table_args__ = (Index("ix_campaign_unsubscribe_patient", "clinic_id", "patient_id"),)


class Campaign(Base):
    __tablename__ = "feedback_campaigns"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    filter_type: Mapped[str] = mapped_column(String(40), nullable=False)
    filter_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    template_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    launched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (CheckConstraint("filter_type IN ('last_visit_before','recall_due','procedure_in_period','no_future_appointment')", name="ck_campaign_filter_type"), CheckConstraint("status IN ('draft','scheduled','sending','sent','failed')", name="ck_campaign_status"), Index("ix_campaign_clinic_status", "clinic_id", "status"))
