"""Runtime integration configuration (no hardcoding, hot-reload).

Effective config = env defaults (Settings) overlaid with DB values (platform scope).
Clients call get_effective() at call time, so flipping stub->live or entering creds in the
admin screen takes effect WITHOUT a restart. Secrets are write-only: get_public() masks them.
"""
from __future__ import annotations

from .config import get_settings
from .db import session_scope
from ..models import IntegrationConfig

# managed keys per provider, and which Settings (env) field is the default
FIELDS = {
    "whatsapp": {
        "mode": "whatsapp_mode", "base_url": "whatsapp_base_url",
        "token": "whatsapp_token", "phone_number_id": "whatsapp_phone_number_id",
        "business_account_id": None, "verify_token": None, "display_number": None,
    },
    "bhashini": {
        "mode": "bhashini_mode", "base_url": "bhashini_base_url",
        "api_key": "bhashini_api_key", "user_id": "bhashini_user_id",
        "translate_pipeline": None, "transliterate_pipeline": None, "asr_pipeline": None, "tts_pipeline": None,
        "languages": "default_languages",
    },
    # AI LLM (Claude) for the WhatsApp agent — configured by the superadmin in the platform tab,
    # NOT hardcoded. Env values are only the bootstrap default; the DB value (set via the UI)
    # overrides them and hot-reloads (same pattern as whatsapp/bhashini).
    "ai": {"mode": "ai_mode", "provider": "ai_provider", "api_key": "anthropic_api_key", "model": "ai_model"},
    # Meta-app-level WhatsApp WEBHOOK secrets (one Tovaitech Meta app, shared by all clinics):
    # the GET-handshake verify_token and the app_secret used to verify X-Hub-Signature-256.
    # Set in the platform admin UI; env (APP_WHATSAPP_VERIFY_TOKEN/APP_WHATSAPP_APP_SECRET) is
    # only the bootstrap fallback. (The active test/live SENDING account lives under "whatsapp".)
    "platform_meta": {"verify_token": "whatsapp_verify_token", "app_secret": "whatsapp_app_secret"},
}
SECRETS = {"whatsapp": {"token"}, "bhashini": {"api_key"}, "ai": {"api_key"},
           "platform_meta": {"app_secret"}}


def _env_default(field: str | None) -> str:
    if not field:
        return ""
    val = getattr(get_settings(), field, "")
    return str(val) if val is not None else ""


def _clinic_scope(tenant_id: str) -> str:
    return f"clinic:{tenant_id}"


def _overlay(cfg: dict, db, provider: str, scope: str) -> None:
    for r in db.query(IntegrationConfig).filter(
            IntegrationConfig.provider == provider,
            IntegrationConfig.scope == scope).all():
        if r.value != "":
            cfg[r.key] = r.value


def get_effective(provider: str, tenant_id: str | None = None) -> dict:
    """Full config INCLUDING secrets — internal use by clients only.

    Layered: env defaults < platform-scope DB < this clinic's DB. So each hospital can have its
    own WhatsApp number / Bhashini creds, falling back to a platform default when unset."""
    cfg = {k: _env_default(v) for k, v in FIELDS[provider].items()}
    with session_scope() as db:
        _overlay(cfg, db, provider, "platform")
        if tenant_id:
            _overlay(cfg, db, provider, _clinic_scope(tenant_id))
    return cfg


def get_public(provider: str, tenant_id: str | None = None) -> dict:
    """Safe view for the UI: secrets replaced by a 'configured' flag, never the value."""
    eff = get_effective(provider, tenant_id)
    out = {}
    for k, v in eff.items():
        if k in SECRETS.get(provider, set()):
            out[k] = {"secret": True, "configured": bool(v)}
        else:
            out[k] = v
    return out


def set_many(provider: str, data: dict, tenant_id: str | None = None, scope: str | None = None) -> None:
    """Upsert provided keys for a clinic (or platform when tenant_id is None), or an explicit
    `scope` (e.g. 'platform:test'). Empty secret values are ignored (don't wipe an existing one)."""
    scope = scope or (_clinic_scope(tenant_id) if tenant_id else "platform")
    secrets = SECRETS.get(provider, set())
    valid = set(FIELDS[provider].keys())
    with session_scope() as db:
        for key, value in data.items():
            if key not in valid:
                continue
            if key in secrets and (value is None or value == ""):
                continue  # keep existing secret
            value = "" if value is None else str(value)
            row = db.query(IntegrationConfig).filter(
                IntegrationConfig.provider == provider,
                IntegrationConfig.scope == scope,
                IntegrationConfig.key == key).first()
            if row is None:
                db.add(IntegrationConfig(scope=scope, provider=provider, key=key,
                                         value=value, is_secret=key in secrets))
            else:
                row.value = value


def find_clinic_by_whatsapp_number(phone_number_id: str) -> str | None:
    """Route an inbound WhatsApp webhook to its clinic by the metadata.phone_number_id that Meta
    sends — matched against each clinic's stored whatsapp 'phone_number_id'. Returns tenant_id."""
    if not phone_number_id:
        return None
    with session_scope() as db:
        row = (db.query(IntegrationConfig)
               .filter(IntegrationConfig.provider == "whatsapp",
                       IntegrationConfig.key == "phone_number_id",
                       IntegrationConfig.value == str(phone_number_id))
               .first())
    if row and row.scope.startswith("clinic:"):
        return row.scope.split("clinic:", 1)[1]
    return None


# ---- platform test/live account management (superadmin) -------------------------------------

def get_public_scoped(provider: str, scope: str) -> dict:
    """Masked view of ONE scope's config (env defaults + that scope's stored rows). Used by the
    platform admin to edit Tovaitech's own 'platform:test' / 'platform:live' WhatsApp accounts."""
    cfg = {k: _env_default(v) for k, v in FIELDS[provider].items()}
    with session_scope() as db:
        _overlay(cfg, db, provider, scope)
    out = {}
    for k, v in cfg.items():
        out[k] = {"secret": True, "configured": bool(v)} if k in SECRETS.get(provider, set()) else v
    return out


def _stored(provider: str, scope: str) -> dict:
    """Raw stored values for a scope (INCLUDING secrets) — internal, for activate-copy."""
    with session_scope() as db:
        return {r.key: r.value for r in db.query(IntegrationConfig).filter(
            IntegrationConfig.provider == provider, IntegrationConfig.scope == scope).all()
            if r.value != ""}


def activate_scope(provider: str, src_scope: str, dest_scope: str = "platform") -> None:
    """Publish a stored account: copy src_scope's values into dest_scope (the effective layer).
    Activating 'platform:test' makes the test account Tovaitech's live-effective default."""
    set_many(provider, _stored(provider, src_scope), scope=dest_scope)


# ---- per-clinic feature flags (e.g. whether a clinic uses the AI WhatsApp agent) ------------

def get_clinic_flag(tenant_id: str, name: str, default: bool = False) -> bool:
    with session_scope() as db:
        row = (db.query(IntegrationConfig)
               .filter(IntegrationConfig.provider == "flags",
                       IntegrationConfig.scope == _clinic_scope(tenant_id),
                       IntegrationConfig.key == name).first())
    return default if row is None else (row.value == "on")


def set_clinic_flag(tenant_id: str, name: str, value: bool) -> None:
    scope = _clinic_scope(tenant_id)
    with session_scope() as db:
        row = (db.query(IntegrationConfig)
               .filter(IntegrationConfig.provider == "flags",
                       IntegrationConfig.scope == scope, IntegrationConfig.key == name).first())
        if row is None:
            db.add(IntegrationConfig(scope=scope, provider="flags", key=name,
                                     value=("on" if value else "off")))
        else:
            row.value = "on" if value else "off"
