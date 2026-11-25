"""T14 patient portal: separate accounts, sessions, publications and patient requests.

Portal accounts are not staff users. They never appear in ``users`` and their tokens are signed with
a key derived for the portal only (see ``security.py``). A patient sees only what staff published for
a patient the account is linked to.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow

JSONType = JSON().with_variant(JSONB, "postgresql")


def _fk(table: str) -> ForeignKey:
    return ForeignKey(f"{table}.id", ondelete="RESTRICT")


class PortalAccount(Base):
    """One login per clinic and email. ``password_hash`` is set when the invitation is accepted."""

    __tablename__ = "portal_accounts"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="invited")
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_by_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "email", name="uq_portal_account_email"),
        CheckConstraint("status IN ('invited', 'active', 'revoked')", name="ck_portal_account_status"),
        CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name="ck_portal_account_revoked"),
        CheckConstraint("status != 'active' OR password_hash IS NOT NULL", name="ck_portal_account_password"),
    )


class PortalInvitation(Base):
    """Single-use, 72 h. Only the SHA-256 of the token is stored."""

    __tablename__ = "portal_invitations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_portal_invitation_token"),
        Index("ix_portal_invitations_account", "account_id", "created_at"),
    )


class PortalAccountPatient(Base):
    """The patients an account may see: the patient themselves or a represented person."""

    __tablename__ = "portal_account_patients"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_by_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    __table_args__ = (
        UniqueConstraint("account_id", "patient_id", name="uq_portal_account_patient"),
        Index("ix_portal_account_patients_patient", "clinic_id", "patient_id"),
    )


class PortalSession(Base):
    """A login. Access tokens carry the session id, so revoking it ends them at once."""

    __tablename__ = "portal_sessions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("refresh_hash", name="uq_portal_session_refresh"),
        Index("ix_portal_sessions_account", "account_id"),
    )


class PortalLoginAttempt(Base):
    """Every login attempt, known email or not. ``clinic_id`` is not a foreign key on purpose:
    an attempt for an unknown clinic is recorded the same way, so the answer reveals nothing."""

    __tablename__ = "portal_login_attempts"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_portal_login_attempts_key", "clinic_id", "email", "occurred_at"),
        CheckConstraint("outcome IN ('success', 'failure', 'blocked')", name="ck_portal_login_outcome"),
    )


class PortalAuditEvent(Base):
    """Append-only log of portal activity: the account or the staff member, the patient, the moment."""

    __tablename__ = "portal_audit_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(_fk("portal_accounts"))
    staff_actor_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    patient_id: Mapped[UUID | None] = mapped_column(_fk("patients"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[UUID | None] = mapped_column(UUIDType)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_portal_audit_patient", "clinic_id", "patient_id", "occurred_at"),
        CheckConstraint("account_id IS NOT NULL OR staff_actor_id IS NOT NULL", name="ck_portal_audit_actor"),
    )


class PortalAppointmentConfirmation(Base):
    __tablename__ = "portal_appointment_confirmations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(_fk("appointments"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("appointment_id", name="uq_portal_confirmation_appointment"),)


class PortalRescheduleRequest(Base):
    """A wish to move a visit. It never moves the appointment; reception resolves it."""

    __tablename__ = "portal_reschedule_requests"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(_fk("appointments"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    resolved_by_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolution_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("account_id", "client_request_id", name="uq_portal_reschedule_retry"),
        Index("uq_portal_reschedule_open", "appointment_id", unique=True,
              postgresql_where=text("status = 'open'"), sqlite_where=text("status = 'open'")),
        Index("ix_portal_reschedule_clinic", "clinic_id", "status", "created_at"),
        CheckConstraint("status IN ('open', 'rescheduled', 'rejected')", name="ck_portal_reschedule_status"),
        CheckConstraint("(status = 'open') = (resolved_at IS NULL)", name="ck_portal_reschedule_resolved"),
    )


class PortalPublishedDocument(Base):
    __tablename__ = "portal_published_documents"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    document_id: Mapped[UUID] = mapped_column(_fk("clinical_documents"), nullable=False)
    published_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    withdrawn_by_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    __table_args__ = (
        Index("uq_portal_document_active", "document_id", unique=True,
              postgresql_where=text("withdrawn_at IS NULL"), sqlite_where=text("withdrawn_at IS NULL")),
        Index("ix_portal_documents_patient", "clinic_id", "patient_id", "published_at"),
    )


class PortalPublishedPlan(Base):
    """A frozen, patient-safe copy of one plan version: no notes, no internal identifiers."""

    __tablename__ = "portal_published_plans"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(_fk("treatment_plans"), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    published_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    withdrawn_by_id: Mapped[UUID | None] = mapped_column(_fk("users"))
    __table_args__ = (
        Index("uq_portal_plan_active", "plan_id", unique=True,
              postgresql_where=text("withdrawn_at IS NULL"), sqlite_where=text("withdrawn_at IS NULL")),
        Index("ix_portal_plans_patient", "clinic_id", "patient_id", "published_at"),
    )


class PortalFormRequest(Base):
    """A T12 patient form sent to the portal. The form itself stays in ``patient_forms``."""

    __tablename__ = "portal_form_requests"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(_fk("patients"), nullable=False)
    patient_form_id: Mapped[UUID] = mapped_column(_fk("patient_forms"), nullable=False)
    sent_by_id: Mapped[UUID] = mapped_column(_fk("users"), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    __table_args__ = (
        UniqueConstraint("patient_form_id", name="uq_portal_form_request_form"),
        UniqueConstraint("sent_by_id", "client_request_id", name="uq_portal_form_request_retry"),
        Index("ix_portal_form_requests_patient", "clinic_id", "patient_id", "sent_at"),
    )


class PortalFormSubmission(Base):
    """Each answer set the patient submitted, kept as a numbered revision with the portal account."""

    __tablename__ = "portal_form_submissions"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(_fk("clinics"), nullable=False)
    patient_form_id: Mapped[UUID] = mapped_column(_fk("patient_forms"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(_fk("portal_accounts"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    answers: Mapped[dict] = mapped_column(JSONType, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("patient_form_id", "revision_number", name="uq_portal_form_submission_rev"),
        CheckConstraint("revision_number > 0", name="ck_portal_form_submission_rev"),
    )
