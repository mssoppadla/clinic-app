"""Booking domain — event-sourced.

booking_events is the source of truth; bookings / booking_patients / tokens / queue_entries
are projections rebuildable from it. Phase 0 supports join_queue mode only (slot mode lands in
Phase 1). Idempotency: a repeated Idempotency-Key returns the stored response, never a duplicate.
"""
from __future__ import annotations

from datetime import timedelta

from ..core.config import Settings
from ..core.db import TenantScope
from ..core.errors import AppError
from ..core.ids import new_id, short_code
from ..models import (Booking, BookingEvent, BookingPatient, Consent, Doctor,
                      IdempotencyKey, Patient, QueueEntry, Session, Token, UsageEvent)


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
    if mode != "join_queue":
        raise AppError("unsupported_mode", "Phase 0 supports join_queue only", status=400)

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
        session_id=session.id, channel="online", status="confirmed",
        party_size=len(patients), fee_total_minor=doctor.fee_minor * len(patients),
        reason=payload.get("reason"),
    )
    scope.add(booking)
    scope.flush()

    _append_event(scope, event_type="BookingRequested", aggregate_id=booking.id,
                  payload={"mode": mode, "party_size": len(patients)}, actor=actor, idem=idempotency_key)

    # next token number for this session (count existing + 1)
    base = sum(1 for _ in scope.query(QueueEntry).filter(QueueEntry.session_id == session.id))
    for i, p in enumerate(patients):
        bp = BookingPatient(tenant_id=scope.tenant_id, booking_id=booking.id,
                            patient_id=patient.id if i == 0 else None,
                            name=p["name"], reason=p.get("reason"))
        scope.add(bp)
        scope.flush()
        position = base + i + 1
        scope.add(Token(tenant_id=scope.tenant_id, booking_patient_id=bp.id, session_id=session.id,
                        number=f"A-{position}", short_code=short_code()))
        scope.add(QueueEntry(tenant_id=scope.tenant_id, session_id=session.id,
                             booking_patient_id=bp.id, position=position,
                             eta_ts=_eta(session, position, settings), state="waiting"))

    _append_event(scope, event_type="BookingConfirmed", aggregate_id=booking.id,
                  payload={"tokens": len(patients)}, actor=actor, idem=idempotency_key)

    # 4) meter the (stub) WhatsApp confirmation as a usage event (FinOps seam)
    scope.add(UsageEvent(tenant_id=scope.tenant_id, provider="whatsapp", kind="message", units=1,
                         meta={"template": "booking_confirmed"}))

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
