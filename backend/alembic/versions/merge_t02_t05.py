"""Merge the T02 operational calendar and T05 clinical timeline branches."""

revision = "merge_t02_t05"
down_revision = ("0004_operational_calendar", "t05_add_clinical_notes_timeline")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
