"""Wave 2 merge: previous wave-2 head (w2_merge_t18) and A1 (a1_appointment_procedures). No operations."""

revision = "w2_merge_a1"
down_revision = ("w2_merge_t18", "a1_appointment_procedures")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
