"""T20 internal messages, tasks and shift handovers.

Nothing here references patients. Messages, task events, handovers, their task snapshots and
acknowledgements are append-only: the API has no route that edits or deletes them.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, UUIDType, utcnow


class CollabConversation(Base):
    """``direct_key`` makes a pair unique; ``room_id`` gives each room one conversation."""

    __tablename__ = "collab_conversations"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(120))
    direct_key: Mapped[str | None] = mapped_column(String(73))
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.id", ondelete="RESTRICT"))
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("clinic_id", "direct_key", name="uq_collab_direct_pair"),
        UniqueConstraint("clinic_id", "room_id", name="uq_collab_room"),
        Index("ix_collab_conversations_clinic", "clinic_id", "created_at"),
        CheckConstraint("kind IN ('direct', 'group', 'channel', 'room')", name="ck_collab_conversation_kind"),
        CheckConstraint("(kind = 'direct') = (direct_key IS NOT NULL)", name="ck_collab_conversation_direct"),
        CheckConstraint("(kind = 'room') = (room_id IS NOT NULL)", name="ck_collab_conversation_room"),
        CheckConstraint("kind IN ('direct', 'room') OR title IS NOT NULL", name="ck_collab_conversation_title"),
        CheckConstraint("last_seq >= 0", name="ck_collab_conversation_seq"),
    )


class CollabMember(Base):
    """Membership and the read marker. In a room conversation a person only reads messages
    they sent or were saved as a recipient of."""

    __tablename__ = "collab_members"
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("collab_conversations.id", ondelete="RESTRICT"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    last_read_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_collab_members_user", "clinic_id", "user_id"),
        CheckConstraint("last_read_seq >= 0", name="ck_collab_member_read"),
    )


class CollabMessage(Base):
    __tablename__ = "collab_messages"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("collab_conversations.id", ondelete="RESTRICT"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    client_request_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_collab_message_seq"),
        UniqueConstraint("clinic_id", "sender_id", "client_request_id", name="uq_collab_message_retry"),
        CheckConstraint("seq > 0", name="ck_collab_message_seq"),
        CheckConstraint("length(body) BETWEEN 1 AND 4000", name="ck_collab_message_body"),
    )


class CollabMessageRecipient(Base):
    """Who received a room message when it was sent. A later shift change never rewrites it."""

    __tablename__ = "collab_message_recipients"
    message_id: Mapped[UUID] = mapped_column(ForeignKey("collab_messages.id", ondelete="RESTRICT"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    __table_args__ = (Index("ix_collab_recipients_user", "clinic_id", "user_id", "message_id"),)


class CollabTask(Base):
    __tablename__ = "collab_tasks"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_collab_tasks_assignee", "clinic_id", "assignee_id", "status"),
        Index("ix_collab_tasks_due", "clinic_id", "status", "due_at"),
        CheckConstraint("status IN ('open', 'in_progress', 'blocked', 'resolved', 'cancelled')", name="ck_collab_task_status"),
        CheckConstraint("version > 0", name="ck_collab_task_version"),
    )


class CollabTaskEvent(Base):
    """One row per task version: creation, or a change of status and/or assignee."""

    __tablename__ = "collab_task_events"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("collab_tasks.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12), nullable=False)
    from_assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    to_assignee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_collab_task_event_version"),
        CheckConstraint("to_status NOT IN ('blocked', 'cancelled') OR from_status = to_status OR reason IS NOT NULL", name="ck_collab_task_event_reason"),
    )


class CollabHandover(Base):
    """A shift has at most one handover and it is never rewritten. Receipt is a separate row."""

    __tablename__ = "collab_handovers"
    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True, default=uuid4)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(ForeignKey("team_shifts.id", ondelete="RESTRICT"), nullable=False)
    from_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    to_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("shift_id", name="uq_collab_handover_shift"),
        Index("ix_collab_handovers_to", "clinic_id", "to_user_id", "created_at"),
        Index("ix_collab_handovers_from", "clinic_id", "from_user_id", "created_at"),
        CheckConstraint("from_user_id <> to_user_id", name="ck_collab_handover_people"),
        CheckConstraint("length(notes) BETWEEN 1 AND 4000", name="ck_collab_handover_notes"),
    )


class CollabHandoverTask(Base):
    """The unresolved task as it stood when the handover was written."""

    __tablename__ = "collab_handover_tasks"
    handover_id: Mapped[UUID] = mapped_column(ForeignKey("collab_handovers.id", ondelete="RESTRICT"), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("collab_tasks.id", ondelete="RESTRICT"), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CollabHandoverAck(Base):
    __tablename__ = "collab_handover_acks"
    handover_id: Mapped[UUID] = mapped_column(ForeignKey("collab_handovers.id", ondelete="RESTRICT"), primary_key=True)
    clinic_id: Mapped[UUID] = mapped_column(ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
