"""SQLAlchemy ORM — Phase-0 subset of data_model_v1.sql.

Portable types only (String/Text/Integer/Boolean/JSON/DateTime) so the same models
run on SQLite (sandbox tests) and Postgres (real). UUIDs are generated in Python.
Postgres-specific concerns (jsonb, native uuid, Row-Level Security policies) are added
in the Alembic baseline migration for real envs; the app-layer tenant guard works everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, JSON, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .core.ids import new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)  # canary tenant
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    branding: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # Onboarding [C34]: a self-registered clinic is created NOT live; a provider approves
    # (go-live / override) before its hosted page accepts patients. Existing/seeded tenants
    # default go_live=True so current behaviour is unchanged [A27, A28].
    go_live: Mapped[bool] = mapped_column(Boolean, default=True)
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)


class User(Base):
    """Staff/doctor/admin/superadmin login. Identity is cross-tenant; clinic membership +
    role live in user_roles (superadmin has a role row with tenant_id=NULL). [AC1-AC18]"""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)  # WhatsApp OTP target
    status: Mapped[str] = mapped_column(String(20), default="active")     # active|revoked
    must_reset_password: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", "role", name="uq_user_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # NULL = superadmin
    role: Mapped[str] = mapped_column(String(30))  # superadmin|clinic_admin|doctor|front_desk|triage
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160))
    specialty: Mapped[str] = mapped_column(String(120), default="")
    fee_minor: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    doctor_id: Mapped[str] = mapped_column(String(36), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    label: Mapped[str] = mapped_column(String(60), default="")
    start_ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)  # doctor running late (F23)
    capacity: Mapped[int] = mapped_column(Integer, default=40)


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_patient_tenant_phone"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    phone: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str] = mapped_column(String(36))
    purpose: Mapped[str] = mapped_column(String(60), default="booking")
    version: Mapped[str] = mapped_column(String(20), default="v1")
    channel: Mapped[str] = mapped_column(String(20), default="web")
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BookingEvent(Base):
    """Append-only source of truth. Projections below are rebuildable from this."""
    __tablename__ = "booking_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    event_version: Mapped[int] = mapped_column(Integer, default=1)
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    actor: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    primary_patient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    doctor_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="online")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    party_size: Mapped[int] = mapped_column(Integer, default=1)
    fee_total_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)        # F36
    in_premises: Mapped[bool] = mapped_column(Boolean, default=False)      # F19
    payment_status: Mapped[str] = mapped_column(String(20), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BookingPatient(Base):
    __tablename__ = "booking_patients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    booking_id: Mapped[str] = mapped_column(String(36), index=True)
    patient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")


class Token(Base):
    __tablename__ = "tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    booking_patient_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    number: Mapped[str] = mapped_column(String(20))
    short_code: Mapped[str] = mapped_column(String(12), default="")  # F14 (QR/track link)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class QueueEntry(Base):
    __tablename__ = "queue_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    booking_patient_id: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer)
    eta_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="waiting")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_idem_tenant_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    key: Mapped[str] = mapped_column(String(120))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UsageEvent(Base):
    """Cost/usage metering seam — every external API call is metered per tenant (FinOps)."""
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(40))   # whatsapp | bhashini | llm | ...
    kind: Mapped[str] = mapped_column(String(40))        # message | translate | ...
    units: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class IntegrationConfig(Base):
    """Runtime, hot-reloadable integration settings (WhatsApp/Bhashini/...).
    scope='platform' for global provider creds; or a tenant_id for per-clinic overrides.
    Secrets (is_secret=True) are stored here but NEVER returned to the UI (masked).
    In prod the secret values should be backed by a secrets manager via the same interface."""
    __tablename__ = "integration_config"
    __table_args__ = (UniqueConstraint("scope", "provider", "key", name="uq_intcfg"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(40), default="platform")
    provider: Mapped[str] = mapped_column(String(40))     # whatsapp | bhashini
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
