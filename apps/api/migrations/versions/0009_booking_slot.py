"""add bookings.slot_id (link a slot booking to its slot, for cancel/reschedule) — additive.
Revision ID: 0009_booking_slot
Revises: 0008_slots
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_booking_slot"
down_revision = "0008_slots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bookings", sa.Column("slot_id", sa.String(36), nullable=True))


def downgrade():
    op.drop_column("bookings", "slot_id")
