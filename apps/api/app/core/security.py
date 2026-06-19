"""Password hashing (bcrypt) + JWT session tokens (HS256). [AC1, AC2]

Access token is short-lived and carries sub/roles/tenants/exp; refresh token is longer-lived
and only mints new access tokens. Secret + TTLs come from settings (env).
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import get_settings

_PW_ALPHABET = string.ascii_letters + string.digits


def generate_temp_password(length: int = 12) -> str:
    """A readable random temp password for admin-created users (force-reset on first login)."""
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))


def generate_otp(digits: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(digits))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _encode(claims: dict, ttl: timedelta) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {**claims, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def create_access_token(*, sub: str, roles: list[dict]) -> str:
    """roles: [{'role': 'superadmin'|'clinic_admin'|..., 'tenant_id': str|None}, ...]"""
    s = get_settings()
    return _encode({"sub": sub, "typ": "access", "roles": roles},
                   timedelta(minutes=s.jwt_access_ttl_min))


def create_refresh_token(*, sub: str) -> str:
    s = get_settings()
    return _encode({"sub": sub, "typ": "refresh"}, timedelta(days=s.jwt_refresh_ttl_days))


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
