"""Clinic sites and site ownership for rooms, equipment, and stock locations."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "t32b_locations"
down_revision = "w2_merge_a1"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "clinic_locations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("clinic_id", UUID, sa.ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("address", sa.String(300)),
        sa.Column("phone", sa.String(40)), sa.Column("timezone", sa.String(64)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("clinic_id", "name", name="uq_clinic_location_name"),
    )
    op.create_index("ix_clinic_locations_clinic_id", "clinic_locations", ["clinic_id"])
    op.execute("""
        INSERT INTO clinic_locations (id, clinic_id, name, is_active, created_at)
        SELECT gen_random_uuid(), id, 'Principal', TRUE, NOW() FROM clinics
    """)
    op.add_column("rooms", sa.Column("location_id", UUID, nullable=True))
    op.execute("""
        UPDATE rooms SET location_id = clinic_locations.id
        FROM clinic_locations WHERE clinic_locations.clinic_id = rooms.clinic_id
    """)
    op.alter_column("rooms", "location_id", nullable=False)
    op.create_foreign_key("fk_rooms_location_id", "rooms", "clinic_locations", ["location_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_rooms_location_id", "rooms", ["location_id"])
    op.add_column("equipment", sa.Column("location_id", UUID, sa.ForeignKey("clinic_locations.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_equipment_location_id", "equipment", ["location_id"])
    op.execute("""
        UPDATE equipment SET location_id = clinic_locations.id
        FROM clinic_locations WHERE clinic_locations.clinic_id = equipment.clinic_id
    """)
    op.add_column("inventory_locations", sa.Column("site_id", UUID, sa.ForeignKey("clinic_locations.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_inventory_locations_site_id", "inventory_locations", ["site_id"])
    op.execute("""
        UPDATE inventory_locations SET site_id = clinic_locations.id
        FROM clinic_locations WHERE clinic_locations.clinic_id = inventory_locations.clinic_id
    """)
    op.add_column("users", sa.Column("default_location_id", UUID, nullable=True))
    op.add_column("users", sa.Column("all_locations", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_foreign_key("fk_users_default_location_id", "users", "clinic_locations", ["default_location_id"], ["id"], ondelete="RESTRICT")
    op.execute("""
        UPDATE users SET default_location_id = clinic_locations.id
        FROM clinic_locations WHERE clinic_locations.clinic_id = users.clinic_id
    """)
    op.create_table("user_location_assignments",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("location_id", UUID, sa.ForeignKey("clinic_locations.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_location_assignments")
    op.drop_constraint("fk_users_default_location_id", "users", type_="foreignkey")
    op.drop_column("users", "all_locations")
    op.drop_column("users", "default_location_id")
    op.drop_index("ix_inventory_locations_site_id", table_name="inventory_locations")
    op.drop_column("inventory_locations", "site_id")
    op.drop_index("ix_equipment_location_id", table_name="equipment")
    op.drop_column("equipment", "location_id")
    op.drop_index("ix_rooms_location_id", table_name="rooms")
    op.drop_constraint("fk_rooms_location_id", "rooms", type_="foreignkey")
    op.drop_column("rooms", "location_id")
    op.drop_index("ix_clinic_locations_clinic_id", table_name="clinic_locations")
    op.drop_table("clinic_locations")
