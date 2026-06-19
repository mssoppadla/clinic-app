"""Public clinic landing — ClinicPublic (queue count, avg wait, doctors) + localized labels."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..core.config import Settings
from ..core.db import TenantScope, session_scope
from ..core.tenancy import resolve_tenant
from ..domain import booking as domain
from ..integrations import bhashini
from .deps import settings_dep

router = APIRouter(tags=["clinics"])

# UI label set the hosted page needs; English-always, Malayalam in addition when opted in.
LABELS_EN = {
    "Book appointment": "Book appointment",
    "Join today's queue": "Join today's queue",
    "in queue now": "in queue now",
    "avg wait": "avg wait",
    "Patient name": "Patient name",
    "Reason for visit": "Reason for visit",
    "Mobile number": "Mobile number",
    "Your token": "Your token",
}


@router.get("/clinics/{slug}")
def clinic_public(
    slug: str,
    request: Request,
    settings: Settings = Depends(settings_dep),
    lang: str | None = Query(default=None),
):
    tenant = resolve_tenant(request)
    with session_scope() as db:
        scope = TenantScope(db, tenant["id"])
        view = domain.clinic_public(scope, settings, tenant)

    # bilingual labels: English always; add Malayalam (or requested lang) if clinic opted in
    labels = {"en": LABELS_EN}
    for target in tenant["languages"]:
        if target == "en":
            continue
        labels[target] = bhashini().localize(tenant_id=tenant["id"], keys=LABELS_EN, target_lang=target)
    view["labels"] = labels
    return view
