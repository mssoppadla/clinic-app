"""Booking domain — event-sourced.

booking_events is the source of truth; bookings / booking_patients / tokens / queue_entries
are projections rebuildable from it. Phase 0 supports join_queue mode only (slot mode lands in
Phase 1). Idempotency: a repeated Idempotency-Key returns the stored response, never a duplicate.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ..core.config import Settings
from ..core.db import TenantScope
from ..core.errors import AppError
from ..core.ids import new_id, short_code
from ..models import (Booking, BookingEvent, BookingPatient, Consent, Doctor,
                      IdempotencyKey, Patient, QueueEntry, Session, Slot, Token, UsageEvent)


def _append_event(scope: TenantScope, *, event_type: str, aggregate_id: str,
                  payload: dict, actor: dict, idem: str | None) -> None:
    scope.add(BookingEvent(
        event_id=new_id(), tenant_id=scope.tenant_id, event_type=event_type,
        aggregate_id=aggregate_id, payload=payload, actor=actor, idempotency_key=idem,
    ))


def _eta(session: Session, position: int, settings: Settings):
    minutes = (position - 1) * settings.avg_consult_minutes + (session.delay_minutes or 0)
    return session.start_ts + timedelta(minutes=minutes)


def _booking_view(scope: TenantScope, booking: Booking) -> dict:
    bps = list(scope.query(BookingPatient).filter(BookingPatient.booking_id == booking.id))
    tokens = []
    for bp in bps:
        tok = scope.get(Token, booking_patient_id=bp.id)
        qe = scope.get(QueueEntry, booking_patient_id=bp.id)
        tokens.append({
            "number": tok.number if tok else None,
            "patient_name": bp.name,
            "eta": qe.eta_ts.isoformat() if qe and qe.eta_ts else None,
            "provisional": tok.provisional if tok else False,
            "short_code": tok.short_code if tok else "",
        })
    return {
        "id": booking.id,
        "status": booking.status,
        "channel": booking.channel,
        "party_size": booking.party_size,
        "fee_total": {"amount_minor": booking.fee_total_minor, "currency": booking.currency},
        "reason": booking.reason,
        "in_premises": booking.in_premises,
        "tokens": tokens,
        "payment_status": booking.payment_status,
    }


def _active_session(scope: TenantScope, doctor_id: str) -> Session:
    session = scope.query(Session).filter(Session.doctor_id == doctor_id).first()
    if session is None:
        raise AppError("no_session", "Doctor has no open session today", status=409, retryable=False)
    return session


def create_booking(scope: TenantScope, settings: Settings, *, payload: dict,
                   idempotency_key: str | None, actor: dict) -> tuple[dict, bool]:
    """Returns (booking_view, created). created=False when replayed via idempotency."""
    # 1) idempotency replay
    if idempotency_key:
        existing = scope.get(IdempotencyKey, key=idempotency_key)
        if existing:
            return existing.response_json, False

    mode = payload.get("mode", "join_queue")
    if mode not in ("join_queue", "slot"):
        raise AppError("unsupported_mode", "mode must be join_queue or slot", status=400)

    patients = payload.get("patients") or []
    if not (1 <= len(patients) <= 3):
        raise AppError("invalid_party", "1 to 3 patients required", status=400,
                       field_errors=[{"field": "patients", "code": "range"}])
    if not payload.get("consent"):
        raise AppError("consent_required", "Consent is required (DPDP)", status=400,
                       field_errors=[{"field": "consent", "code": "required"}])

    doctor = scope.get(Doctor, id=payload["doctor_id"])
    if doctor is None:
        raise AppError("doctor_not_found", "Unknown doctor", status=404)

    # Resolve the target session + (for slot mode) atomically reserve capacity.
    slot = None
    if mode == "slot":
        slot_id = payload.get("slot_id")
        if not slot_id:
            raise AppError("slot_required", "Pick a time slot", status=400)
        # row-lock the slot so concurrent bookings can't oversell (FOR UPDATE on Postgres;
        # no-op on SQLite, where the app-layer check still guards single-threaded tests).
        slot = scope.db.execute(
            select(Slot).where(Slot.id == slot_id, Slot.tenant_id == scope.tenant_id).with_for_update()
        ).scalars().first()
        if slot is None or slot.status != "open":
            raise AppError("slot_not_found", "That slot is no longer available", status=404)
        if slot.doctor_id != doctor.id:
            raise AppError("slot_mismatch", "Slot belongs to a different doctor", status=400)
        if slot.booked + len(patients) > slot.capacity:
            raise AppError("slot_full", "This slot is no longer available", status=409, retryable=False)
        slot.booked += len(patients)
        session = scope.get(Session, id=slot.session_id)
    else:
        session = _active_session(scope, doctor.id)

    # 2) match/create patient by phone (returning-patient match, F10)
    phone = payload["contact_phone"]
    patient = scope.get(Patient, phone=phone)
    if patient is None:
        patient = Patient(tenant_id=scope.tenant_id, phone=phone, name=patients[0]["name"])
        scope.add(patient)
        scope.flush()
    scope.add(Consent(tenant_id=scope.tenant_id, patient_id=patient.id, channel="web"))

    # 3) projections
    booking = Booking(
        tenant_id=scope.tenant_id, primary_patient_id=patient.id, doctor_id=doctor.id,
        session_id=session.id, slot_id=(slot.id if slot else None),
        channel="online", status="confirmed",
        party_size=len(patients), fee_total_minor=doctor.fee_minor * len(patients),
        reason=payload.get("reason"),
    )
    scope.add(booking)
    scope.flush()

    _append_event(scope, event_type="BookingRequested", aggregate_id=booking.id,
                  payload={"mode": mode, "party_size": len(patients)}, actor=actor, idem=idempotency_key)

    base = sum(1 for _ in scope.query(QueueEntry).filter(QueueEntry.session_id == session.id))
    for i, p in enumerate(patients):
        bp = BookingPatient(tenant_id=scope.tenant_id, booking_id=booking.id,
                            patient_id=patient.id if i == 0 else None,
                            name=p["name"], reason=p.get("reason"))
        scope.add(bp)
        scope.flush()
        position = base + i + 1
        if mode == "slot":
            # scheduled appointment: token = slot time, ETA = slot start, not in the walk-in queue
            label = slot.start_ts.strftime("%I:%M %p").lstrip("0")
            number = label if len(patients) == 1 else f"{label} (+{i})"
            scope.add(Token(tenant_id=scope.tenant_id, booking_patient_id=bp.id, session_id=session.id,
                            number=number, short_code=short_code()))
            scope.add(QueueEntry(tenant_id=scope.tenant_id, session_id=session.id,
                                 booking_patient_id=bp.id, position=position,
                                 eta_ts=slot.start_ts, state="scheduled"))
        else:
            scope.add(Token(tenant_id=scope.tenant_id, booking_patient_id=bp.id, session_id=session.id,
                            number=f"A-{position}", short_code=short_code()))
            scope.add(QueueEntry(tenant_id=scope.tenant_id, session_id=session.id,
                                 booking_patient_id=bp.id, position=position,
                                 eta_ts=_eta(session, position, settings), state="waiting"))

    _append_event(scope, event_type="BookingConfirmed", aggregate_id=booking.id,
                  payload={"tokens": len(patients)}, actor=actor, idem=idempotency_key)

    # The WhatsApp confirmation (send + meter + log) is dispatched post-commit by the API layer
    # via domain.notifications.notify("booking_confirmed"), so it isn't re-sent on an idempotent replay.

    view = _booking_view(scope, booking)

    if idempotency_key:
        scope.add(IdempotencyKey(tenant_id=scope.tenant_id, key=idempotency_key, response_json=view))

    return view, True


def clinic_public(scope: TenantScope, settings: Settings, tenant: dict) -> dict:
    """ClinicPublic view: live queue count + avg wait + doctors."""
    waiting = [q for q in scope.query(QueueEntry) if q.state == "waiting"]
    queue_count = len(waiting)
    avg_wait = queue_count * settings.avg_consult_minutes
    doctors = [{
        "id": d.id, "name": d.name, "specialty": d.specialty,
        "fee": {"amount_minor": d.fee_minor, "currency": "INR"},
    } for d in scope.query(Doctor) if d.deleted_at is None]
    return {
        "slug": tenant["slug"],
        "name": tenant["name"],
        "branding": tenant["branding"],
        "languages": tenant["languages"],
        "queue_count": queue_count,
        "avg_wait_minutes": avg_wait,
        "doctors": doctors,
    }


def _refund_amount(settings: Settings, booking: Booking) -> int:
    """Configurable refund hook. Default: refund any captured fee on cancel. (No gateway yet,
    so for unpaid bookings this is 0; the hook + event seam are in place for Phase-payments.)"""
    if not getattr(settings, "refund_on_cancel", True):
        return 0
    return booking.fee_total_minor if booking.payment_status == "paid" else 0


def cancel_booking(scope: TenantScope, settings: Settings, *, booking_id: str,
                   actor: dict, reason: str | None = None) -> dict:
    booking = scope.get(Booking, id=booking_id)
    if booking is None:
        raise AppError("booking_not_found", "No such booking", status=404)
    if booking.status == "cancelled":
        return {**_booking_view(scope, booking), "refund_minor": 0}
    # free the reserved slot capacity
    if booking.slot_id:
        slot = scope.db.execute(
            select(Slot).where(Slot.id == booking.slot_id, Slot.tenant_id == scope.tenant_id).with_for_update()
        ).scalars().first()
        if slot is not None:
            slot.booked = max(0, slot.booked - booking.party_size)
    # drop the patients out of the queue
    for bp in scope.query(BookingPatient).filter(BookingPatient.booking_id == booking.id):
        qe = scope.get(QueueEntry, booking_patient_id=bp.id)
        if qe is not None:
            qe.state = "cancelled"
    booking.status = "cancelled"
    refund = _refund_amount(settings, booking)
    if refund > 0:
        booking.payment_status = "refunded"
        scope.add(UsageEvent(tenant_id=scope.tenant_id, provider="gateway", kind="refund", units=1,
                             meta={"booking_id": booking.id, "amount_minor": refund}))
    _append_event(scope, event_type="BookingCancelled", aggregate_id=booking.id,
                  payload={"reason": reason, "refund_minor": refund}, actor=actor, idem=None)
    return {**_booking_view(scope, booking), "refund_minor": refund}


def reschedule_booking(scope: TenantScope, settings: Settings, *, booking_id: str,
                       new_slot_id: str, actor: dict) -> dict:
    booking = scope.get(Booking, id=booking_id)
    if booking is None:
        raise AppError("booking_not_found", "No such booking", status=404)
    if booking.status == "cancelled":
        raise AppError("booking_cancelled", "A cancelled booking can't be rescheduled", status=409)
    new_slot = scope.db.execute(
        select(Slot).where(Slot.id == new_slot_id, Slot.tenant_id == scope.tenant_id).with_for_update()
    ).scalars().first()
    if new_slot is None or new_slot.status != "open":
        raise AppError("slot_not_found", "That slot is no longer available", status=404)
    if new_slot.doctor_id != booking.doctor_id:
        raise AppError("slot_mismatch", "Slot belongs to a different doctor", status=400)
    if new_slot.booked + booking.party_size > new_slot.capacity:
        raise AppError("slot_full", "This slot is no longer available", status=409, retryable=False)
    # reserve the new slot, release the old one
    new_slot.booked += booking.party_size
    if booking.slot_id and booking.slot_id != new_slot.id:
        old = scope.db.execute(
            select(Slot).where(Slot.id == booking.slot_id, Slot.tenant_id == scope.tenant_id).with_for_update()
        ).scalars().first()
        if old is not None:
            old.booked = max(0, old.booked - booking.party_size)
    booking.slot_id = new_slot.id
    booking.session_id = new_slot.session_id
    label = new_slot.start_ts.strftime("%I:%M %p").lstrip("0")
    for bp in scope.query(BookingPatient).filter(BookingPatient.booking_id == booking.id):
        tok = scope.get(Token, booking_patient_id=bp.id)
        if tok is not None:
            tok.number = label
            tok.session_id = new_slot.session_id
        qe = scope.get(QueueEntry, booking_patient_id=bp.id)
        if qe is not None:
            qe.session_id = new_slot.session_id
            qe.eta_ts = new_slot.start_ts
            qe.state = "scheduled"
    _append_event(scope, event_type="BookingRescheduled", aggregate_id=booking.id,
                  payload={"new_slot_id": new_slot.id}, actor=actor, idem=None)
    return _booking_view(scope, booking)
