"""T31 imaging: STL and DICOM documents (new category and content types, extracted metadata)."""
import sqlalchemy as sa

from alembic import op

revision = "t31_imaging"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None

OLD_CATEGORY = "category IN ('photo', 'radiograph', 'document', 'other')"
NEW_CATEGORY = "category IN ('photo', 'radiograph', 'document', 'other', 'imaging')"
OLD_TYPE = "content_type IN ('image/jpeg', 'image/png', 'application/pdf')"
NEW_TYPE = "content_type IN ('image/jpeg', 'image/png', 'application/pdf', 'model/stl', 'application/dicom')"


def upgrade() -> None:
    op.add_column("clinical_documents", sa.Column("imaging_metadata", sa.JSON(), nullable=True))
    op.drop_constraint("ck_clinical_document_category", "clinical_documents", type_="check")
    op.create_check_constraint("ck_clinical_document_category", "clinical_documents", NEW_CATEGORY)
    op.drop_constraint("ck_clinical_document_type", "clinical_documents", type_="check")
    op.create_check_constraint("ck_clinical_document_type", "clinical_documents", NEW_TYPE)


def downgrade() -> None:
    # Imaging rows cannot satisfy the old constraints: refuse instead of deleting clinical files' metadata.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM clinical_documents WHERE category = 'imaging' "
               "OR content_type IN ('model/stl', 'application/dicom')) THEN "
               "RAISE EXCEPTION 'Archive or remove imaging documents before downgrading t31_imaging'; END IF; END $$")
    op.drop_constraint("ck_clinical_document_type", "clinical_documents", type_="check")
    op.create_check_constraint("ck_clinical_document_type", "clinical_documents", OLD_TYPE)
    op.drop_constraint("ck_clinical_document_category", "clinical_documents", type_="check")
    op.create_check_constraint("ck_clinical_document_category", "clinical_documents", OLD_CATEGORY)
    op.drop_column("clinical_documents", "imaging_metadata")
