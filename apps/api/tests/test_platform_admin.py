"""Platform admin: Tovaitech's own test/live WhatsApp accounts (superadmin). Secrets masked;
the active account becomes the platform-scope default clinics fall back to."""
from __future__ import annotations


def test_requires_superadmin(client):
    assert client.get("/admin/platform/whatsapp").status_code == 401


def test_defaults_to_test_env_empty(client, superadmin_headers):
    r = client.get("/admin/platform/whatsapp", headers=superadmin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["active_env"] == "test"
    assert body["test"]["token"] == {"secret": True, "configured": False}
    assert "live" in body and "effective" in body


def test_set_test_account_masks_secret_and_publishes_when_active(client, superadmin_headers):
    # test is active by default -> saving it also publishes to the effective platform default
    r = client.put("/admin/platform/whatsapp/test", headers=superadmin_headers, json={
        "mode": "live", "token": "EAAtest-secret", "phone_number_id": "1242752045578853",
        "business_account_id": "1732099944482965"})
    assert r.status_code == 200
    assert r.json()["token"] == {"secret": True, "configured": True}      # value never echoed
    eff = client.get("/admin/platform/whatsapp", headers=superadmin_headers).json()
    assert eff["effective"]["phone_number_id"] == "1242752045578853"      # published to platform


def test_activate_switches_effective_account(client, superadmin_headers):
    h = superadmin_headers
    client.put("/admin/platform/whatsapp/test", headers=h, json={
        "mode": "live", "token": "t", "phone_number_id": "TESTID"})
    client.put("/admin/platform/whatsapp/live", headers=h, json={
        "mode": "live", "token": "l", "phone_number_id": "LIVEID"})
    # test was active -> effective is the test number
    assert client.get("/admin/platform/whatsapp", headers=h).json()["effective"]["phone_number_id"] == "TESTID"
    # switch to live
    act = client.post("/admin/platform/whatsapp/activate", headers=h, json={"environment": "live"})
    assert act.status_code == 200 and act.json()["active_env"] == "live"
    assert act.json()["effective"]["phone_number_id"] == "LIVEID"
    # both accounts remain stored independently
    full = client.get("/admin/platform/whatsapp", headers=h).json()
    assert full["test"]["phone_number_id"] == "TESTID" and full["live"]["phone_number_id"] == "LIVEID"


def test_invalid_env_rejected(client, superadmin_headers):
    assert client.put("/admin/platform/whatsapp/staging", headers=superadmin_headers,
                      json={"mode": "stub"}).status_code == 422


def test_webhook_config_gated_and_masks_app_secret(client, superadmin_headers):
    assert client.get("/admin/platform/webhook").status_code == 401          # gated
    r = client.get("/admin/platform/webhook", headers=superadmin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["callback_path"].endswith("/webhooks/whatsapp")              # env-aware path
    assert body["app_secret"] == {"secret": True, "configured": False}       # masked, not set


def test_webhook_config_is_used_by_the_webhook(client, superadmin_headers):
    """Verify token + app secret set in the UI are what the public webhook validates against
    (config-driven, hot-reload) — no env/redeploy needed."""
    import hashlib, hmac, json
    r = client.put("/admin/platform/webhook", headers=superadmin_headers,
                   json={"verify_token": "VT-from-ui", "app_secret": "AS-from-ui"})
    assert r.status_code == 200
    assert r.json()["verify_token"] == "VT-from-ui"
    assert r.json()["app_secret"] == {"secret": True, "configured": True}     # never echoed

    # GET handshake now accepts the UI token
    ok = client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "VT-from-ui", "hub.challenge": "99"})
    assert ok.status_code == 200 and ok.text == "99"
    bad = client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "99"})
    assert bad.status_code == 403

    # POST signature is verified with the UI app secret
    raw = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    sig = "sha256=" + hmac.new(b"AS-from-ui", raw, hashlib.sha256).hexdigest()
    good = client.post("/webhooks/whatsapp", content=raw,
                       headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert good.status_code == 200
    bad_sig = client.post("/webhooks/whatsapp", content=raw,
                          headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"})
    assert bad_sig.status_code == 403


def test_ai_llm_config_is_admin_driven_not_hardcoded(client, superadmin_headers):
    """The AI key/model/mode live in runtime config (set in the UI), masked, superadmin-only."""
    assert client.get("/admin/platform/ai").status_code == 401          # gated
    r = client.put("/admin/platform/ai", headers=superadmin_headers,
                   json={"mode": "live", "provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-secret"})
    assert r.status_code == 200
    pub = r.json()
    assert pub["api_key"] == {"secret": True, "configured": True}        # never echoed
    assert pub["mode"] == "live" and pub["provider"] == "openai" and pub["model"] == "gpt-4o-mini"
    # and it's what the AI client reads (config-driven, hot-reload)
    from app.core import integration_config as cfg
    eff = cfg.get_effective("ai")
    assert (eff["mode"] == "live" and eff["provider"] == "openai"
            and eff["api_key"] == "sk-secret" and eff["model"] == "gpt-4o-mini")
