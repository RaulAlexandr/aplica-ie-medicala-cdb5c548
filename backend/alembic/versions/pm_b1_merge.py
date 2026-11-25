"""Join the B1 integration migration heads without schema operations."""

revision = "pm_b1_merge"
down_revision = (
    "t14_patient_portal",
    "t26_feedback_campaigns",
    "t32_import_export",
    "t34b_operations",
    "w4_merge_int8",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
