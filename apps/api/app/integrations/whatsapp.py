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
    def send_template(self, *, tenant_id: str, to_phone: str, template: str,
                      language: str = "en_US", body_params: list | None = None) -> dict:
        """Send a pre-approved template. body_params fill the body's {{1}}..{{n}} variables IN ORDER
        (previously dropped — templates with variables were sent empty). Never raises."""
        cfg = get_effective("whatsapp", tenant_id=tenant_id)
        body_params = [str(p) for p in (body_params or [])]
        if cfg.get("mode") != "live" or not cfg.get("token"):
            SENT_STUB.append({"tenant_id": tenant_id, "to": to_phone, "template": template,
                              "language": language, "body_params": body_params})
            # stub = dev/test only (prod flips to live). Surface any code (OTP) so local testers can
            # complete the flow without a real message; never reached in prod.
            log.info("whatsapp stub send template=%s to=%s params=%s", template, to_phone, body_params,
                     extra={"event": "wa.stub", "tenant_id": tenant_id})
            return {"ok": True, "mode": "stub"}
        try:
            url = f"{cfg['base_url']}/{cfg['phone_number_id']}/messages"
            tpl: dict = {"name": template, "language": {"code": language}}
            if body_params:
                tpl["components"] = [{"type": "body",
                                      "parameters": [{"type": "text", "text": p} for p in body_params]}]
            body = {"messaging_product": "whatsapp", "to": to_phone, "type": "template", "template": tpl}
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
