"""Admin integration config: masking, hot-reload (stub->live), test endpoints."""
from __future__ import annotations


def test_status_defaults_stub(client):
    r = client.get("/admin/integrations/status")
    assert r.status_code == 200
    body = r.json()
    assert body["whatsapp"]["mode"] == "stub"
    assert body["bhashini"]["mode"] == "stub"
    # secret keys are masked, never returned as a value
    assert body["whatsapp"]["config"]["token"] == {"secret": True, "configured": False}
    assert body["bhashini"]["config"]["api_key"] == {"secret": True, "configured": False}


def test_set_whatsapp_masks_secret_and_marks_ready(client):
    r = client.put("/admin/integrations/whatsapp", json={
        "mode": "live", "token": "EAAG-secret-xyz", "phone_number_id": "12345",
        "base_url": "https://graph.facebook.com/v21.0"})
    assert r.status_code == 200
    pub = r.json()
    assert pub["token"] == {"secret": True, "configured": True}   # value never echoed
    assert pub["phone_number_id"] == "12345"
    st = client.get("/admin/integrations/status").json()
    assert st["whatsapp"]["mode"] == "live"
    assert st["whatsapp"]["ready_for_live"] is True


def test_empty_secret_does_not_wipe_existing(client):
    client.put("/admin/integrations/whatsapp", json={"mode": "live", "token": "keep-me", "phone_number_id": "1"})
    # resend without token -> must keep the old one
    client.put("/admin/integrations/whatsapp", json={"phone_number_id": "2"})
    st = client.get("/admin/integrations/status").json()
    assert st["whatsapp"]["config"]["token"]["configured"] is True
    assert st["whatsapp"]["config"]["phone_number_id"] == "2"


def test_bhashini_test_endpoint_uses_fallback_in_stub(client):
    r = client.post("/admin/integrations/bhashini/test", json={"text": "Book appointment", "target_lang": "ml"})
    assert r.status_code == 200
    b = r.json()
    assert b["translated"] != "Book appointment"   # localized via fallback
    assert b["used_fallback"] is True


def test_whatsapp_test_endpoint_stub(client):
    r = client.post("/admin/integrations/whatsapp/test", json={"to_phone": "+910000000000"})
    assert r.status_code == 200
    assert r.json()["sent"]["ok"] is True
