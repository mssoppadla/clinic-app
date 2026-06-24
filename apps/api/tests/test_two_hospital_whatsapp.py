"""End-to-end two-hospital WhatsApp journey — NO seeded data at any stage. Everything (clinics,
doctors, slots, WhatsApp routing, AI toggle) is built through the real API, then patients are
driven entirely via signed inbound webhooks.

Clinic A runs the AI agent (superadmin-enabled); clinic B runs the deterministic menu. Both flows
are exercised independently, with cross-clinic isolation asserted. AI is in stub mode (no key),
so the AI path is deterministic; replies go to the WhatsApp stub.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

VERIFY, SECRET = "vtok", "app-secret"


@pytest.fixture()
def wa_env():
    import os
    from app.core.config import get_settings
    os.environ["APP_WHATSAPP_VERIFY_TOKEN"] = VERIFY
    os.environ["APP_WHATSAPP_APP_SECRET"] = SECRET
    get_settings.cache_clear()
    yield
    os.environ.pop("APP_WHATSAPP_VERIFY_TOKEN", None)
    os.environ.pop("APP_WHATSAPP_APP_SECRET", None)
    get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


_seq = [0]


def _say(client, pnid, phone, text, name="Pat"):
    """Deliver one inbound WhatsApp text and return the agent's reply (from the WA stub)."""
    from app.integrations.whatsapp import SENT_STUB
    _seq[0] += 1
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": pnid},
        "contacts": [{"wa_id": phone, "profile": {"name": name}}],
        "messages": [{"from": phone, "id": f"wamid.{_seq[0]}", "type": "text", "text": {"body": text}}],
    }}]}]}
    raw = json.dumps(payload).encode()
    before = len(SENT_STUB)
    r = client.post("/webhooks/whatsapp", content=raw,
                    headers={"X-Hub-Signature-256": _sign(raw), "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    out = [m for m in SENT_STUB[before:] if m.get("type") == "text" and m.get("to") == phone]
    return out[-1]["text"] if out else ""


def _setup_clinic(client, sa, name, email, pnid, ai):
    slug = client.post("/onboarding/clinic", json={"name": name, "contact_email": email}).json()["slug"]
    # real onboarding: a doctor + a today session + timed slots
    client.post(f"/onboarding/clinic/{slug}/doctor", json={"name": "Dr " + name, "session_label": "Morning"})
    client.post("/onboarding/override", headers=sa, json={"slug": slug})
    # route this clinic's WhatsApp number (superadmin acts on the clinic's integration config)
    client.put("/admin/integrations/whatsapp", headers={**sa, "X-Clinic-Slug": slug},
               json={"phone_number_id": pnid})
    if ai:
        assert client.post(f"/admin/platform/clinics/{slug}/ai", headers=sa,
                           json={"enabled": True}).status_code == 200
    tid = client.get("/users/clinic", headers={**sa, "X-Clinic-Slug": slug}).json()["tenant_id"]
    return {"slug": slug, "tenant_id": tid, "pnid": pnid}


def _bookings(tenant_id):
    from app.core.db import system_session
    from app.models import Booking
    with system_session() as db:
        return db.query(Booking).filter(Booking.tenant_id == tenant_id).all()


def test_two_hospital_whatsapp_journey(client, superadmin_headers, wa_env):
    A = _setup_clinic(client, superadmin_headers, "Apollo", "a@wa.test", "PNID-A", ai=True)   # AI agent
    B = _setup_clinic(client, superadmin_headers, "Bethel", "b@wa.test", "PNID-B", ai=False)  # menu

    # ---- Clinic B: deterministic MENU flow (AI off) -> join the queue ----
    menu = _say(client, "PNID-B", "+91900000001", "hi")
    assert "Bethel" in menu and "1." in menu and "queue" in menu.lower()
    ask = _say(client, "PNID-B", "+91900000001", "2")        # join today's queue
    assert "queue" in ask.lower() and "yes" in ask.lower()   # confirm-before-commit
    done = _say(client, "PNID-B", "+91900000001", "yes")
    assert "queue" in done.lower() and ("A-" in done or "token" in done.lower())
    assert len(_bookings(B["tenant_id"])) == 1               # exactly one booking, in clinic B

    # ---- Clinic A: AI flow (stub infers intent) -> book a slot ----
    propose = _say(client, "PNID-A", "+91900000002", "I would like to book an appointment")
    assert "book" in propose.lower() and "yes" in propose.lower()   # AI proposed, awaiting confirm
    booked = _say(client, "PNID-A", "+91900000002", "yes")
    assert "booked" in booked.lower() or "✅" in booked
    a_books = _bookings(A["tenant_id"])
    assert len(a_books) == 1 and a_books[0].slot_id is not None     # a SLOT booking

    # ---- AI read-only intent: queue status (no confirm step) ----
    status = _say(client, "PNID-A", "+91900000003", "what's the queue status?")
    assert "queue" in status.lower()
    # isolation: A's queue is independent of B's (B has 1 waiting; A's scheduled slot isn't a walk-in)
    assert "0 patient" in status

    # ---- isolation: each clinic's bookings stay in its own tenant ----
    assert len(_bookings(A["tenant_id"])) == 1 and len(_bookings(B["tenant_id"])) == 1


def _setup_shared_clinic(client, sa, name, email):
    """A clinic with NO number of its own — reaches patients via Tovaitech's shared number."""
    slug = client.post("/onboarding/clinic",
                       json={"name": name, "contact_email": email, "use_shared_whatsapp": True}).json()["slug"]
    client.post(f"/onboarding/clinic/{slug}/doctor", json={"name": "Dr " + name, "session_label": "Morning"})
    client.post("/onboarding/override", headers=sa, json={"slug": slug})
    tid = client.get("/users/clinic", headers={**sa, "X-Clinic-Slug": slug}).json()["tenant_id"]
    return {"slug": slug, "tenant_id": tid}


def test_shared_number_routes_by_clinic_code(client, superadmin_headers, wa_env):
    """Two clinics share ONE Tovaitech number; a deep-link code in the first message routes the
    patient to the right clinic, the binding is remembered, and a new code switches clinics."""
    C = _setup_shared_clinic(client, superadmin_headers, "Carewell", "c@shared.test")
    D = _setup_shared_clinic(client, superadmin_headers, "Downtown", "d@shared.test")
    SHARED = "TOVAITECH-SHARED"          # phone_number_id no clinic owns -> shared routing
    pt = "+91930000001"

    # deep-link first message -> clinic C
    assert "Carewell" in _say(client, SHARED, pt, f"Book at {C['slug']}")
    assert "yes" in _say(client, SHARED, pt, "2").lower()        # join queue -> confirm
    assert "queue" in _say(client, SHARED, pt, "yes").lower()    # committed
    assert len(_bookings(C["tenant_id"])) == 1 and len(_bookings(D["tenant_id"])) == 0

    # follow-up WITHOUT a code still routes to C (binding remembered)
    assert "Carewell" in _say(client, SHARED, pt, "hi")
    # a new code switches the same patient to clinic D
    assert "Downtown" in _say(client, SHARED, pt, f"book at {D['slug']}")
    # a brand-new patient with no code and no binding is asked for their clinic
    ask = _say(client, SHARED, "+91930000099", "hello")
    assert "tovaitech" in ask.lower() and "clinic" in ask.lower()


def test_menu_and_ai_are_independent(client, superadmin_headers, wa_env):
    """The same 'book' message yields the menu on a non-AI clinic and an AI proposal on an AI clinic."""
    A = _setup_clinic(client, superadmin_headers, "AIco", "ai@wa.test", "PNID-AI", ai=True)
    B = _setup_clinic(client, superadmin_headers, "Menuco", "menu@wa.test", "PNID-MENU", ai=False)
    menu_reply = _say(client, "PNID-MENU", "+91911111111", "book")   # menu: first msg -> main menu
    ai_reply = _say(client, "PNID-AI", "+91922222222", "book")        # AI: -> book proposal
    assert "1." in menu_reply and "2." in menu_reply                  # numbered menu
    assert "book" in ai_reply.lower() and "yes" in ai_reply.lower()   # AI confirm prompt
