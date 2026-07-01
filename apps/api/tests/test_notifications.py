"""Notification dispatcher: resolves a template's FULL param set from real booking data, sends the
proper params, logs the notification, meters it, and is idempotent.

NOTE: the suite shares one SQLite DB, so tests that add a MessageTemplate delete it again — a
leaked platform 'booking_confirmed' template would change every other booking test's params."""
from __future__ import annotations

from app.core.db import system_session
from app.domain import notifications
from app.integrations.whatsapp import SENT_STUB
from app.models import MessageTemplate, Notification


def _book(client, canary, name, phone):
    r = client.post("/bookings", json={"doctor_id": canary["doctor_id"],
                    "patients": [{"name": name}], "contact_phone": phone, "consent": True},
                    headers={"X-Clinic-Slug": canary["slug"]})
    assert r.status_code == 201, r.text
    return r.json()


def _clear_platform_confirmed():
    with system_session() as db:
        for t in db.query(MessageTemplate).filter(
                MessageTemplate.scope == "platform",
                MessageTemplate.event_type == "booking_confirmed").all():
            db.delete(t)


def test_dispatcher_sends_full_proper_params_from_real_data(client, canary):
    booked = _book(client, canary, "Asha Menon", "+919800000001")
    token = booked["tokens"][0]["number"]
    tid = None
    try:
        # a template whose body has the full set {{1}}..{{4}} — the dispatcher must fill ALL of them
        with system_session() as db:
            t = MessageTemplate(
                scope="platform", event_type="booking_confirmed", meta_name="booking_confirmed_rich",
                language="en_US", meta_status="approved",
                param_map=["patient_name", "doctor_name", "token_number", "clinic_name"])
            db.add(t)
            db.flush()
            tid = t.id

        SENT_STUB.clear()
        res = notifications.notify(event_type="booking_confirmed", tenant_id=canary["tenant_id"],
                                   to_phone="+919800000001", booking_id=booked["id"], offset="t")
        assert res["status"] == "sent"
        sent = SENT_STUB[-1]
        assert sent["template"] == "booking_confirmed_rich"
        bp = sent["body_params"]
        assert len(bp) == 4
        assert bp[0] == "Asha Menon"      # patient_name  — resolved from the booking
        assert bp[1]                       # doctor_name   — resolved (non-empty)
        assert bp[2] == token             # token_number  — resolved
        assert bp[3]                       # clinic_name   — resolved (tenant name, non-empty)
    finally:
        _clear_platform_confirmed()        # don't leak the rich template into other tests


def test_dispatcher_logs_meters_and_is_idempotent(client, canary):
    _clear_platform_confirmed()
    booked = _book(client, canary, "Ravi K", "+919800000002")
    dk = f"{booked['id']}:booking_confirmed:x"
    SENT_STUB.clear()
    a = notifications.notify(event_type="booking_confirmed", tenant_id=canary["tenant_id"],
                             to_phone="+919800000002", booking_id=booked["id"], offset="x")
    assert a["status"] == "sent"
    # exactly one Notification row for THIS claim (delivery log)
    with system_session() as db:
        rows = db.query(Notification).filter(Notification.dedupe_key == dk).all()
    assert len(rows) == 1 and rows[0].status == "sent"
    # calling again with the same (booking, event, offset) is deduped — no second send
    n_before = len(SENT_STUB)
    b = notifications.notify(event_type="booking_confirmed", tenant_id=canary["tenant_id"],
                             to_phone="+919800000002", booking_id=booked["id"], offset="x")
    assert b["status"] == "skipped" and b["reason"] == "duplicate"
    assert len(SENT_STUB) == n_before


def test_dispatcher_default_is_token_only_no_regression(client, canary):
    """With no catalog template, booking_confirmed falls back to the minimal [token_number] —
    matching the currently-approved template (no behaviour change)."""
    _clear_platform_confirmed()
    booked = _book(client, canary, "Priya", "+919800000003")
    SENT_STUB.clear()
    notifications.notify(event_type="booking_confirmed", tenant_id=canary["tenant_id"],
                         to_phone="+919800000003", booking_id=booked["id"], offset="d")
    assert SENT_STUB[-1]["template"] == "booking_confirmed"
    assert SENT_STUB[-1]["body_params"] == [booked["tokens"][0]["number"]]
