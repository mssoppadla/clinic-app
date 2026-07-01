"""Booking endpoints: create (queue/slot), cancel, reschedule."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core.config import Settings
from ..core.db import TenantScope, session_scope
from ..domain import booking as domain
from ..domain import notifications
from .deps import STAFF_ROLES, get_tenant, require_clinic_staff, settings_dep
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
    # fire confirmation outside the txn; never fails the booking. The dispatcher resolves the
    # clinic's template + its proper params from the booking, sends, logs, and meters (once).
    if created:
        notifications.notify(event_type="booking_confirmed", tenant_id=tenant["id"],
                             to_phone=body.contact_phone, booking_id=view["id"])
    status = 201 if created else 200
    return JSONResponse(status_code=status, content=view, headers={"X-Trace-Id": trace_id})


class CancelIn(BaseModel):
    reason: str | None = None


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, body: CancelIn | None = None,
                   ctx: dict = Depends(require_clinic_staff(*STAFF_ROLES)),
                   settings: Settings = Depends(settings_dep)):
    """Cancel a booking — frees the slot, drops it from the queue, applies the refund hook."""
    with session_scope() as db:
        scope = TenantScope(db, ctx["tenant"]["id"])
        return domain.cancel_booking(scope, settings, booking_id=booking_id,
                                     actor={"type": "staff", "id": ctx["user"].get("sub")},
                                     reason=(body.reason if body else None))


class RescheduleIn(BaseModel):
    new_slot_id: str


@router.post("/bookings/{booking_id}/reschedule")
def reschedule_booking(booking_id: str, body: RescheduleIn,
                       ctx: dict = Depends(require_clinic_staff(*STAFF_ROLES)),
                       settings: Settings = Depends(settings_dep)):
    """Move a booking to a different slot (atomic: reserve new, release old)."""
    with session_scope() as db:
        scope = TenantScope(db, ctx["tenant"]["id"])
        return domain.reschedule_booking(scope, settings, booking_id=booking_id,
                                         new_slot_id=body.new_slot_id,
                                         actor={"type": "staff", "id": ctx["user"].get("sub")})
