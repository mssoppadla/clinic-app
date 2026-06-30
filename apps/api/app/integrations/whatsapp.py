"""WhatsApp Cloud API client (DIRECT, no BSP). Reads runtime config (env+DB) per call,
so the admin screen can flip stub->live and set creds without a restart. Never raises into
the booking flow. Stub mode records sends for tests."""
from __future__ import annotations

import logging

import httpx

from ..core.config import get_settings
from ..core.integration_config import get_effective

log = logging.getLogger("integrations.whatsapp")
SENT_STUB: list[dict] = []  # inspectable by tests


class WhatsAppClient:
    def send_template(self, *, tenant_id: str, to_phone: str, template: str, params: dict) -> dict:
        cfg = get_effective("whatsapp", tenant_id=tenant_id)
        if cfg.get("mode") != "live" or not cfg.get("token"):
            SENT_STUB.append({"tenant_id": tenant_id, "to": to_phone, "template": template, "params": params})
            # stub mode = dev/test only (prod flips to live). Surface the OTP so local testers
            # can complete the WhatsApp-OTP flow without a real message; never reached in prod.
            if params.get("code"):
                log.info("whatsapp stub send template=%s to=%s OTP=%s", template, to_phone, params["code"],
                         extra={"event": "wa.stub", "tenant_id": tenant_id})
            else:
                log.info("whatsapp stub send", extra={"event": "wa.stub", "tenant_id": tenant_id})
            return {"ok": True, "mode": "stub"}
        try:
            url = f"{cfg['base_url']}/{cfg['phone_number_id']}/messages"
            body = {"messaging_product": "whatsapp", "to": to_phone, "type": "template",
                    "template": {"name": template, "language": {"code": params.get("lang", "en")}}}
            resp = httpx.post(url, json=body, headers={"Authorization": f"Bearer {cfg['token']}"},
                              timeout=10.0, verify=get_settings().outbound_tls_verify)
            resp.raise_for_status()
            return {"ok": True, "mode": "live", "id": resp.json().get("messages", [{}])[0].get("id")}
        except Exception as exc:
            log.warning("whatsapp live send failed", extra={"event": "wa.fail", "tenant_id": tenant_id})
            return {"ok": False, "mode": "live", "error": str(exc)}

    def send_text(self, *, tenant_id: str, to_phone: str, text: str) -> dict:
        """Free-form session reply (valid within 24h of the patient's last message). Used by the
        WhatsApp agent (menu + AI flows). Stub records the text for tests."""
        cfg = get_effective("whatsapp", tenant_id=tenant_id)
        if cfg.get("mode") != "live" or not cfg.get("token"):
            SENT_STUB.append({"tenant_id": tenant_id, "to": to_phone, "type": "text", "text": text})
            log.info("whatsapp stub text to=%s", to_phone, extra={"event": "wa.stub", "tenant_id": tenant_id})
            return {"ok": True, "mode": "stub"}
        try:
            url = f"{cfg['base_url']}/{cfg['phone_number_id']}/messages"
            body = {"messaging_product": "whatsapp", "to": to_phone, "type": "text",
                    "text": {"body": text}}
            resp = httpx.post(url, json=body, headers={"Authorization": f"Bearer {cfg['token']}"},
                              timeout=10.0, verify=get_settings().outbound_tls_verify)
            resp.raise_for_status()
            return {"ok": True, "mode": "live", "id": resp.json().get("messages", [{}])[0].get("id")}
        except Exception as exc:
            log.warning("whatsapp live text failed", extra={"event": "wa.fail", "tenant_id": tenant_id})
            return {"ok": False, "mode": "live", "error": str(exc)}
