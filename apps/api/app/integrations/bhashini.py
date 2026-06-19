"""Bhashini language client (Malayalam-first). English ALWAYS present (A15); Malayalam in
addition. Reads runtime config (env+DB) per call. On any failure -> static fallback so the UI
never breaks. Stub mode returns the fallback dictionary."""
from __future__ import annotations

import logging

import httpx

from ..core.integration_config import get_effective

log = logging.getLogger("integrations.bhashini")

STATIC_ML = {
    "Book appointment": "അപ്പോയിന്റ്മെന്റ് ബുക്ക് ചെയ്യുക",
    "Join today's queue": "ഇന്നത്തെ ക്യൂവിൽ ചേരുക",
    "in queue now": "ഇപ്പോൾ ക്യൂവിൽ",
    "avg wait": "ശരാശരി കാത്തിരിപ്പ്",
    "Your token": "നിങ്ങളുടെ ടോക്കൺ",
    "Patient name": "രോഗിയുടെ പേര്",
    "Reason for visit": "സന്ദർശന കാരണം",
    "Mobile number": "മൊബൈൽ നമ്പർ",
}


class BhashiniClient:
    def localize(self, *, tenant_id: str, keys: dict[str, str], target_lang: str) -> dict[str, str]:
        if target_lang == "en":
            return keys
        cfg = get_effective("bhashini")
        if cfg.get("mode") != "live" or not cfg.get("api_key"):
            return self._fallback(keys, target_lang)
        try:
            resp = httpx.post(
                f"{cfg['base_url']}/translate",
                json={"source": "en", "target": target_lang, "texts": list(keys.values()),
                      "pipeline": cfg.get("translate_pipeline")},
                headers={"Authorization": cfg["api_key"], "userID": cfg.get("user_id", "")},
                timeout=8.0)
            resp.raise_for_status()
            translated = resp.json().get("texts", [])
            if len(translated) != len(keys):
                raise ValueError("length mismatch")
            return {k: translated[i] for i, k in enumerate(keys.keys())}
        except Exception:
            log.warning("bhashini live failed -> fallback", extra={"event": "bh.fallback", "tenant_id": tenant_id})
            return self._fallback(keys, target_lang)

    def _fallback(self, keys: dict[str, str], target_lang: str) -> dict[str, str]:
        if target_lang == "ml":
            return {k: STATIC_ML.get(v, v) for k, v in keys.items()}
        return keys
