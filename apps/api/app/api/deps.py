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


def get_scope(db=Depends(get_db), tenant: dict = Depends(get_tenant)) -> TenantScope:
    return TenantScope(db, tenant["id"])


def settings_dep() -> Settings:
    return get_settings()
