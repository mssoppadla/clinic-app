"""Platform-level integration config for TOVAITECH's OWN WhatsApp accounts — superadmin only.

Distinct from the per-clinic admin page (which configures each hospital's number). Here the
platform admin stores Tovaitech's **test** and **live** WhatsApp sending accounts and chooses
which one is active. The active account becomes the platform-scope default that every clinic
falls back to when it hasn't set its own number (env < platform < clinic layering).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core import integration_config as cfg
from ..core.config import get_settings
from ..core.db import system_session
from ..core.errors import AppError
from ..integrations import whatsapp
from ..models import IntegrationConfig, Tenant
from .deps import require_role

router = APIRouter(prefix="/admin/platform", tags=["platform-admin"],
                   dependencies=[Depends(require_role("superadmin"))])

_ENVS = ("test", "live")
_ACTIVE_PROVIDER, _ACTIVE_KEY = "platform_meta", "wa_active_env"


def _scope(env: str) -> str:
    if env not in _ENVS:
        raise AppError("invalid_env", "environment must be 'test' or 'live'.", status=422)
    return f"platform:{env}"


def _get_active_env() -> str:
    with system_session() as db:
        row = (db.query(IntegrationConfig)
               .filter(IntegrationConfig.provider == _ACTIVE_PROVIDER,
                       IntegrationConfig.key == _ACTIVE_KEY).first())
        return row.value if row and row.value in _ENVS else "test"


def _set_active_env(env: str) -> None:
    with system_session() as db:
        row = (db.query(IntegrationConfig)
               .filter(IntegrationConfig.provider == _ACTIVE_PROVIDER,
                       IntegrationConfig.key == _ACTIVE_KEY).first())
        if row is None:
            db.add(IntegrationConfig(scope="platform", provider=_ACTIVE_PROVIDER,
                                     key=_ACTIVE_KEY, value=env))
        else:
            row.value = env


@router.get("/whatsapp")
def get_platform_whatsapp():
    """Both accounts (masked) + which is active + the effective platform default."""
    return {
        "active_env": _get_active_env(),
        "test": cfg.get_public_scoped("whatsapp", _scope("test")),
        "live": cfg.get_public_scoped("whatsapp", _scope("live")),
        "effective": cfg.get_public("whatsapp"),
    }


class WhatsAppCfg(BaseModel):
    mode: str | None = None
    base_url: str | None = None
    token: str | None = None
    phone_number_id: str | None = None
    business_account_id: str | None = None
    verify_token: str | None = None
    display_number: str | None = None


@router.put("/whatsapp/{env}")
def set_platform_whatsapp(env: str, body: WhatsAppCfg):
    """Store Tovaitech's test or live account. If that env is the active one, also publish it."""
    scope = _scope(env)
    cfg.set_many("whatsapp", body.model_dump(exclude_none=True), scope=scope)
    if _get_active_env() == env:
        cfg.activate_scope("whatsapp", scope)     # keep the effective default in sync
    return cfg.get_public_scoped("whatsapp", scope)


class ActivateIn(BaseModel):
    environment: str


@router.post("/whatsapp/activate")
def activate_platform_whatsapp(body: ActivateIn):
    """Make test or live the active account Tovaitech sends from (publishes to platform scope)."""
    scope = _scope(body.environment)
    cfg.activate_scope("whatsapp", scope)
    _set_active_env(body.environment)
    return {"active_env": body.environment, "effective": cfg.get_public("whatsapp")}


class WhatsAppTest(BaseModel):
    to_phone: str
    template: str = "hello_world"
    # Meta's sample "hello_world" template is registered as en_US; the language code must match a
    # language the template is approved in, or Graph 400s. Overridable for other templates.
    language: str = "en_US"


@router.post("/whatsapp/test")
def test_platform_whatsapp(body: WhatsAppTest):
    """Send a test message using Tovaitech's active platform account."""
    res = whatsapp().send_template(tenant_id="__platform__", to_phone=body.to_phone,
                                   template=body.template, language=body.language)
    return {"sent": res, "active_env": _get_active_env()}


# ---- Meta-app webhook secrets (verify token + app secret) — one Meta app, all clinics --------

def _webhook_path() -> str:
    """Public path Meta calls, matching the app's root_path. Prod serves under /api/<ver> (the
    front proxy forwards /api/* unstripped); local has no prefix. Prepend the origin in the UI."""
    s = get_settings()
    root = "" if s.env == "local" else f"/api/{s.api_version}"
    return f"{root}/webhooks/whatsapp"


@router.get("/webhook")
def get_platform_webhook():
    """Webhook config for the Meta app: callback path + verify token (visible, you paste it into
    Meta) + whether the app secret is set (masked, write-only)."""
    pub = cfg.get_public("platform_meta")
    return {
        "callback_path": _webhook_path(),
        "verify_token": pub.get("verify_token") or "",
        "app_secret": pub.get("app_secret", {"secret": True, "configured": False}),
    }


class WebhookCfg(BaseModel):
    verify_token: str | None = None
    app_secret: str | None = None       # write-only; blank keeps the existing one


@router.put("/webhook")
def set_platform_webhook(body: WebhookCfg):
    """Store the verify token + app secret (config-driven, hot-reload — the webhook reads these at
    request time, with env APP_WHATSAPP_VERIFY_TOKEN/APP_WHATSAPP_APP_SECRET as the fallback)."""
    cfg.set_many("platform_meta", body.model_dump(exclude_none=True))
    return get_platform_webhook()


@router.get("/ai")
def get_platform_ai():
    """Tovaitech's AI LLM config (Claude) — mode/model + whether a key is set (masked)."""
    return cfg.get_public("ai")


class AiCfg(BaseModel):
    mode: str | None = None       # stub | live
    provider: str | None = None   # anthropic | openai
    model: str | None = None      # e.g. claude-opus-4-8 / gpt-4o-mini
    api_key: str | None = None    # write-only


@router.put("/ai")
def set_platform_ai(body: AiCfg):
    """Register/update the AI LLM key + model from the UI (config-driven, hot-reload — not code)."""
    cfg.set_many("ai", body.model_dump(exclude_none=True))
    return cfg.get_public("ai")


class AiToggleIn(BaseModel):
    enabled: bool


@router.post("/clinics/{slug}/ai")
def set_clinic_ai(slug: str, body: AiToggleIn):
    """Superadmin enables/disables the AI WhatsApp agent for a clinic. When off, that clinic's
    patients get the deterministic numbered menu instead. The two flows are fully independent."""
    with system_session() as db:
        t = db.query(Tenant).filter(Tenant.slug == slug).first()
        if t is None:
            raise AppError("tenant_not_found", f"No clinic for slug '{slug}'", status=404)
        tid = t.id
    cfg.set_clinic_flag(tid, "ai_enabled", body.enabled)
    return {"slug": slug, "ai_enabled": body.enabled}
