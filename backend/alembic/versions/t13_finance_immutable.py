"""CUR-04: database-level immutability of T13 financial movements.

The application never updates or deletes a movement: a reversal is a new row of kind 'reversal'
(see app/finance/router.py). The trigger makes that a database guarantee, so raw SQL and any other
client are refused too. No legitimate UPDATE/DELETE exists, therefore none is allowed.
"""

from alembic import op

revision = "t13_finance_immutable"
down_revision = "t16_waitlist_recalls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION patient_financial_entries_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'patient_financial_entries is append-only: % is not allowed; record a reversal instead', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_patient_financial_entries_immutable
        BEFORE UPDATE OR DELETE ON patient_financial_entries
        FOR EACH ROW EXECUTE FUNCTION patient_financial_entries_immutable()
    """)
    op.execute("""
        CREATE TRIGGER trg_patient_financial_entries_no_truncate
        BEFORE TRUNCATE ON patient_financial_entries
        FOR EACH STATEMENT EXECUTE FUNCTION patient_financial_entries_immutable()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_patient_financial_entries_no_truncate ON patient_financial_entries")
    op.execute("DROP TRIGGER IF EXISTS trg_patient_financial_entries_immutable ON patient_financial_entries")
    op.execute("DROP FUNCTION IF EXISTS patient_financial_entries_immutable()")
