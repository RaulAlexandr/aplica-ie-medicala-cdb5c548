"""T13 internal patient account: immutable financial movements (payment, refund, adjustments, reversal).

Additive only, from the consolidated pilot head (merge_pilot_t09_t11). No existing table or row is
changed. Services owed are a projection of completed T04 performed procedures and are not copied.
Not fiscal invoicing.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t13_add_patient_financial_entries"
down_revision = "merge_pilot_t09_t11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "patient_financial_entries",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("clinic_id", uuid, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", uuid, sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("appointment_id", uuid, sa.ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("reverses_entry_id", uuid, sa.ForeignKey("patient_financial_entries.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("created_by_id", uuid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clinic_id", "client_request_id", name="uq_financial_entry_client_request"),
        sa.CheckConstraint(
            "kind IN ('payment', 'refund', 'adjustment_credit', 'adjustment_debit', 'reversal')",
            name="ck_financial_entry_kind"),
        sa.CheckConstraint("amount > 0", name="ck_financial_entry_amount"),
        sa.CheckConstraint("currency = 'RON'", name="ck_financial_entry_currency"),
        sa.CheckConstraint(
            "(kind IN ('payment', 'refund') AND method IS NOT NULL AND method IN ('cash', 'card', 'transfer', 'other')) "
            "OR (kind NOT IN ('payment', 'refund') AND method IS NULL)",
            name="ck_financial_entry_method"),
        sa.CheckConstraint(
            "kind = 'payment' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="ck_financial_entry_reason"),
        sa.CheckConstraint(
            "(kind = 'reversal') = (reverses_entry_id IS NOT NULL)",
            name="ck_financial_entry_reversal_target"),
    )
    op.create_index("ix_patient_financial_entries_patient", "patient_financial_entries",
                    ["clinic_id", "patient_id", "created_at"])
    op.create_index("ix_patient_financial_entries_clinic_day", "patient_financial_entries", ["clinic_id", "created_at"])
    op.create_index("ix_patient_financial_entries_appointment", "patient_financial_entries", ["appointment_id"])
    op.create_index("uq_financial_entry_reversal_once", "patient_financial_entries", ["reverses_entry_id"],
                    unique=True, postgresql_where=sa.text("reverses_entry_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_financial_entry_reversal_once", table_name="patient_financial_entries")
    op.drop_index("ix_patient_financial_entries_appointment", table_name="patient_financial_entries")
    op.drop_index("ix_patient_financial_entries_clinic_day", table_name="patient_financial_entries")
    op.drop_index("ix_patient_financial_entries_patient", table_name="patient_financial_entries")
    op.drop_table("patient_financial_entries")
