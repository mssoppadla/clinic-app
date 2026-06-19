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
}
SECRETS = {"whatsapp": {"token"}, "bhashini": {"api_key"}}


def _env_default(field: str | None) -> str:
    if not field:
        return ""
    val = getattr(get_settings(), field, "")
    return str(val) if val is not None else ""


def get_effective(provider: str, scope: str = "platform") -> dict:
    """Full config INCLUDING secrets — internal use by clients only."""
    cfg = {k: _env_default(v) for k, v in FIELDS[provider].items()}
    with session_scope() as db:
        rows = db.query(IntegrationConfig).filter(
            IntegrationConfig.provider == provider,
            IntegrationConfig.scope == scope).all()
        for r in rows:
            if r.value != "":
                cfg[r.key] = r.value
    return cfg


def get_public(provider: str, scope: str = "platform") -> dict:
    """Safe view for the UI: secrets replaced by a 'configured' flag, never the value."""
    eff = get_effective(provider, scope)
    out = {}
    for k, v in eff.items():
        if k in SECRETS.get(provider, set()):
            out[k] = {"secret": True, "configured": bool(v)}
        else:
            out[k] = v
    return out


def set_many(provider: str, data: dict, scope: str = "platform") -> None:
    """Upsert provided keys. Empty secret values are ignored (don't wipe an existing secret)."""
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
