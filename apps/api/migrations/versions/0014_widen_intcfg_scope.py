"""widen prefix:<uuid> string columns so Postgres stops 500ing — additive.
Revision ID: 0014_widen_intcfg_scope
Revises: 0013_message_templates

Two columns store a short prefix + a 36-char tenant UUID and overflowed their varchar. SQLite
ignores varchar length (tests + local passed); Postgres enforces it, so these paths 500'd only in
prod with StringDataRightTruncation:
  * integration_config.scope  = 'clinic:<uuid>'        (7 + 36 = 43) > varchar(40)
      -> every per-clinic config write: wa_shared / ai_enabled / confirm flags, per-clinic
         WhatsApp & Bhashini config. Hit by registering a clinic on the shared WhatsApp number.
  * otp_challenges.purpose     = 'patient_login:<uuid>' (14 + 36 = 50) > varchar(30)
      -> patient WhatsApp-OTP request (the booking 'send code' step).
Widening a varchar is a safe, backward-compatible expand: all existing rows preserved, no data
change. (The ORM models now declare the wider lengths; this brings existing prod DBs in line.)
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_widen_intcfg_scope"
down_revision = "0013_message_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("integration_config", "scope",
                    existing_type=sa.String(40), type_=sa.String(80),
                    existing_nullable=False)
    op.alter_column("otp_challenges", "purpose",
                    existing_type=sa.String(30), type_=sa.String(80),
                    existing_nullable=False)


def downgrade():
    op.alter_column("otp_challenges", "purpose",
                    existing_type=sa.String(80), type_=sa.String(30),
                    existing_nullable=False)
    op.alter_column("integration_config", "scope",
                    existing_type=sa.String(80), type_=sa.String(40),
                    existing_nullable=False)
