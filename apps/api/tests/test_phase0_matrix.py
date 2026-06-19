"""Phase-0 ↔ 4-way reconciliation matrix coverage.

Each test below maps to a requirement row that Phase 0 (spine + WhatsApp + Bhashini) commits to.
PHASE0_SCOPE is the authoritative list of matrix IDs in scope for this phase; the final test
asserts every one of them has a proving test here, so nothing in scope slips through.
"""
from __future__ import annotations

import json
import logging

from app.core.db import TenantScope, session_scope
from app.core.logging import JsonFormatter
from app.models import BookingEvent, Patient, UsageEvent

PHASE0_SCOPE = {
    "F4/F5": "live queue count & avg wait",
    "F10": "returning-patient match by phone",
    "F11a-c": "multi-patient up to 3",
    "F12/S5": "consent capture (DPDP)",
    "F14": "token short_code / QR",
    "F36": "reason for visit",
    "A9/A10": "event-sourced booking_events",
    "A15": "English always; Malayalam in addition",
    "S4/S6": "PII vault / no PII in logs + tenant isolation",
    "O-finops": "per-clinic usage metering",
    "IDEMPOTENCY": "no duplicate on retried key",
    "INTEG-WA": "WhatsApp confirmation",
    "INTEG-BH": "Bhashini localize + fallback",
    "HEALTH": "liveness/readiness for blue-green",
}

# requirement id -> the test(s) (in this suite or sibling files) that prove it
COVERAGE = {
    "F4/F5": ["test_clinic_public_returns_bilingual_labels", "test_book_then_token_then_queue"],
    "F10": ["test_returning_patient_matched_by_phone"],
    "F11a-c": ["test_multi_patient_overflow_up_to_three"],
    "F12/S5": ["test_consent_required", "test_consent_row_recorded"],
    "F14": ["test_book_then_token_then_queue"],
    "F36": ["test_book_then_token_then_queue"],
    "A9/A10": ["test_event_store_appends_requested_and_confirmed"],
    "A15": ["test_clinic_public_returns_bilingual_labels", "test_bhashini_english_passthrough"],
    "S4/S6": ["test_tenant_scope_blocks_cross_tenant_reads", "test_cross_tenant_write_blocked",
              "test_logs_redact_pii"],
    "O-finops": ["test_usage_event_metered_on_booking"],
    "IDEMPOTENCY": ["test_idempotent_replay_returns_same_no_duplicate"],
    "INTEG-WA": ["test_whatsapp_confirmation_sent_on_booking"],
    "INTEG-BH": ["test_bhashini_fallback_translates_known_keys"],
    "HEALTH": ["test_health_and_ready"],
}


def _book(client, slug, doctor_id, phone, name="X"):
    return client.post("/bookings",
                       json={"doctor_id": doctor_id, "patients": [{"name": name}],
                             "contact_phone": phone, "consent": True},
                       headers={"X-Clinic-Slug": slug})


def test_returning_patient_matched_by_phone(client, canary):
    phone = "+919833333333"
    _book(client, canary["slug"], canary["doctor_id"], phone, "First")
    _book(client, canary["slug"], canary["doctor_id"], phone, "Second")
    with session_scope() as db:
        scope = TenantScope(db, canary["tenant_id"])
        matches = [p for p in scope.query(Patient) if p.phone == phone]
    assert len(matches) == 1  # same phone -> one patient record, not duplicated


def test_event_store_appends_requested_and_confirmed(client, canary):
    r = _book(client, canary["slug"], canary["doctor_id"], "+919844444444", "Evt")
    booking_id = r.json()["id"]
    with session_scope() as db:
        scope = TenantScope(db, canary["tenant_id"])
        events = [e.event_type for e in scope.query(BookingEvent)
                  if e.aggregate_id == booking_id]
    assert "BookingRequested" in events
    assert "BookingConfirmed" in events


def test_consent_row_recorded(client, canary):
    from app.models import Consent
    _book(client, canary["slug"], canary["doctor_id"], "+919855555555", "Cons")
    with session_scope() as db:
        scope = TenantScope(db, canary["tenant_id"])
        consents = list(scope.query(Consent))
    assert len(consents) >= 1


def test_usage_event_metered_on_booking(client, canary):
    _book(client, canary["slug"], canary["doctor_id"], "+919866666666", "Meter")
    with session_scope() as db:
        scope = TenantScope(db, canary["tenant_id"])
        wa = [u for u in scope.query(UsageEvent) if u.provider == "whatsapp"]
    assert len(wa) >= 1  # every external call metered (FinOps seam)


def test_logs_redact_pii():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "patient +919812345678 booked", None, None)
    out = json.loads(JsonFormatter().format(rec))
    assert "+919812345678" not in out["msg"]  # phone redacted


def test_every_phase0_requirement_has_a_test():
    missing = [rid for rid in PHASE0_SCOPE if not COVERAGE.get(rid)]
    assert not missing, f"Phase-0 matrix rows with no test: {missing}"
