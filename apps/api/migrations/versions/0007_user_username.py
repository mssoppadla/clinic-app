"""add users.username + make users.email optional — additive (expand).
Revision ID: 0007_user_username
Revises: 0006_otp_challenges

Login accepts email OR username (clinics without an official email use a username).
Postgres-only DDL; fresh DBs get this from the model via create_all.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_user_username"
down_revision = "0006_otp_challenges"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("username", sa.String(80), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.alter_column("users", "email", existing_type=sa.String(200), nullable=True)


def downgrade():
    op.alter_column("users", "email", existing_type=sa.String(200), nullable=False)
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
