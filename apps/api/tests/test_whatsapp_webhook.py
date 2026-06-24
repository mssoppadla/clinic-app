"""Inbound WhatsApp webhook foundation: Meta verification, signature check, per-clinic routing,
and message dedupe. (The AI reply step is a later slice.)"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

VERIFY = "verify-tok-123"
SECRET = "app-secret-xyz"


@pytest.fixture()
def wa_env():
    """Configure the platform verify token + app secret (read at request time)."""
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


def _payload(phone_number_id: str, from_phone: str, text: str, wa_id: str) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [{"from": from_phone, "id": wa_id, "type": "text", "text": {"body": text}}],
    }}]}]}


# ---- GET verification handshake -----------------------------------------------------------

def test_verify_handshake_ok(client, wa_env):
    r = client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"


def test_verify_handshake_wrong_token(client, wa_env):
    r = client.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert r.status_code == 403


# ---- POST signature + routing + dedupe ----------------------------------------------------

def test_post_rejects_bad_signature(client, wa_env):
    raw = json.dumps(_payload("PNID", "+91900", "hi", "wamid.1")).encode()
    r = client.post("/webhooks/whatsapp", content=raw,
                    headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"})
    assert r.status_code == 403


def test_post_unrouted_message_is_asked_for_clinic(client, wa_env):
    # no clinic owns this number and the message has no clinic code -> shared-number fallback: ask
    from app.integrations.whatsapp import SENT_STUB
    SENT_STUB.clear()
    raw = json.dumps(_payload("UNCONFIGURED", "+91900", "hi", "wamid.unknown")).encode()
    r = client.post("/webhooks/whatsapp", content=raw,
                    headers={"X-Hub-Signature-256": _sign(raw), "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["processed"] == 1
    asked = [m for m in SENT_STUB if m.get("type") == "text" and m.get("to") == "+91900"]
    assert asked and "clinic" in asked[-1]["text"].lower()


def test_post_routes_to_clinic_and_dedupes(client, canary, wa_env):
    from app.core import integration_config as cfg
    from app.core.db import system_session
    from app.models import WhatsAppMessage
    # this clinic owns WhatsApp number PNID-canary
    cfg.set_many("whatsapp", {"phone_number_id": "PNID-canary"}, tenant_id=canary["tenant_id"])

    raw = json.dumps(_payload("PNID-canary", "+919811112222", "Hi, can I book?", "wamid.AAA")).encode()
    sig = {"X-Hub-Signature-256": _sign(raw), "Content-Type": "application/json"}
    r1 = client.post("/webhooks/whatsapp", content=raw, headers=sig)
    assert r1.status_code == 200 and r1.json()["processed"] == 1

    # stored under the right clinic
    with system_session() as db:
        rows = db.query(WhatsAppMessage).filter(WhatsAppMessage.wa_message_id == "wamid.AAA").all()
    assert len(rows) == 1 and rows[0].tenant_id == canary["tenant_id"] and rows[0].direction == "in"

    # Meta retry with the same message id -> deduped (not processed, not duplicated)
    r2 = client.post("/webhooks/whatsapp", content=raw, headers=sig)
    assert r2.status_code == 200 and r2.json()["processed"] == 0
    with system_session() as db:
        again = db.query(WhatsAppMessage).filter(WhatsAppMessage.wa_message_id == "wamid.AAA").all()
    assert len(again) == 1
