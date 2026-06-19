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
from ..models import Doctor, Session as ClinicSession, Tenant
from .deps import require_role

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
        readiness = _readiness(db, tenant)
        log.info("onboarding.register slug=%s name=%s", slug, body.name)
        return {
            "slug": slug,
            "name": tenant.name,
            "status": tenant.status,
            "go_live": tenant.go_live,
            "hosted_page": f"/appointments/{slug}",
            "branding": tenant.branding or {},
            "readiness": readiness,
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
def add_doctor(slug: str, body: DoctorIn):
    """Add a doctor + a today session (slots) — completes the mandatory readiness step."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        doctor = Doctor(tenant_id=tenant.id, name=body.name.strip(),
                        specialty=(body.specialty or "").strip(),
                        fee_minor=int(round((body.fee_inr or 0) * 100)))
        db.add(doctor)
        db.flush()
        today = datetime.now(timezone.utc)
        session = ClinicSession(
            tenant_id=tenant.id, doctor_id=doctor.id, date=today.strftime("%Y-%m-%d"),
            label=(body.session_label or "Morning"),
            start_ts=today.replace(hour=9, minute=0, second=0, microsecond=0),
            capacity=body.capacity or get_settings().default_session_capacity,
        )
        db.add(session)
        db.flush()
        log.info("onboarding.add_doctor slug=%s doctor=%s", slug, body.name)
        return {"doctor_id": doctor.id, "session_id": session.id,
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
def save_appearance(body: AppearanceIn):
    """Save on-screen appearance (colors, logo, text, header). Merges over existing."""
    with system_session() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == body.slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{body.slug}'", status=404)
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
        return {"slug": tenant.slug, "status": tenant.status, "go_live": tenant.go_live}
