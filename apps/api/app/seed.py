"""Idempotent seed of the __canary__ tenant (synthetic clinic for prod E2E smoke).

Idempotent: safe to run on every deploy; never duplicates rows. Creates tables on SQLite/local
when Alembic is not used (real envs use the Alembic baseline migration instead).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .core.config import get_settings
from .core.db import engine, system_session
from .core.security import hash_password
from .models import Base, Doctor, Session, Tenant, User, UserRole


def ensure_schema() -> None:
    Base.metadata.create_all(engine)


def seed_canary() -> dict:
    settings = get_settings()
    # system_session bypasses RLS: the seed creates the canary tenant + its doctor/session
    # before any per-request tenant context exists.
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == settings.canary_slug).first()
        if tenant is None:
            tenant = Tenant(
                slug=settings.canary_slug, name="Canary Clinic (synthetic)",
                status="active", is_synthetic=True, languages=settings.languages,
                branding={"color": "#0e7c66", "accent": "#2563eb", "city": "Kochi"},
            )
            db.add(tenant)
            db.flush()
        else:
            # keep canary aligned with the mock palette + configured languages
            tenant.branding = {"color": "#0e7c66", "accent": "#2563eb", "city": "Kochi"}
            tenant.languages = settings.languages
        doctor = db.query(Doctor).filter(Doctor.tenant_id == tenant.id).first()
        if doctor is None:
            doctor = Doctor(tenant_id=tenant.id, name="Dr. Canary", specialty="General Medicine",
                            fee_minor=30000)
            db.add(doctor)
            db.flush()
        session = db.query(Session).filter(Session.doctor_id == doctor.id).first()
        if session is None:
            today = datetime.now(timezone.utc)
            session = Session(tenant_id=tenant.id, doctor_id=doctor.id,
                              date=today.strftime("%Y-%m-%d"), label="Morning",
                              start_ts=today.replace(hour=9, minute=0, second=0, microsecond=0),
                              capacity=settings.default_session_capacity)
            db.add(session)
            db.flush()
        return {"tenant_id": tenant.id, "slug": tenant.slug,
                "doctor_id": doctor.id, "session_id": session.id}


def seed_superadmin() -> dict:
    """Create the root superadmin from env if absent (force reset on first login). Idempotent.
    Skipped if APP_SUPERADMIN_EMAIL/PASSWORD are not set (e.g. CI/tests)."""
    settings = get_settings()
    email = (settings.superadmin_email or "").strip().lower()
    if not email or not settings.superadmin_password:
        return {"superadmin": "skipped (no APP_SUPERADMIN_EMAIL/PASSWORD)"}
    with system_session() as db:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, password_hash=hash_password(settings.superadmin_password),
                        must_reset_password=True, status="active")
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, tenant_id=None, role="superadmin"))
            return {"superadmin": email, "created": True}
        return {"superadmin": email, "created": False}


if __name__ == "__main__":
    ensure_schema()
    print(seed_canary())
    print(seed_superadmin())
