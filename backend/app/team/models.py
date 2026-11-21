"""T19 team data layered over the existing ``users`` table (no second user model).

Attendance is deliberately unrelated to procedure timers: nothing here references
``performed_procedures`` or its execution segments.
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class TeamProfile(Base):
    __tablename__ = "team_profiles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    job_title: Mapped[str | None] = mapped_column(String(120))
    employment_start: Mapped[date | None] = mapped_column(Date)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(160))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(40))
    updated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)


class TeamDocument(Base):
    """Administrative document metadata; content lives in T11 storage under a server-generated key."""

    __tablename__ = "team_documents"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    uploaded_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    archived_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    archive_reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        Index("ix_team_documents_member", "clinic_id", "user_id", "uploaded_at"),
        UniqueConstraint("storage_key", name="uq_team_document_storage_key"),
        UniqueConstraint("clinic_id", "uploaded_by_id", "idempotency_key", name="uq_team_document_retry"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_team_document_status"),
        CheckConstraint("category IN ('contract', 'certificate', 'medical', 'other')", name="ck_team_document_category"),
        CheckConstraint("content_type IN ('image/jpeg', 'image/png', 'application/pdf')", name="ck_team_document_type"),
        CheckConstraint("size_bytes > 0", name="ck_team_document_size"),
        CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL AND archived_by_id IS NOT NULL AND archive_reason IS NOT NULL)",
            name="ck_team_document_archive",
        ),
    )


class TeamShift(Base):
    """A shift is never edited: it is cancelled with a reason and a new one is created.

    PostgreSQL enforces non-overlap with exclusion constraints (see migration t19_team_shifts).
    ``post`` lets one doctor and one assistant share a room while two doctors cannot.
    """

    __tablename__ = "team_shifts"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    post: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        Index("ix_team_shifts_clinic_interval", "clinic_id", "starts_at", "ends_at"),
        Index("ix_team_shifts_user", "user_id", "starts_at"),
        CheckConstraint("ends_at > starts_at", name="ck_team_shift_interval"),
        CheckConstraint("status IN ('active', 'cancelled')", name="ck_team_shift_status"),
        CheckConstraint("post IN ('doctor', 'assistant', 'front_desk')", name="ck_team_shift_post"),
        CheckConstraint("(post = 'front_desk') = (room_id IS NULL)", name="ck_team_shift_room"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL AND cancelled_by_id IS NOT NULL AND cancel_reason IS NOT NULL)",
            name="ck_team_shift_cancel",
        ),
    )


class TeamLeaveRequest(Base):
    """Leave workflow. The agenda block itself stays in ``resource_unavailability`` (T02)."""

    __tablename__ = "team_leave_requests"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    leave_type: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    decided_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    unavailability_id: Mapped[UUID | None] = mapped_column(ForeignKey("resource_unavailability.id", ondelete="SET NULL"))
    __table_args__ = (
        Index("ix_team_leave_member", "clinic_id", "user_id", "starts_on"),
        CheckConstraint("ends_on >= starts_on", name="ck_team_leave_interval"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')", name="ck_team_leave_status"),
        CheckConstraint("leave_type IN ('annual', 'medical', 'unpaid', 'other')", name="ck_team_leave_type"),
    )


class TeamAttendance(Base):
    """One presence session. ``clock_in_at``/``clock_out_at`` are the original facts; a closed
    session is never changed. Manager corrections are separate rows."""

    __tablename__ = "team_attendance"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="clock")
    clock_in_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    clock_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_team_attendance_member", "clinic_id", "user_id", "clock_in_at"),
        Index("uq_team_attendance_open", "clinic_id", "user_id", unique=True,
              postgresql_where=text("clock_out_at IS NULL"), sqlite_where=text("clock_out_at IS NULL")),
        CheckConstraint("clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_team_attendance_interval"),
        CheckConstraint("source IN ('clock', 'manager')", name="ck_team_attendance_source"),
    )


class TeamAttendanceCorrection(Base):
    """Append-only manager correction; the highest revision gives the effective interval."""

    __tablename__ = "team_attendance_corrections"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True)
    attendance_id: Mapped[UUID] = mapped_column(ForeignKey("team_attendance.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    clock_in_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    clock_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("attendance_id", "revision", name="uq_team_attendance_revision"),
        CheckConstraint("clock_out_at IS NULL OR clock_out_at > clock_in_at", name="ck_team_correction_interval"),
        CheckConstraint("length(reason) > 0", name="ck_team_correction_reason"),
    )
