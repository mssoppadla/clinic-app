"""Platform/admin integration configuration endpoints.

In prod these require staffAuth with an admin/superadmin scope (per the contract). For the
Phase-0 local skeleton auth is not yet enforced — wired in Phase 2. Secrets are write-only:
GET never returns a secret value, only whether it is configured.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core import integration_config as cfg
from ..integrations import bhashini, whatsapp
from .deps import require_role

# Platform integration config (secrets) — superadmin only [AC1].
router = APIRouter(prefix="/admin/integrations", tags=["admin"],
                   dependencies=[Depends(require_role("superadmin"))])

PROVIDERS = ("whatsapp", "bhashini")


@router.get("/status")
def status():
    out = {}
    for p in PROVIDERS:
        eff = cfg.get_effective(p)
        pub = cfg.get_public(p)
        ready = eff.get("mode") == "live" and all(
            eff.get(k) for k in (["token", "phone_number_id"] if p == "whatsapp" else ["api_key", "base_url"])
        )
        out[p] = {"mode": eff.get("mode", "stub"), "ready_for_live": ready, "config": pub}
    return out


class WhatsAppCfg(BaseModel):
    mode: str | None = None
    base_url: str | None = None
    token: str | None = None
    phone_number_id: str | None = None
    business_account_id: str | None = None
    verify_token: str | None = None
    display_number: str | None = None


class BhashiniCfg(BaseModel):
    mode: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    user_id: str | None = None
    translate_pipeline: str | None = None
    transliterate_pipeline: str | None = None
    asr_pipeline: str | None = None
    tts_pipeline: str | None = None
    languages: str | None = None


@router.put("/whatsapp")
def set_whatsapp(body: WhatsAppCfg):
    cfg.set_many("whatsapp", body.model_dump(exclude_none=True))
    return cfg.get_public("whatsapp")


@router.put("/bhashini")
def set_bhashini(body: BhashiniCfg):
    cfg.set_many("bhashini", body.model_dump(exclude_none=True))
    return cfg.get_public("bhashini")


class WhatsAppTest(BaseModel):
    to_phone: str
    template: str = "hello_world"


@router.post("/whatsapp/test")
def test_whatsapp(body: WhatsAppTest):
    res = whatsapp().send_template(tenant_id="__admin_test__", to_phone=body.to_phone,
                                   template=body.template, params={"lang": "en"})
    return {"sent": res}


class BhashiniTest(BaseModel):
    text: str = "Book appointment"
    target_lang: str = "ml"


@router.post("/bhashini/test")
def test_bhashini(body: BhashiniTest):
    out = bhashini().localize(tenant_id="__admin_test__", keys={body.text: body.text},
                              target_lang=body.target_lang)
    translated = out[body.text]
    return {"source": body.text, "translated": translated,
            "used_fallback": translated == body.text or cfg.get_effective("bhashini").get("mode") != "live"}
