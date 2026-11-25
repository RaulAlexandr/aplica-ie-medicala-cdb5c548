"""Wave 4 merge: T22B, T15, T23, T34 and T25 heads. No operations."""

revision = "w4_merge_int8"
down_revision = ("t22_cabinet_ready", "t15_patient_comms", "t23_sterilization", "t34_security", "t25_automations")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
