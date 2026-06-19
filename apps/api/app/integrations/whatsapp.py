"""WhatsApp Cloud API client (DIRECT, no BSP). Reads runtime config (env+DB) per call,
so the admin screen can flip stub->live and set creds without a restart. Never raises into
the booking flow. Stub mode records sends for tests."""
from __future__ import annotations

import logging

import httpx

from ..core.integration_config import get_effective

log = logging.getLogger("integrations.whatsapp")
SENT_STUB: list[dict] = []  # inspectable by tests


class WhatsAppClient:
    def send_template(self, *, tenant_id: str, to_phone: str, template: str, params: dict) -> dict:
        cfg = get_effective("whatsapp")
        if cfg.get("mode") != "live" or not cfg.get("token"):
            SENT_STUB.append({"tenant_id": tenant_id, "to": to_phone, "template": template, "params": params})
            log.info("whatsapp stub send", extra={"event": "wa.stub", "tenant_id": tenant_id})
            return {"ok": True, "mode": "stub"}
        try:
            url = f"{cfg['base_url']}/{cfg['phone_number_id']}/messages"
            body = {"messaging_product": "whatsapp", "to": to_phone, "type": "template",
                    "template": {"name": template, "language": {"code": params.get("lang", "en")}}}
            resp = httpx.post(url, json=body, headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=10.0)
            resp.raise_for_status()
            return {"ok": True, "mode": "live", "id": resp.json().get("messages", [{}])[0].get("id")}
        except Exception as exc:
            log.warning("whatsapp live send failed", extra={"event": "wa.fail", "tenant_id": tenant_id})
            return {"ok": False, "mode": "live", "error": str(exc)}
