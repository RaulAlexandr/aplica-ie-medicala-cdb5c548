"""T34 authentication protection, patient access log, and session metadata."""

import sqlalchemy as sa

from alembic import op

revision = "t34_security"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("refresh_tokens", sa.Column("session_id", sa.Uuid(), nullable=True))
    op.add_column("refresh_tokens", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE refresh_tokens SET session_id = id WHERE session_id IS NULL")
    op.execute("UPDATE refresh_tokens SET last_used_at = created_at WHERE last_used_at IS NULL")
    op.alter_column("refresh_tokens", "session_id", nullable=False)
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])
    op.create_table("login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("email", sa.String(320), nullable=False),
        sa.Column("ip_address", sa.String(64)), sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False))
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"])
    op.create_table("login_unlocks",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("email", sa.String(320), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_login_unlocks_email", "login_unlocks", ["email"])
    op.create_index("ix_login_unlocks_unlocked_at", "login_unlocks", ["unlocked_at"])
    op.create_table("patient_access_log",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("clinic_id", sa.Uuid(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("clinic_id", "actor_id", "patient_id", "occurred_at"):
        op.create_index(f"ix_patient_access_log_{column}", "patient_access_log", [column])
    op.create_index("ix_patient_access_clinic_date", "patient_access_log", ["clinic_id", "occurred_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
          CREATE FUNCTION reject_security_history_mutation() RETURNS trigger AS $$
          BEGIN RAISE EXCEPTION 'security history is append-only'; END;
          $$ LANGUAGE plpgsql;
          CREATE TRIGGER patient_access_log_immutable BEFORE UPDATE OR DELETE ON patient_access_log
          FOR EACH ROW EXECUTE FUNCTION reject_security_history_mutation();
          CREATE TRIGGER login_attempts_immutable BEFORE UPDATE OR DELETE ON login_attempts
          FOR EACH ROW EXECUTE FUNCTION reject_security_history_mutation();
          CREATE TRIGGER login_unlocks_immutable BEFORE UPDATE OR DELETE ON login_unlocks
          FOR EACH ROW EXECUTE FUNCTION reject_security_history_mutation();
        """)
    else:
        for table in ("patient_access_log", "login_attempts", "login_unlocks"):
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'security history is append-only'); END")
            op.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'security history is append-only'); END")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS patient_access_log_immutable ON patient_access_log")
        op.execute("DROP TRIGGER IF EXISTS login_attempts_immutable ON login_attempts")
        op.execute("DROP TRIGGER IF EXISTS login_unlocks_immutable ON login_unlocks")
        op.execute("DROP FUNCTION IF EXISTS reject_security_history_mutation()")
    else:
        for table in ("patient_access_log", "login_attempts", "login_unlocks"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
    op.drop_index("ix_patient_access_clinic_date", table_name="patient_access_log")
    for column in ("clinic_id", "actor_id", "patient_id", "occurred_at"):
        op.drop_index(f"ix_patient_access_log_{column}", table_name="patient_access_log")
    op.drop_table("patient_access_log")
    op.drop_index("ix_login_unlocks_unlocked_at", table_name="login_unlocks")
    op.drop_index("ix_login_unlocks_email", table_name="login_unlocks")
    op.drop_table("login_unlocks")
    op.drop_index("ix_login_attempts_attempted_at", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "last_used_at")
    op.drop_column("refresh_tokens", "session_id")
    op.drop_column("users", "password_changed_at")
