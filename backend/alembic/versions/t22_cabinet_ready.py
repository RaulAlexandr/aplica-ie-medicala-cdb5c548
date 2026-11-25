"""T22 Cabinet Ready: versioned checklists, preparations and append-only confirmations."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t22_cabinet_ready"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)
KIND_CHECK = "kind IN ('material','instrument','document','action')"


def _id():
    return sa.Column("id", UUID, primary_key=True)


def _clinic():
    return sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False)


def _fk(name, target, *, nullable=False):
    return sa.Column(name, UUID, sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def _at(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


TABLES = (
    "cabinet_request_keys", "cabinet_manual_block_closures", "cabinet_manual_blocks", "cabinet_confirmation_events",
    "cabinet_preparation_items", "cabinet_particularity_decisions", "cabinet_particularities", "cabinet_preparations",
    "cabinet_checklist_items", "cabinet_checklist_versions",
)


def upgrade() -> None:
    op.create_table("cabinet_checklist_versions", _id(), _clinic(), _fk("procedure_catalog_id", "procedure_catalog.id"),
        sa.Column("version", sa.Integer(), nullable=False), _fk("created_by_id", "users.id"), _at("created_at"),
        sa.UniqueConstraint("clinic_id", "procedure_catalog_id", "version", name="uq_cabinet_checklist_version"),
        sa.CheckConstraint("version >= 1", name="ck_cabinet_checklist_version"))
    op.create_table("cabinet_checklist_items", _id(), _clinic(), _fk("version_id", "cabinet_checklist_versions.id"),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False), sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("prep_minutes", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "position", name="uq_cabinet_checklist_item_position"),
        sa.CheckConstraint(KIND_CHECK, name="ck_cabinet_checklist_item_kind"),
        sa.CheckConstraint("prep_minutes BETWEEN 0 AND 240", name="ck_cabinet_checklist_item_minutes"))
    op.create_table("cabinet_preparations", _id(), _clinic(), _fk("appointment_id", "appointments.id"),
        sa.Column("revision", sa.Integer(), nullable=False), _fk("room_id", "rooms.id"), _at("starts_at"),
        sa.Column("procedures_signature", sa.Text(), nullable=False), _fk("started_by_id", "users.id"), _at("started_at"),
        sa.UniqueConstraint("appointment_id", "revision", name="uq_cabinet_preparation_revision"))
    op.create_table("cabinet_particularities", _id(), _clinic(), _fk("appointment_id", "appointments.id"),
        sa.Column("text", sa.String(500), nullable=False), sa.Column("item_kind", sa.String(16)), sa.Column("item_label", sa.String(200)),
        sa.Column("item_required", sa.Boolean()), sa.Column("item_minutes", sa.Integer()), _fk("proposed_by_id", "users.id"), _at("proposed_at"),
        sa.CheckConstraint("item_kind IS NULL OR item_kind IN ('material','instrument','document','action')", name="ck_cabinet_particularity_kind"),
        sa.CheckConstraint("(item_kind IS NULL) = (item_label IS NULL) AND (item_kind IS NULL) = (item_required IS NULL) "
                           "AND (item_kind IS NULL) = (item_minutes IS NULL)", name="ck_cabinet_particularity_item"),
        sa.CheckConstraint("item_minutes IS NULL OR item_minutes BETWEEN 0 AND 240", name="ck_cabinet_particularity_minutes"))
    op.create_table("cabinet_particularity_decisions", _id(), _clinic(), _fk("particularity_id", "cabinet_particularities.id"),
        sa.Column("decision", sa.String(16), nullable=False), sa.Column("reason", sa.String(500)), _fk("decided_by_id", "users.id"), _at("decided_at"),
        sa.UniqueConstraint("particularity_id", name="uq_cabinet_particularity_decision"),
        sa.CheckConstraint("decision IN ('approved','rejected')", name="ck_cabinet_particularity_decision"))
    op.create_table("cabinet_preparation_items", _id(), _clinic(), _fk("preparation_id", "cabinet_preparations.id"),
        sa.Column("position", sa.Integer(), nullable=False), _fk("checklist_version_id", "cabinet_checklist_versions.id", nullable=True),
        _fk("particularity_id", "cabinet_particularities.id", nullable=True), _fk("procedure_catalog_id", "procedure_catalog.id", nullable=True),
        sa.Column("procedure_name", sa.String(200)), sa.Column("kind", sa.String(16), nullable=False), sa.Column("label", sa.String(200), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False), sa.Column("prep_minutes", sa.Integer(), nullable=False),
        sa.UniqueConstraint("preparation_id", "position", name="uq_cabinet_preparation_item_position"),
        sa.CheckConstraint(KIND_CHECK, name="ck_cabinet_preparation_item_kind"),
        sa.CheckConstraint("(checklist_version_id IS NULL) <> (particularity_id IS NULL)", name="ck_cabinet_preparation_item_source"))
    op.create_table("cabinet_confirmation_events", _id(), _clinic(), _fk("preparation_item_id", "cabinet_preparation_items.id"),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("action", sa.String(16), nullable=False), sa.Column("reason", sa.String(500)),
        _fk("actor_id", "users.id"), _at("occurred_at"),
        sa.UniqueConstraint("preparation_item_id", "sequence", name="uq_cabinet_confirmation_sequence"),
        sa.CheckConstraint("action IN ('confirm','revoke')", name="ck_cabinet_confirmation_action"),
        sa.CheckConstraint("action = 'confirm' OR reason IS NOT NULL", name="ck_cabinet_confirmation_reason"))
    op.create_table("cabinet_manual_blocks", _id(), _clinic(), _fk("appointment_id", "appointments.id"),
        sa.Column("reason", sa.String(500), nullable=False), sa.Column("check_with", sa.String(200), nullable=False),
        _fk("created_by_id", "users.id"), _at("created_at"))
    op.create_table("cabinet_manual_block_closures", _id(), _clinic(), _fk("block_id", "cabinet_manual_blocks.id"),
        sa.Column("reason", sa.String(500), nullable=False), _fk("closed_by_id", "users.id"), _at("closed_at"),
        sa.UniqueConstraint("block_id", name="uq_cabinet_manual_block_closure"))
    op.create_table("cabinet_request_keys", _id(), _clinic(), sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False), sa.Column("response", sa.Text(), nullable=False),
        _fk("actor_id", "users.id"), _at("created_at"),
        sa.UniqueConstraint("clinic_id", "client_request_id", name="uq_cabinet_request_key"))
    for table in TABLES:
        if table != "cabinet_request_keys":
            op.create_index(f"ix_{table}_clinic_id", table, ["clinic_id"])
    op.create_index("ix_cabinet_request_keys_created", "cabinet_request_keys", ["clinic_id", "created_at"])
    op.create_index("ix_cabinet_checklist_items_version_id", "cabinet_checklist_items", ["version_id"])
    op.create_index("ix_cabinet_preparations_appointment_id", "cabinet_preparations", ["appointment_id"])
    op.create_index("ix_cabinet_particularities_appointment_id", "cabinet_particularities", ["appointment_id"])
    op.create_index("ix_cabinet_preparation_items_preparation_id", "cabinet_preparation_items", ["preparation_id"])
    op.create_index("ix_cabinet_confirmation_events_preparation_item_id", "cabinet_confirmation_events", ["preparation_item_id"])
    op.create_index("ix_cabinet_manual_blocks_appointment_id", "cabinet_manual_blocks", ["appointment_id"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
