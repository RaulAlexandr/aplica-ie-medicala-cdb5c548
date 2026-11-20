"""T12 versioned clinic intake forms."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t12_intake_forms"
down_revision = "t03_catalog_allowed_locations"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "form_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_form_templates_clinic_id", "form_templates", ["clinic_id"])
    op.create_table(
        "form_template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("form_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("definition", _json_type(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("template_id", "version_number", name="uq_form_template_version_number"),
        sa.CheckConstraint("version_number > 0", name="ck_form_template_version_positive"),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_form_template_version_status"),
    )
    op.create_index("ix_form_template_versions_clinic_id", "form_template_versions", ["clinic_id"])
    op.create_table(
        "form_template_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("form_template_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question_id", sa.String(80), nullable=False),
        sa.Column("section_id", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(24), nullable=False),
        sa.Column("definition", _json_type(), nullable=False),
        sa.UniqueConstraint("template_version_id", "question_id", name="uq_form_question_version_id"),
        sa.CheckConstraint("position >= 0", name="ck_form_question_position"),
        sa.CheckConstraint("question_type IN ('short_text', 'long_text', 'yes_no', 'single_choice', 'multiple_choice', 'date', 'number')", name="ck_form_question_type"),
    )
    op.create_index("ix_form_template_questions_clinic_id", "form_template_questions", ["clinic_id"])
    op.create_table(
        "patient_forms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("template_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("form_template_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("answers", _json_type(), nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("validated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("clarification_reason", sa.Text()),
        sa.CheckConstraint("source IN ('staff', 'patient')", name="ck_patient_form_source"),
        sa.CheckConstraint("status IN ('draft', 'submitted', 'validated', 'needs_clarification')", name="ck_patient_form_status"),
    )
    op.create_index("ix_patient_forms_clinic_id", "patient_forms", ["clinic_id"])
    op.create_index("ix_patient_forms_patient_id", "patient_forms", ["patient_id"])
    op.create_table(
        "patient_form_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("clinic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("patient_form_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patient_forms.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("answers", _json_type(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("validated_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("patient_form_id", "revision_number", name="uq_patient_form_revision_number"),
        sa.CheckConstraint("revision_number > 0", name="ck_patient_form_revision_positive"),
        sa.CheckConstraint("status IN ('draft', 'submitted', 'validated', 'needs_clarification')", name="ck_patient_form_revision_status"),
    )
    op.create_index("ix_patient_form_revisions_clinic_id", "patient_form_revisions", ["clinic_id"])
    op.create_index("ix_patient_form_revisions_patient_form_id", "patient_form_revisions", ["patient_form_id"])
    op.execute("""
    CREATE FUNCTION prevent_published_form_version_change() RETURNS trigger AS $$
    BEGIN
      IF OLD.status = 'published' THEN
        RAISE EXCEPTION 'published form versions are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_published_form_version_immutable
    BEFORE UPDATE OR DELETE ON form_template_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_published_form_version_change()
    """)
    op.execute("""
    CREATE FUNCTION prevent_published_form_question_change() RETURNS trigger AS $$
    DECLARE version_status text;
    DECLARE parent_version_id uuid;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        parent_version_id := OLD.template_version_id;
      ELSE
        parent_version_id := NEW.template_version_id;
      END IF;
      SELECT status INTO version_status FROM form_template_versions
        WHERE id = parent_version_id;
      IF version_status = 'published' THEN
        RAISE EXCEPTION 'questions in published form versions are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_published_form_question_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON form_template_questions
    FOR EACH ROW EXECUTE FUNCTION prevent_published_form_question_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_published_form_question_immutable ON form_template_questions")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_form_question_change()")
    op.execute("DROP TRIGGER IF EXISTS trg_published_form_version_immutable ON form_template_versions")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_form_version_change()")
    op.drop_index("ix_patient_form_revisions_patient_form_id", table_name="patient_form_revisions")
    op.drop_index("ix_patient_form_revisions_clinic_id", table_name="patient_form_revisions")
    op.drop_table("patient_form_revisions")
    op.drop_index("ix_patient_forms_patient_id", table_name="patient_forms")
    op.drop_index("ix_patient_forms_clinic_id", table_name="patient_forms")
    op.drop_table("patient_forms")
    op.drop_index("ix_form_template_versions_clinic_id", table_name="form_template_versions")
    op.drop_index("ix_form_template_questions_clinic_id", table_name="form_template_questions")
    op.drop_table("form_template_questions")
    op.drop_table("form_template_versions")
    op.drop_index("ix_form_templates_clinic_id", table_name="form_templates")
    op.drop_table("form_templates")
