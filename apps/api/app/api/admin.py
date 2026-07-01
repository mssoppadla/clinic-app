"""Per-clinic integration configuration endpoints.

Each hospital configures its OWN WhatsApp number + Bhashini creds — addressed per clinic at
/appointments/<slug>/admin (the page sends X-Clinic-Slug). Authorized to that clinic's admin or
a platform superadmin. Config is layered env < platform < clinic, so a clinic falls back to a
platform default until it sets its own. Secrets are write-only: GET never returns a secret value.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core import integration_config as cfg
from ..core.config import get_settings
from ..integrations import bhashini, whatsapp
from .deps import require_clinic_staff

# clinic_admin manages their own clinic's providers; superadmin may manage any clinic's.
router = APIRouter(prefix="/admin/integrations", tags=["admin"])

PROVIDERS = ("whatsapp", "bhashini")


@router.get("/status")
def status(ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    tid = ctx["tenant"]["id"]
    out = {"clinic": {"slug": ctx["tenant"]["slug"], "name": ctx["tenant"]["name"]}}
    for p in PROVIDERS:
        eff = cfg.get_effective(p, tenant_id=tid)
        pub = cfg.get_public(p, tenant_id=tid)
        ready = eff.get("mode") == "live" and all(
            eff.get(k) for k in (["token", "phone_number_id"] if p == "whatsapp" else ["api_key", "base_url"])
        )
        out[p] = {"mode": eff.get("mode", "stub"), "ready_for_live": ready, "config": pub}
    # WhatsApp agent: AI on/off is set by the platform admin (read-only here); confirm-before-
    # booking is the clinic's own toggle (configurable, not hardcoded).
    from ..domain.whatsapp_routing import deep_link
    shared = cfg.get_clinic_flag(tid, "wa_shared", False)
    out["agent"] = {
        "ai_enabled": cfg.get_clinic_flag(tid, "ai_enabled", False),
        "confirm_before_booking": cfg.get_clinic_flag(
            tid, "confirm", default=get_settings().ai_confirm_before_action),
        "wa_shared": shared,
        "whatsapp_deep_link": deep_link(ctx["tenant"]["slug"]) if shared else None,
    }
    return out


class WaSourceIn(BaseModel):
    shared: bool


@router.post("/whatsapp-source")
def set_whatsapp_source(body: WaSourceIn, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    """Choose this clinic's WhatsApp number source: its own number (False) or Tovaitech's shared
    number (True). On shared, patients reach the clinic via its deep link (no own number needed)."""
    cfg.set_clinic_flag(ctx["tenant"]["id"], "wa_shared", body.shared)
    return {"wa_shared": body.shared}


class ConfirmIn(BaseModel):
    enabled: bool


@router.post("/confirm")
def set_confirm(body: ConfirmIn, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    """Clinic toggle: confirm a WhatsApp booking/queue action with the patient before committing.
    Off = act immediately (fewer messages). Applies to both the menu and AI flows."""
    cfg.set_clinic_flag(ctx["tenant"]["id"], "confirm", body.enabled)
    return {"confirm_before_booking": body.enabled}


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
def set_whatsapp(body: WhatsAppCfg, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    tid = ctx["tenant"]["id"]
    cfg.set_many("whatsapp", body.model_dump(exclude_none=True), tenant_id=tid)
    return cfg.get_public("whatsapp", tenant_id=tid)


@router.put("/bhashini")
def set_bhashini(body: BhashiniCfg, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    tid = ctx["tenant"]["id"]
    cfg.set_many("bhashini", body.model_dump(exclude_none=True), tenant_id=tid)
    return cfg.get_public("bhashini", tenant_id=tid)


class WhatsAppTest(BaseModel):
    to_phone: str
    template: str = "hello_world"


@router.post("/whatsapp/test")
def test_whatsapp(body: WhatsAppTest, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    res = whatsapp().send_template(tenant_id=ctx["tenant"]["id"], to_phone=body.to_phone,
                                   template=body.template, language="en_US")
    return {"sent": res}


class BhashiniTest(BaseModel):
    text: str = "Book appointment"
    target_lang: str = "ml"


@router.post("/bhashini/test")
def test_bhashini(body: BhashiniTest, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    tid = ctx["tenant"]["id"]
    out = bhashini().localize(tenant_id=tid, keys={body.text: body.text},
                              target_lang=body.target_lang)
    translated = out[body.text]
    return {"source": body.text, "translated": translated,
            "used_fallback": translated == body.text or cfg.get_effective("bhashini", tenant_id=tid).get("mode") != "live"}
