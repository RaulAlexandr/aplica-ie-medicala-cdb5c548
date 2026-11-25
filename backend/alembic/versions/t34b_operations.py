"""Allow bounded retention deletes for T34 security history."""

from alembic import op

revision = "t34b_operations"
down_revision = "t34_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
          CREATE OR REPLACE FUNCTION reject_security_history_mutation() RETURNS trigger AS $$
          DECLARE cutoff_text text;
          BEGIN
            IF TG_OP = 'DELETE' AND current_setting('dentacare.security_retention', true) = 'on' THEN
              IF TG_TABLE_NAME = 'login_attempts' THEN
                cutoff_text := current_setting('dentacare.login_attempt_cutoff', true);
                IF cutoff_text IS NOT NULL AND OLD.attempted_at < cutoff_text::timestamptz THEN RETURN OLD; END IF;
              ELSIF TG_TABLE_NAME = 'patient_access_log' THEN
                cutoff_text := current_setting('dentacare.patient_access_cutoff', true);
                IF cutoff_text IS NOT NULL AND OLD.occurred_at < cutoff_text::timestamptz THEN RETURN OLD; END IF;
              END IF;
            END IF;
            RAISE EXCEPTION 'security history is append-only outside bounded retention';
          END;
          $$ LANGUAGE plpgsql;
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
          CREATE OR REPLACE FUNCTION reject_security_history_mutation() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'security history is append-only'; END;
          $$ LANGUAGE plpgsql;
        """)
