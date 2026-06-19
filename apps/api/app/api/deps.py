"""Request dependencies: DB session + resolved tenant scope."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, Request

from ..core.config import Settings, get_settings
from ..core.db import SessionLocal, TenantScope
from ..core.tenancy import resolve_tenant


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_tenant(request: Request, x_clinic_slug: str | None = Header(default=None)) -> dict:
    return resolve_tenant(request, x_clinic_slug)


def get_scope(db=Depends(get_db), tenant: dict = Depends(get_tenant)) -> TenantScope:
    return TenantScope(db, tenant["id"])


def settings_dep() -> Settings:
    return get_settings()
