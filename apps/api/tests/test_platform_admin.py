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


def test_ai_llm_config_is_admin_driven_not_hardcoded(client, superadmin_headers):
    """The AI key/model/mode live in runtime config (set in the UI), masked, superadmin-only."""
    assert client.get("/admin/platform/ai").status_code == 401          # gated
    r = client.put("/admin/platform/ai", headers=superadmin_headers,
                   json={"mode": "live", "model": "claude-opus-4-8", "api_key": "sk-ant-secret"})
    assert r.status_code == 200
    pub = r.json()
    assert pub["api_key"] == {"secret": True, "configured": True}        # never echoed
    assert pub["mode"] == "live" and pub["model"] == "claude-opus-4-8"
    # and it's what the AI client reads (config-driven, hot-reload)
    from app.core import integration_config as cfg
    eff = cfg.get_effective("ai")
    assert eff["mode"] == "live" and eff["api_key"] == "sk-ant-secret" and eff["model"] == "claude-opus-4-8"
