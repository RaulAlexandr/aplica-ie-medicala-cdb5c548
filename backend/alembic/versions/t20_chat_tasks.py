"""T20 internal conversations, messages, tasks and shift handovers."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t20_chat_tasks"
down_revision = "t19_team_shifts"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _fk(table: str) -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "collab_conversations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(120)),
        sa.Column("direct_key", sa.String(73)),
        sa.Column("room_id", UUID, _fk("rooms")),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("clinic_id", "direct_key", name="uq_collab_direct_pair"),
        sa.UniqueConstraint("clinic_id", "room_id", name="uq_collab_room"),
        sa.CheckConstraint("kind IN ('direct', 'group', 'channel', 'room')", name="ck_collab_conversation_kind"),
        sa.CheckConstraint("(kind = 'direct') = (direct_key IS NOT NULL)", name="ck_collab_conversation_direct"),
        sa.CheckConstraint("(kind = 'room') = (room_id IS NOT NULL)", name="ck_collab_conversation_room"),
        sa.CheckConstraint("kind IN ('direct', 'room') OR title IS NOT NULL", name="ck_collab_conversation_title"),
        sa.CheckConstraint("last_seq >= 0", name="ck_collab_conversation_seq"),
    )
    op.create_index("ix_collab_conversations_clinic", "collab_conversations", ["clinic_id", "created_at"])

    op.create_table(
        "collab_members",
        sa.Column("conversation_id", UUID, _fk("collab_conversations"), primary_key=True),
        sa.Column("user_id", UUID, _fk("users"), primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("last_read_seq", sa.Integer(), nullable=False),
        sa.Column("joined_at", TS, nullable=False),
        sa.CheckConstraint("last_read_seq >= 0", name="ck_collab_member_read"),
    )
    op.create_index("ix_collab_members_user", "collab_members", ["clinic_id", "user_id"])

    op.create_table(
        "collab_messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("conversation_id", UUID, _fk("collab_conversations"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("sender_id", UUID, _fk("users"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_request_id", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_collab_message_seq"),
        sa.UniqueConstraint("clinic_id", "sender_id", "client_request_id", name="uq_collab_message_retry"),
        sa.CheckConstraint("seq > 0", name="ck_collab_message_seq"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 4000", name="ck_collab_message_body"),
    )

    op.create_table(
        "collab_message_recipients",
        sa.Column("message_id", UUID, _fk("collab_messages"), primary_key=True),
        sa.Column("user_id", UUID, _fk("users"), primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
    )
    op.create_index("ix_collab_recipients_user", "collab_message_recipients", ["clinic_id", "user_id", "message_id"])

    op.create_table(
        "collab_tasks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("assignee_id", UUID, _fk("users"), nullable=False),
        sa.Column("due_at", TS),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'blocked', 'resolved', 'cancelled')", name="ck_collab_task_status"),
        sa.CheckConstraint("version > 0", name="ck_collab_task_version"),
    )
    op.create_index("ix_collab_tasks_assignee", "collab_tasks", ["clinic_id", "assignee_id", "status"])
    op.create_index("ix_collab_tasks_due", "collab_tasks", ["clinic_id", "status", "due_at"])

    op.create_table(
        "collab_task_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("task_id", UUID, _fk("collab_tasks"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(12)),
        sa.Column("to_status", sa.String(12), nullable=False),
        sa.Column("from_assignee_id", UUID, _fk("users")),
        sa.Column("to_assignee_id", UUID, _fk("users"), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("actor_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("task_id", "version", name="uq_collab_task_event_version"),
        sa.CheckConstraint("to_status NOT IN ('blocked', 'cancelled') OR from_status = to_status OR reason IS NOT NULL", name="ck_collab_task_event_reason"),
    )

    op.create_table(
        "collab_handovers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("shift_id", UUID, _fk("team_shifts"), nullable=False),
        sa.Column("from_user_id", UUID, _fk("users"), nullable=False),
        sa.Column("to_user_id", UUID, _fk("users"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by_id", UUID, _fk("users"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("shift_id", name="uq_collab_handover_shift"),
        sa.CheckConstraint("from_user_id <> to_user_id", name="ck_collab_handover_people"),
        sa.CheckConstraint("length(notes) BETWEEN 1 AND 4000", name="ck_collab_handover_notes"),
    )
    op.create_index("ix_collab_handovers_to", "collab_handovers", ["clinic_id", "to_user_id", "created_at"])
    op.create_index("ix_collab_handovers_from", "collab_handovers", ["clinic_id", "from_user_id", "created_at"])

    op.create_table(
        "collab_handover_tasks",
        sa.Column("handover_id", UUID, _fk("collab_handovers"), primary_key=True),
        sa.Column("task_id", UUID, _fk("collab_tasks"), primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("due_at", TS),
    )

    op.create_table(
        "collab_handover_acks",
        sa.Column("handover_id", UUID, _fk("collab_handovers"), primary_key=True),
        sa.Column("clinic_id", UUID, _fk("clinics"), nullable=False),
        sa.Column("user_id", UUID, _fk("users"), nullable=False),
        sa.Column("acknowledged_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("collab_handover_acks")
    op.drop_table("collab_handover_tasks")
    op.drop_table("collab_handovers")
    op.drop_table("collab_task_events")
    op.drop_table("collab_tasks")
    op.drop_table("collab_message_recipients")
    op.drop_table("collab_messages")
    op.drop_table("collab_members")
    op.drop_table("collab_conversations")
