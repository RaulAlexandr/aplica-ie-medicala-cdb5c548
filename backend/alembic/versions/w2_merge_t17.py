"""Wave 2 merge: previous wave-2 head (w2_merge_h2_t19_t21) and T17 (t17_inventory). No operations."""

revision = "w2_merge_t17"
down_revision = ("w2_merge_h2_t19_t21", "t17_inventory")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
