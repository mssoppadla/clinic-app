"""WhatsApp stub + Bhashini fallback + health probes."""
from __future__ import annotations

from app.integrations import bhashini
from app.integrations.bhashini import STATIC_ML
from app.integrations.whatsapp import SENT_STUB


def test_health_and_ready(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json()["status"] == "ready"


def test_whatsapp_confirmation_sent_on_booking(client, canary):
    SENT_STUB.clear()
    payload = {
        "doctor_id": canary["doctor_id"],
        "patients": [{"name": "WA Test"}],
        "contact_phone": "+919822222222",
        "consent": True,
    }
    r = client.post("/bookings", json=payload, headers={"X-Clinic-Slug": canary["slug"]})
    assert r.status_code == 201
    assert len(SENT_STUB) == 1
    assert SENT_STUB[0]["template"] == "booking_confirmed"


def test_bhashini_fallback_translates_known_keys(canary):
    out = bhashini().localize(tenant_id=canary["tenant_id"],
                              keys={"Your token": "Your token"}, target_lang="ml")
    assert out["Your token"] == STATIC_ML["Your token"]


def test_bhashini_english_passthrough(canary):
    out = bhashini().localize(tenant_id=canary["tenant_id"],
                              keys={"Patient name": "Patient name"}, target_lang="en")
    assert out["Patient name"] == "Patient name"  # English never altered (A15)
