"""link a doctor's clinical profile to their login user (doctors.user_id) — additive.
A doctor's login and clinical profile are one and the same; user_id is NULL for ad-hoc/visiting
doctors with no login.
Revision ID: 0010_doctor_user
Revises: 0009_booking_slot
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_doctor_user"
down_revision = "0009_booking_slot"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("doctors", sa.Column("user_id", sa.String(36), nullable=True))
    op.create_index("ix_doctors_user_id", "doctors", ["user_id"])


def downgrade():
    op.drop_index("ix_doctors_user_id", table_name="doctors")
    op.drop_column("doctors", "user_id")
