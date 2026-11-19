"""Join the T08+T09 merge head with the additive T11 clinical documents migration."""

revision = "merge_pilot_t09_t11"
down_revision = ("merge_t08_t09_periodontal", "t11_add_clinical_documents")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parents add independent tables. The merge revision only joins history.
    pass


def downgrade() -> None:
    # Alembic downgrades each parent after this merge point.
    pass
