"""Central configuration. NO HARDCODING: every tunable comes from env / settings.

Secrets (WhatsApp, Bhashini, DB password) are read from the environment only - never
committed. `.env.example` documents the keys with placeholder values.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app ---
    env: Literal["local", "ci", "staging", "prod"] = "local"
    app_name: str = "clinic-saas"
    api_version: str = "v1"
    log_level: str = "INFO"
    cors_origins: str = ""  # comma-separated; e.g. "http://localhost:8080" for local web

    # --- database (Postgres in real envs; SQLite only for sandbox tests) ---
    # database_url = the connection the APP serves requests on. In prod this is the
    # non-superuser app role so Postgres RLS actually enforces (superusers bypass RLS).
    database_url: str = "sqlite+pysqlite:///./local.db"
    # admin_database_url = a superuser/owner connection used ONLY by bootstrap/migrations to
    # create the app role, the schema, RLS policies, and grants. Falls back to database_url
    # for single-role/dev/SQLite (where the distinction doesn't apply).
    admin_database_url: str = ""

    @property
    def admin_url(self) -> str:
        return self.admin_database_url or self.database_url

    # --- tenancy ---
    canary_slug: str = "__canary__"

    # --- auth (Phase 2) ---
    jwt_secret: str = "dev-insecure-change-me-please-override-in-prod"  # override via APP_JWT_SECRET
    jwt_access_ttl_min: int = 30
    jwt_refresh_ttl_days: int = 7
    # First/root superadmin, seeded if absent (force password reset on first login).
    superadmin_email: str = ""
    superadmin_password: str = ""
    # DEV/TEST ONLY: a fixed OTP code testers can always enter to walk the WhatsApp-OTP flows
    # locally. Honored ONLY when WhatsApp is in stub mode (never when live/prod), so production
    # codes stay random. Set APP_DEV_OTP_CODE locally; leave empty everywhere else.
    dev_otp_code: str = ""

    # --- queue / ETA (configurable, never hardcoded in logic) ---
    avg_consult_minutes: int = 6
    default_session_capacity: int = 40
    refund_on_cancel: bool = True   # refund hook: refund a captured fee when a booking is cancelled

    # --- integrations: provider mode is swappable (stub now, live when creds arrive) ---
    whatsapp_mode: Literal["stub", "live"] = "stub"
    whatsapp_base_url: str = "https://graph.facebook.com/v21.0"
    whatsapp_token: str = ""            # secret - from env only
    whatsapp_phone_number_id: str = ""  # per-clinic; from env/tenant_config

    bhashini_mode: Literal["stub", "live"] = "stub"
    bhashini_base_url: str = ""
    bhashini_api_key: str = ""          # secret - from env only
    bhashini_user_id: str = ""

    # --- WhatsApp inbound webhook (two-way) — PLATFORM-level (one Tovaitech Meta app) ---
    # The webhook is ONE public URL (tovaitech.in/appointments/v1/webhooks/whatsapp); inbound
    # messages are routed to the right clinic by metadata.phone_number_id. verify_token is the
    # GET-handshake secret; app_secret verifies Meta's X-Hub-Signature-256 on POSTs.
    whatsapp_verify_token: str = ""     # secret - you choose it; paste the same value in Meta
    whatsapp_app_secret: str = ""       # secret - Meta App → Settings → Basic

    # --- AI agent that reads patient messages, infers intent, and replies ---
    # Provider is operator-selectable (Anthropic Claude or OpenAI), set in the platform admin UI.
    ai_mode: Literal["stub", "live"] = "stub"
    ai_provider: Literal["anthropic", "openai"] = "anthropic"
    ai_model: str = "claude-opus-4-8"
    anthropic_api_key: str = ""         # secret - from env only (the AI api_key, any provider)
    # Confirm a booking/cancel with the patient before committing. Per-clinic override lives in
    # integration_config (provider "whatsapp", key "ai_confirm"); this is the platform default.
    ai_confirm_before_action: bool = True

    # DEV/LOCAL ONLY: skip outbound TLS verification for integration calls (WhatsApp/Claude).
    # Needed on dev machines behind an HTTPS-intercepting proxy/antivirus that presents a cert
    # chain Python rejects. Opt-in (APP_INSECURE_SKIP_TLS_VERIFY=1) AND ignored unless env != prod
    # — production ALWAYS verifies. Never set this on the VPS.
    insecure_skip_tls_verify: bool = False

    @property
    def outbound_tls_verify(self) -> bool:
        """True = verify certs (always in prod). Only non-prod may opt out via the flag above."""
        return not (self.insecure_skip_tls_verify and self.env != "prod")

    # default UI languages; English ('en') is ALWAYS present (A15)
    default_languages: str = "en,ml"

    @property
    def languages(self) -> list[str]:
        langs = [x.strip() for x in self.default_languages.split(",") if x.strip()]
        return ["en"] + [x for x in langs if x != "en"]  # English always first

    @property
    def cors_origins_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
