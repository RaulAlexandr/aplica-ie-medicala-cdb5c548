"""Join the independent additive T08 and T09 feature migrations."""

revision = "merge_t08_t09_periodontal"
down_revision = ("t08_add_treatment_plans", "t09_add_periodontal_assessments")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parents add independent tables. The merge revision only joins history.
    pass


def downgrade() -> None:
    # Alembic downgrades each parent after this merge point.
    pass
