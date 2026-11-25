"""Wave 2 merge: H2 (t13_finance_immutable), T19 (t19_team_shifts) and T21 (t21_equipment). No operations."""

revision = "w2_merge_h2_t19_t21"
down_revision = ("t13_finance_immutable", "t19_team_shifts", "t21_equipment")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
