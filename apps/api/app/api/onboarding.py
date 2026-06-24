"""Clinic onboarding [C34] — self-serve registration + provider go-live approval.

Flow (matches docs mock 'Onboarding journey' and CONTRACT_UI_API_DB_v1.md):
  1. A clinic registers at tovaitech.in/appointments/onboard -> POST /onboarding/clinic
     creates a tenant (status=trial, go_live=FALSE) + captures the admin contact (account).
  2. Its hosted page tovaitech.in/appointments/<slug> exists but is NOT live (resolve_tenant
     gates on go_live) until a provider approves.
  3. Provider reviews pending clinics and approves go-live -> POST /onboarding/override
     (status=active, go_live=TRUE), audited.

NOTE: staff/provider auth is wired in Phase 2 (see api/admin.py). For now the provider
endpoints are unauthenticated in the skeleton; the approval page is unlinked. Full
account/users + WhatsApp embedded-signup + readiness automation are later onboarding steps.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.config import get_settings
from ..core.db import system_session
from ..core.errors import AppError
from ..core import slug as slugmod
from ..core.security import generate_temp_password, hash_password
from datetime import timedelta

from ..models import Doctor, Session as ClinicSession, Slot, Tenant, User, UserRole
from .deps import optional_current_user, require_role

# default working windows per session label, so onboarding creates REAL bookable timed slots
# (the same ones the Slots page + patient booking use), not just a queue session. [F7]
_SESSION_WINDOWS = {"morning": ("09:00", "12:00"), "afternoon": ("14:00", "17:00"),
                    "evening": ("18:00", "20:00")}
_DEFAULT_WINDOW = ("09:00", "12:00")
_ONBOARD_SLOT_MINUTES = 15


def _authz_if_live(tenant, caller: dict | None) -> None:
    """Pre-go-live: open self-serve (by slug). Once LIVE: only the clinic's own admin
    (clinic_admin of this tenant) or a superadmin may mutate it."""
    if not tenant.go_live:
        return
    roles = (caller or {}).get("roles") or []
    if any(r.get("role") == "superadmin" for r in roles):
        return
    if any(r.get("role") == "clinic_admin" and r.get("tenant_id") == tenant.id for r in roles):
        return
    if caller is None:
        raise AppError("unauthenticated", "Sign in as the clinic admin to change a live clinic.",
                       status=401)
    raise AppError("forbidden", "Only this clinic's admin can change it once it's live.", status=403)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
log = logging.getLogger("onboarding")

# Configurable appearance — every on-screen brand item a clinic can change so the booking
# widget (hosted OR embedded on their own site) matches their identity. Whitelisted so the
# branding blob can't be stuffed with arbitrary data.
APPEARANCE_KEYS = {
    "color", "accent", "logo", "headline", "tagline", "book_label", "show_header", "city",
    "hosting",   # "hosted" | "embed" | "both" — where patients book (drives the success screen)
}


def _clean_branding(raw: dict | None) -> dict:
    if not raw:
        return {}
    out = {}
    for k, v in raw.items():
        if k not in APPEARANCE_KEYS or v is None:
            continue
        out[k] = bool(v) if k == "show_header" else (str(v)[:500] if isinstance(v, str) else v)
    return out


# ---- helpers ---------------------------------------------------------------

def _unique_slug(db, desired: str) -> str:
    """First free slug at or after `desired` (desired, desired-2, desired-3, ...)."""
    base = desired
    n = 1
    candidate = base
    while db.query(Tenant).filter(Tenant.slug == candidate).first() is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _readiness(db, tenant: Tenant) -> dict:
    """Go-live readiness checklist (mandatory/optional), mirroring the mock."""
    has_doctor_with_slots = (
        db.query(Doctor.id)
        .join(ClinicSession, ClinicSession.doctor_id == Doctor.id)
        .filter(Doctor.tenant_id == tenant.id, Doctor.deleted_at.is_(None))
        .first()
        is not None
    )
    mandatory = [
        {"key": "name", "label": "Clinic name", "done": bool(tenant.name)},
        {"key": "channel", "label": "A booking channel (hosted page)", "done": True},
        {"key": "doctor_slots", "label": "At least one doctor with slots",
         "done": has_doctor_with_slots},
    ]
    optional = [
        {"key": "whatsapp", "label": "Connect WhatsApp", "done": False},
        {"key": "payments", "label": "Payment gateway", "done": False},
    ]
    mandatory_met = all(item["done"] for item in mandatory)
    return {
        "mandatory": mandatory,
        "optional": optional,
        "mandatory_met": mandatory_met,
        "go_live": tenant.go_live,
    }


# ---- schemas ---------------------------------------------------------------

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=40)
    slug: str | None = Field(default=None, max_length=80)
    languages: list[str] | None = None
    branding: dict | None = None
    # WhatsApp number source: False/None = clinic's own number; True = Tovaitech's shared number.
    use_shared_whatsapp: bool | None = None


class OverrideIn(BaseModel):
    slug: str
    reason: str | None = None


class AppearanceIn(BaseModel):
    slug: str
    branding: dict


class DoctorIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    specialty: str | None = Field(default=None, max_length=120)
    fee_inr: float | None = None
    session_label: str | None = Field(default=None, max_length=60)
    capacity: int | None = None


# ---- endpoints -------------------------------------------------------------

@router.get("/clinic/slug-available")
def slug_available(slug: str = ""):
    """Live availability check for the registration form."""
    normalized = slugmod.slugify(slug)
    if not slugmod.is_valid(normalized):
        return {"slug": slug, "normalized": normalized, "available": False,
                "reason": "reserved" if slugmod.is_reserved(normalized) else "invalid"}
    with system_session() as db:
        taken = db.query(Tenant).filter(Tenant.slug == normalized).first() is not None
    return {"slug": slug, "normalized": normalized, "available": not taken,
            "reason": "taken" if taken else None}


@router.post("/clinic", status_code=201)
def register_clinic(body: RegisterIn):
    """Create a tenant + account; NOT live until a provider approves go-live."""
    desired = slugmod.slugify(body.slug or body.name)
    if not desired:
        raise AppError("invalid_slug", "Could not derive a valid URL from the clinic name.",
                       status=422)
    if slugmod.is_reserved(desired):
        raise AppError("reserved_slug", f"'{desired}' is reserved; choose another URL.",
                       status=422)
    languages = body.languages or ["en"]
    if "en" not in languages:           # English always present [A15]
        languages = ["en", *languages]
    with system_session() as db:
        slug = _unique_slug(db, desired)
        tenant = Tenant(
            slug=slug, name=body.name.strip(), status="trial", go_live=False,
            is_synthetic=False, languages=languages, branding=_clean_branding(body.branding),
            contact_name=body.contact_name, contact_email=body.contact_email,
            contact_phone=body.contact_phone,
        )
        db.add(tenant)
        db.flush()
        tid = tenant.id
        readiness = _readiness(db, tenant)
        log.info("onboarding.register slug=%s name=%s shared_wa=%s", slug, body.name,
                 bool(body.use_shared_whatsapp))
    from ..core import integration_config as _cfg
    from ..domain.whatsapp_routing import deep_link as _deep_link
    if body.use_shared_whatsapp:
        _cfg.set_clinic_flag(tid, "wa_shared", True)
    return {
        "slug": slug, "name": body.name.strip(), "status": "trial", "go_live": False,
        "hosted_page": f"/appointments/{slug}",
        "branding": _clean_branding(body.branding),
        "readiness": readiness,
        "whatsapp": {"mode": "shared" if body.use_shared_whatsapp else "own",
                     "deep_link": _deep_link(slug) if body.use_shared_whatsapp else None},
    }


@router.get("/status")
def onboarding_status(slug: str):
    """Readiness per step + go-live, for the clinic's own onboarding view."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        return {"slug": tenant.slug, "name": tenant.name, "status": tenant.status,
                **_readiness(db, tenant)}


@router.get("/clinics")
def list_clinics(_: dict = Depends(require_role("superadmin")), live: bool = True):
    """Provider view: clinics (live by default) — powers the 'assign to clinic' picker."""
    with system_session() as db:
        q = db.query(Tenant).filter(Tenant.is_synthetic.is_(False))
        if live:
            q = q.filter(Tenant.go_live.is_(True))
        rows = q.order_by(Tenant.name).all()
        from ..core import integration_config as _cfg
        return {"clinics": [{"tenant_id": t.id, "slug": t.slug, "name": t.name,
                             "status": t.status, "go_live": t.go_live,
                             "ai_enabled": _cfg.get_clinic_flag(t.id, "ai_enabled", False)}
                            for t in rows]}


@router.get("/pending")
def pending_clinics(_: dict = Depends(require_role("superadmin"))):
    """Provider view: clinics awaiting go-live approval (superadmin only)."""
    with system_session() as db:
        rows = db.query(Tenant).filter(Tenant.go_live.is_(False)).all()
        return {"pending": [
            {"slug": t.slug, "name": t.name, "status": t.status,
             "contact_name": t.contact_name, "contact_email": t.contact_email,
             "contact_phone": t.contact_phone, "readiness": _readiness(db, t)}
            for t in rows
        ]}


@router.post("/clinic/{slug}/doctor", status_code=201)
def add_doctor(slug: str, body: DoctorIn, caller: dict | None = Depends(optional_current_user)):
    """Add a doctor + a today session (slots) — completes the mandatory readiness step.
    Open during onboarding; once the clinic is live, only its admin/superadmin may add."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        _authz_if_live(tenant, caller)
        doctor = Doctor(tenant_id=tenant.id, name=body.name.strip(),
                        specialty=(body.specialty or "").strip(),
                        fee_minor=int(round((body.fee_inr or 0) * 100)))
        db.add(doctor)
        db.flush()
        today = datetime.now(timezone.utc)
        date_str = today.strftime("%Y-%m-%d")
        session = ClinicSession(
            tenant_id=tenant.id, doctor_id=doctor.id, date=date_str,
            label=(body.session_label or "Morning"),
            start_ts=today.replace(hour=9, minute=0, second=0, microsecond=0),
            capacity=body.capacity or get_settings().default_session_capacity,
        )
        db.add(session)
        db.flush()
        # Generate REAL bookable timed slots for the session window so the doctor is immediately
        # bookable AND shows on the Slots management page (one consistent "slot" everywhere).
        start_s, end_s = _SESSION_WINDOWS.get((body.session_label or "").strip().lower(), _DEFAULT_WINDOW)
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        base = today.replace(hour=0, minute=0, second=0, microsecond=0)
        t = base.replace(hour=sh, minute=sm)
        end_dt = base.replace(hour=eh, minute=em)
        slots_made = 0
        while t + timedelta(minutes=_ONBOARD_SLOT_MINUTES) <= end_dt:
            e = t + timedelta(minutes=_ONBOARD_SLOT_MINUTES)
            db.add(Slot(tenant_id=tenant.id, doctor_id=doctor.id, session_id=session.id,
                        date=date_str, start_ts=t, end_ts=e, capacity=1, booked=0, status="open"))
            slots_made += 1
            t = e
        db.flush()
        log.info("onboarding.add_doctor slug=%s doctor=%s slots=%s", slug, body.name, slots_made)
        return {"doctor_id": doctor.id, "session_id": session.id, "slots_created": slots_made,
                "readiness": _readiness(db, tenant)}


@router.get("/appearance")
def get_appearance(slug: str):
    """Current appearance config for the configurator (not gated on go-live)."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        return {"slug": tenant.slug, "name": tenant.name,
                "languages": tenant.languages or ["en"], "branding": tenant.branding or {}}


@router.post("/appearance")
def save_appearance(body: AppearanceIn, caller: dict | None = Depends(optional_current_user)):
    """Save on-screen appearance. Open during onboarding; once live, clinic admin/superadmin only."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == body.slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{body.slug}'", status=404)
        _authz_if_live(tenant, caller)
        merged = {**(tenant.branding or {}), **_clean_branding(body.branding)}
        tenant.branding = merged
        log.info("onboarding.appearance slug=%s keys=%s", body.slug, list(merged.keys()))
        return {"slug": tenant.slug, "branding": merged}


@router.post("/override")
def approve_go_live(body: OverrideIn, _: dict = Depends(require_role("superadmin"))):
    """Provider approves / force go-live (audited; superadmin only)."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == body.slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{body.slug}'", status=404)
        tenant.go_live = True
        tenant.status = "active"
        log.info("onboarding.override slug=%s reason=%s -> go_live", body.slug, body.reason or "")

        # Auto-create the clinic's first clinic_admin from the registration contact email
        # (temp password, force-reset). Surfaced once so the provider can share it.
        created_admin = None
        email = (tenant.contact_email or "").strip().lower()
        has_admin = db.query(UserRole).filter(UserRole.tenant_id == tenant.id,
                                              UserRole.role == "clinic_admin").first() is not None
        if email and "@" in email and not has_admin:
            u = db.query(User).filter(User.email == email).first()
            if u is None:
                temp = generate_temp_password()
                u = User(email=email, phone=tenant.contact_phone, password_hash=hash_password(temp),
                         must_reset_password=True, status="active")
                db.add(u)
                db.flush()
                created_admin = {"email": email, "temp_password": temp}
            db.add(UserRole(user_id=u.id, tenant_id=tenant.id, role="clinic_admin"))

        return {"slug": tenant.slug, "status": tenant.status, "go_live": tenant.go_live,
                "clinic_admin": created_admin}


@router.post("/clinic/{slug}/admin-credentials")
def reset_clinic_admin_credentials(slug: str, _: dict = Depends(require_role("superadmin"))):
    """Platform-admin recovery: (re)generate and REVEAL a working clinic-admin login for a clinic.
    Passwords are stored hashed and can't be read back, so this issues a fresh temporary password
    (forced reset on next login). Superadmin only. Other staff/doctor logins are created elsewhere."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        role = db.query(UserRole).filter(UserRole.tenant_id == tenant.id,
                                         UserRole.role == "clinic_admin").first()
        if role is not None:
            u = db.query(User).filter(User.id == role.user_id).first()
        else:
            email = (tenant.contact_email or "").strip().lower()
            if not (email and "@" in email):
                raise AppError("no_contact_email",
                               "This clinic has no contact email to create an admin from.", status=422)
            u = db.query(User).filter(User.email == email).first()
            if u is None:
                u = User(email=email, phone=tenant.contact_phone, password_hash="",
                         status="active", must_reset_password=True)
                db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=tenant.id, role="clinic_admin"))
        temp = generate_temp_password()
        u.password_hash = hash_password(temp)
        u.must_reset_password = True
        u.status = "active"
        log.info("onboarding.reset_clinic_admin slug=%s", slug)
        return {"slug": slug, "clinic_admin": {"login": u.email or u.username, "temp_password": temp}}
