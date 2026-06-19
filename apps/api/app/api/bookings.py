"""Booking endpoints (Phase 0: create + read public clinic)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from ..core.config import Settings
from ..core.db import TenantScope, session_scope
from ..domain import booking as domain
from ..integrations import whatsapp
from .deps import get_tenant, settings_dep
from .schemas import BookingCreate

router = APIRouter(tags=["bookings"])


@router.post("/bookings", status_code=201)
def create_booking(
    body: BookingCreate,
    request: Request,
    tenant: dict = Depends(get_tenant),
    settings: Settings = Depends(settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    trace_id = getattr(request.state, "trace_id", "")
    actor = {"type": "patient", "id": "public"}
    with session_scope() as db:
        scope = TenantScope(db, tenant["id"])
        view, created = domain.create_booking(
            scope, settings, payload=body.model_dump(),
            idempotency_key=idempotency_key, actor=actor,
        )
    # fire confirmation outside the txn; never fails the booking
    if created:
        whatsapp().send_template(
            tenant_id=tenant["id"], to_phone=body.contact_phone,
            template="booking_confirmed",
            params={"token": view["tokens"][0]["number"], "lang": tenant["languages"][0]},
        )
    status = 201 if created else 200
    return JSONResponse(status_code=status, content=view, headers={"X-Trace-Id": trace_id})
