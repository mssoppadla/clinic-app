"""Public clinic landing — ClinicPublic (queue count, avg wait, doctors) + localized labels."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request

from ..core.config import Settings
from ..core.db import TenantScope, session_scope
from ..core.tenancy import resolve_tenant
from ..domain import booking as domain
from ..integrations import bhashini
from ..models import Slot
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


@router.get("/clinics/{slug}/availability/days")
def availability_days(slug: str, request: Request, doctor: str = Query(...),
                      days: int = Query(default=7, ge=1, le=30)):
    """Upcoming days with free-slot counts — powers the date tabs (Today / Tomorrow / …)."""
    tenant = resolve_tenant(request)
    today = datetime.now().date()
    out = []
    with session_scope() as db:
        scope = TenantScope(db, tenant["id"])
        for i in range(days):
            d = today + timedelta(days=i)
            ds = d.isoformat()
            avail = sum(max(0, s.capacity - s.booked)
                        for s in scope.query(Slot).filter(Slot.doctor_id == doctor, Slot.date == ds)
                        if s.status == "open")
            label = "Today" if i == 0 else ("Tomorrow" if i == 1 else d.strftime("%a, %d %b"))
            out.append({"date": ds, "label": label, "available": avail})
    return {"doctor_id": doctor, "days": out}


@router.get("/clinics/{slug}/availability")
def availability(slug: str, request: Request, doctor: str = Query(...), date: str = Query(...)):
    """Open slots for a doctor on a date (UI groups them Morning/Afternoon/Evening)."""
    tenant = resolve_tenant(request)
    with session_scope() as db:
        scope = TenantScope(db, tenant["id"])
        slots = sorted((s for s in scope.query(Slot).filter(Slot.doctor_id == doctor, Slot.date == date)
                        if s.status == "open"), key=lambda s: s.start_ts)
        return {"doctor_id": doctor, "date": date, "slots": [
            {"id": s.id, "start": s.start_ts.isoformat(),
             "label": s.start_ts.strftime("%I:%M %p").lstrip("0"),
             "available": max(0, s.capacity - s.booked)}
            for s in slots if s.booked < s.capacity]}
