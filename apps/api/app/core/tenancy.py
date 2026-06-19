"""Server-side tenant resolution.

In prod the tenant context comes from the public URL (host / clinic-slug routing) - the
hosted page at tovaitech.in/appointments/<slug> sets it. For the API we accept it via the
X-Clinic-Slug header (set by the web tier) and VALIDATE it against the tenants table. It is a
public context, never a scoping value the client can forge to reach another tenant's rows -
the resolved tenant_id is what scopes all data, and Postgres RLS backs it up.
"""
from __future__ import annotations

from fastapi import Request

from .config import get_settings
from .db import session_scope
from .errors import AppError
from ..models import Tenant


def resolve_tenant(request: Request, x_clinic_slug: str | None = None) -> dict:
    """Plain resolver (no FastAPI Header default so it is safe to call directly).
    The dependency layer (api/deps.py) supplies the header value."""
    settings = get_settings()
    slug = x_clinic_slug or request.path_params.get("slug") or settings.canary_slug
    with session_scope() as db:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        if tenant is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        if tenant.status not in ("active", "trial"):
            raise AppError("tenant_inactive", "Clinic is not active", status=403)
        # Onboarding [C34]: a self-registered clinic exists but is not live to patients
        # until a provider approves go-live. Its hosted page/booking are gated here.
        if not tenant.go_live:
            raise AppError("clinic_not_live",
                           "This clinic is being set up and will be live soon.", status=403)
        return {
            "id": tenant.id,
            "slug": tenant.slug,
            "name": tenant.name,
            "languages": tenant.languages or ["en"],
            "branding": tenant.branding or {},
        }
