"""Request dependencies: DB session + resolved tenant scope + auth (current user / roles)."""
from __future__ import annotations

from collections.abc import Iterator

import jwt
from fastapi import Depends, Header, Request

from ..core.config import Settings, get_settings
from ..core.db import SessionLocal, TenantScope
from ..core.errors import AppError
from ..core.security import decode_token
from ..core.tenancy import resolve_tenant


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """Decode the Bearer access token -> claims {sub, roles:[{role,tenant_id}], ...}."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AppError("unauthenticated", "Login required.", status=401)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise AppError("token_expired", "Session expired — please log in again.", status=401)
    except jwt.PyJWTError:
        raise AppError("invalid_token", "Invalid session token.", status=401)
    if claims.get("typ") != "access":
        raise AppError("invalid_token", "Not an access token.", status=401)
    return claims


def optional_current_user(authorization: str | None = Header(default=None)) -> dict | None:
    """Decoded claims if a valid Bearer token is present, else None (never raises)."""
    if not authorization:
        return None
    try:
        return get_current_user(authorization)
    except AppError:
        return None


def require_role(*roles: str):
    """Dependency factory: allow only users holding one of `roles` (any tenant)."""
    allowed = set(roles)

    def _dep(user: dict = Depends(get_current_user)) -> dict:
        held = {r.get("role") for r in (user.get("roles") or [])}
        if held.isdisjoint(allowed):
            raise AppError("forbidden", "You don't have access to this action.", status=403)
        return user

    return _dep


def get_tenant(request: Request, x_clinic_slug: str | None = Header(default=None)) -> dict:
    return resolve_tenant(request, x_clinic_slug)


# --- clinic-scoped staff authorization (hospital-specific pages) -----------------------------
# Every staff page is addressed per clinic: /appointments/<slug>/{slots,admin,users}. The page
# sends X-Clinic-Slug; this dependency resolves THAT clinic and authorizes the caller against it
# — a superadmin (platform admin) may act on any clinic; clinic staff only on their own. This is
# what keeps each hospital's slots/config/team isolated even though the code is shared.
STAFF_ROLES = ("clinic_admin", "doctor", "front_desk", "triage")


def require_clinic_staff(*roles: str):
    """Dependency factory: caller must be superadmin OR hold one of `roles` for the clinic named
    by X-Clinic-Slug. Returns {"tenant": <resolved clinic>, "user": <claims>}.
    Resolves the clinic with require_live=False so staff can set it up before go-live."""
    allowed = set(roles) or set(STAFF_ROLES)

    def _dep(request: Request, x_clinic_slug: str | None = Header(default=None),
             user: dict = Depends(get_current_user)) -> dict:
        tenant = resolve_tenant(request, x_clinic_slug, require_live=False)
        held = user.get("roles") or []
        if any(r.get("role") == "superadmin" for r in held):
            return {"tenant": tenant, "user": user}
        if any(r.get("role") in allowed and r.get("tenant_id") == tenant["id"] for r in held):
            return {"tenant": tenant, "user": user}
        raise AppError("forbidden", "You don't manage this clinic.", status=403)

    return _dep


def get_scope(db=Depends(get_db), tenant: dict = Depends(get_tenant)) -> TenantScope:
    return TenantScope(db, tenant["id"])


def settings_dep() -> Settings:
    return get_settings()
