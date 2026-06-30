"""Inbound WhatsApp webhook (two-way messaging) — ONE public endpoint for ALL clinics.

Public URL (register in Meta): https://tovaitech.in/appointments/v1/webhooks/whatsapp
  GET  -> Meta verification handshake (hub.challenge), checked against APP_WHATSAPP_VERIFY_TOKEN.
  POST -> inbound messages + delivery statuses. Verifies Meta's X-Hub-Signature-256 with the app
          secret, routes each message to its clinic by metadata.phone_number_id, and dedupes by
          the Meta message id. The AI reply step is wired in the next slice (whatsapp_agent).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..core.db import system_session
from ..core.integration_config import find_clinic_by_whatsapp_number, get_effective
from ..domain.whatsapp_agent import handle_message
from ..domain.whatsapp_routing import resolve_shared_clinic
from ..integrations import whatsapp
from ..models import WhatsAppMessage

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])
log = logging.getLogger("whatsapp.webhook")


@router.get("/whatsapp")
def verify_webhook(request: Request):
    """Meta calls this once to verify the callback URL. Echo hub.challenge iff the token matches."""
    q = request.query_params
    mode, token, challenge = q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge")
    expected = get_effective("platform_meta").get("verify_token")  # admin UI value, env fallback
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("forbidden", status_code=403)


def _signature_ok(app_secret: str, raw: bytes, header: str | None) -> bool:
    """Verify Meta's X-Hub-Signature-256: 'sha256=<hex hmac of the raw body with the app secret>'."""
    if not app_secret:
        return False                      # never accept unsigned in any real (configured) setup
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


def _iter_inbound(payload: dict):
    """Yield (phone_number_id, from_phone, text, wa_message_id, profile_name) per inbound text."""
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                     for c in value.get("contacts", []) or []}
            for msg in value.get("messages", []) or []:
                if msg.get("type") != "text":
                    continue              # this slice handles text; media/interactive come later
                frm = msg.get("from", "")
                yield (phone_number_id, frm, (msg.get("text") or {}).get("body", ""),
                       msg.get("id"), names.get(frm))


def _process(tenant_id: str, phone: str, text: str, profile_name: str | None) -> None:
    """Run the agent for one message and send the reply (background — keeps the webhook fast)."""
    try:
        reply = handle_message(tenant_id, phone, text, profile_name)
    except Exception:
        log.exception("whatsapp agent failed tenant=%s", tenant_id)
        reply = "Sorry, something went wrong. Please reply 'menu' to try again."
    res = whatsapp().send_text(tenant_id=tenant_id, to_phone=phone, text=reply)
    with system_session() as db:
        db.add(WhatsAppMessage(tenant_id=tenant_id, wa_message_id=res.get("id"),
                               direction="out", phone=phone, text=reply))


def _ask_clinic(phone: str) -> None:
    """Inbound on the shared number but we can't tell which clinic — ask for the clinic link/code."""
    whatsapp().send_text(tenant_id="__platform__", to_phone=phone,
                         text=("Welcome to Tovaitech! Please open your clinic's WhatsApp booking "
                               "link, or reply with your clinic code to continue."))


@router.post("/whatsapp")
async def receive_webhook(request: Request, background: BackgroundTasks):
    """Receive inbound messages. Always 200 quickly (Meta retries on non-2xx). Bad signature -> 403.
    The agent runs in the background so a slow AI call never blocks the webhook ack."""
    app_secret = get_effective("platform_meta").get("app_secret")  # admin UI value, env fallback
    raw = await request.body()
    if not _signature_ok(app_secret, raw, request.headers.get("X-Hub-Signature-256")):
        return PlainTextResponse("bad signature", status_code=403)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"ok": True})   # ack malformed bodies so Meta stops retrying

    processed = 0
    for phone_number_id, from_phone, text, wa_id, profile_name in _iter_inbound(payload):
        # own-number clinics route by phone_number_id; otherwise this is Tovaitech's SHARED number
        # and we route by the clinic code in the message (or a remembered binding).
        tenant_id = find_clinic_by_whatsapp_number(phone_number_id) or resolve_shared_clinic(from_phone, text)
        with system_session() as db:      # platform context; tables are RLS-bypassed here
            if wa_id and db.query(WhatsAppMessage).filter(
                    WhatsAppMessage.wa_message_id == wa_id).first() is not None:
                continue                  # dedupe Meta retries
            db.add(WhatsAppMessage(tenant_id=(tenant_id or "__unrouted__"), wa_message_id=wa_id,
                                   direction="in", phone=from_phone, text=text))
        if tenant_id is None:
            background.add_task(_ask_clinic, from_phone)   # shared number, clinic unknown -> ask
        else:
            background.add_task(_process, tenant_id, from_phone, text, profile_name)
        processed += 1
    return JSONResponse({"ok": True, "processed": processed})
