"""Auth & identity [C35] — staff/admin login, refresh, logout.

Phase-2 slice 2a: email + password login (no Google/passkey), short-lived JWT access +
refresh. User management, first-login forced reset, and WhatsApp-OTP password reset land in
the next slices. Patient OTP login is a later slice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.db import system_session
from ..core.errors import AppError
from ..core.integration_config import get_effective
from ..core.security import (create_access_token, create_patient_token, create_refresh_token,
                             decode_token, generate_otp, hash_password, verify_password)
from ..integrations import whatsapp
from ..models import OtpChallenge, User, UserRole
from .deps import get_current_user, get_tenant

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("auth")
_OTP_TTL_MIN = 10
_OTP_MAX_ATTEMPTS = 5


def _new_otp(tenant_id: str | None = None) -> str:
    """Random 6-digit OTP — UNLESS a dev/test fixed code is configured AND WhatsApp is in stub
    mode (never live/prod). Lets a tester always enter the same code to walk every OTP flow."""
    s = get_settings()
    if s.dev_otp_code and get_effective("whatsapp", tenant_id=tenant_id).get("mode") != "live":
        return s.dev_otp_code
    return generate_otp()


def _utcnow() -> datetime:
    # naive UTC to match how DateTime columns round-trip (SQLite/Postgres store no tz),
    # so OTP expiry comparisons don't mix aware/naive datetimes.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _roles_for(db, user_id: str) -> list[dict]:
    rows = db.query(UserRole).filter(UserRole.user_id == user_id).all()
    return [{"role": r.role, "tenant_id": r.tenant_id} for r in rows]


def _find_by_identifier(db, ident: str):
    """Look up a user by email OR username (case-insensitive)."""
    ident = (ident or "").strip().lower()
    if not ident:
        return None
    return db.query(User).filter((User.email == ident) | (User.username == ident)).first()


def _display_name(user: User) -> str:
    """Human label for the signed-in chip — email, else username, else short id."""
    return user.email or user.username or f"user {user.id[:8]}"


def _login_payload(db, user: User) -> dict:
    roles = _roles_for(db, user.id)
    return {
        "access_token": create_access_token(sub=user.id, roles=roles, name=_display_name(user)),
        "refresh_token": create_refresh_token(sub=user.id),
        "token_type": "bearer",
        "must_reset_password": user.must_reset_password,
        "user": {"id": user.id, "email": user.email, "username": user.username, "roles": roles},
    }


class LoginIn(BaseModel):
    # accept any of these as the identifier (email or username); password required
    email: str | None = None
    username: str | None = None
    identifier: str | None = None
    password: str

    def ident(self) -> str:
        return self.identifier or self.email or self.username or ""


@router.post("/login")
def login(body: LoginIn):
    with system_session() as db:   # users/user_roles are identity infra (not RLS-scoped)
        user = _find_by_identifier(db, body.ident())
        if user is None or user.status != "active" or not verify_password(body.password, user.password_hash):
            raise AppError("invalid_credentials", "Wrong email/username or password.", status=401)
        return _login_payload(db, user)


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/refresh")
def refresh(body: RefreshIn):
    try:
        claims = decode_token(body.refresh_token)
    except jwt.PyJWTError:
        raise AppError("invalid_token", "Invalid or expired refresh token.", status=401)
    if claims.get("typ") != "refresh":
        raise AppError("invalid_token", "Not a refresh token.", status=401)
    with system_session() as db:
        user = db.query(User).filter(User.id == claims.get("sub")).first()
        if user is None or user.status != "active":
            raise AppError("invalid_token", "User no longer active.", status=401)
        return {"access_token": create_access_token(sub=user.id, roles=_roles_for(db, user.id),
                                                    name=_display_name(user)),
                "token_type": "bearer"}


@router.post("/logout")
def logout(user: dict = Depends(get_current_user)):
    # Stateless JWT: the client discards the tokens. Server-side refresh revocation
    # (denylist) is a later hardening item.
    return {"ok": True}


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePwIn, caller: dict = Depends(get_current_user)):
    """Forced first-login change, and voluntary change. Clears must_reset_password."""
    if len(body.new_password) < 8:
        raise AppError("weak_password", "Password must be at least 8 characters.", status=422)
    with system_session() as db:
        u = db.query(User).filter(User.id == caller.get("sub")).first()
        if u is None or not verify_password(body.current_password, u.password_hash):
            raise AppError("invalid_credentials", "Current password is incorrect.", status=401)
        u.password_hash = hash_password(body.new_password)
        u.must_reset_password = False
        return {"ok": True}


class ForgotIn(BaseModel):
    email: str | None = None
    username: str | None = None
    identifier: str | None = None

    def ident(self) -> str:
        return self.identifier or self.email or self.username or ""


@router.post("/forgot")
def forgot_password(body: ForgotIn):
    """Send a password-reset OTP to the user's WhatsApp number. Always 200 (no account
    enumeration). Accepts email or username."""
    with system_session() as db:
        u = _find_by_identifier(db, body.ident())
        if u is not None and u.status == "active" and u.phone:
            code = _new_otp()
            db.add(OtpChallenge(user_id=u.id, destination=u.phone, purpose="password_reset",
                                code_hash=hash_password(code),
                                expires_at=_utcnow() + timedelta(minutes=_OTP_TTL_MIN)))
            tenant_id = next((r.tenant_id for r in
                              db.query(UserRole).filter(UserRole.user_id == u.id).all()
                              if r.tenant_id), "")
            whatsapp().send_template(tenant_id=tenant_id or "", to_phone=u.phone,
                                     template="auth_otp", params={"code": code, "lang": "en"})
            log.info("auth.forgot otp sent user=%s", u.id)
    return {"ok": True,
            "message": "If that account exists, a reset code has been sent to its WhatsApp number."}


class ResetIn(BaseModel):
    email: str | None = None
    username: str | None = None
    identifier: str | None = None
    otp: str
    new_password: str

    def ident(self) -> str:
        return self.identifier or self.email or self.username or ""


@router.post("/reset")
def reset_password(body: ResetIn):
    if len(body.new_password) < 8:
        raise AppError("weak_password", "Password must be at least 8 characters.", status=422)
    with system_session() as db:
        u = _find_by_identifier(db, body.ident())
        if u is None:
            raise AppError("invalid_otp", "Invalid or expired code.", status=400)
        ch = (db.query(OtpChallenge)
              .filter(OtpChallenge.user_id == u.id, OtpChallenge.purpose == "password_reset",
                      OtpChallenge.consumed_at.is_(None))
              .order_by(OtpChallenge.created_at.desc()).first())
        if ch is None or ch.expires_at < _utcnow() or ch.attempts >= _OTP_MAX_ATTEMPTS:
            raise AppError("invalid_otp", "Invalid or expired code.", status=400)
        ch.attempts += 1
        if not verify_password(body.otp, ch.code_hash):
            raise AppError("invalid_otp", "Invalid or expired code.", status=400)
        ch.consumed_at = _utcnow()
        u.password_hash = hash_password(body.new_password)
        u.must_reset_password = False
        return {"ok": True}


# ---- Patient passwordless OTP login [AC8] (per-clinic; OTP over WhatsApp) ----

class OtpRequestIn(BaseModel):
    phone: str


@router.post("/otp/request")
def patient_otp_request(body: OtpRequestIn, tenant: dict = Depends(get_tenant)):
    """Send a login OTP to the patient's WhatsApp for THIS clinic (X-Clinic-Slug)."""
    phone = (body.phone or "").strip()
    if len(phone) < 6:
        raise AppError("invalid_phone", "Enter a valid mobile number.", status=422)
    with system_session() as db:
        code = _new_otp(tenant["id"])
        db.add(OtpChallenge(user_id=None, destination=phone,
                            purpose=f"patient_login:{tenant['id']}", code_hash=hash_password(code),
                            expires_at=_utcnow() + timedelta(minutes=_OTP_TTL_MIN)))
        whatsapp().send_template(tenant_id=tenant["id"], to_phone=phone, template="auth_otp",
                                 params={"code": code, "lang": tenant["languages"][0]})
        log.info("auth.patient_otp sent tenant=%s", tenant["id"])
    return {"ok": True, "message": "A verification code has been sent to your WhatsApp."}


class OtpVerifyIn(BaseModel):
    phone: str
    otp: str


@router.post("/otp/verify")
def patient_otp_verify(body: OtpVerifyIn, tenant: dict = Depends(get_tenant)):
    phone = (body.phone or "").strip()
    with system_session() as db:
        ch = (db.query(OtpChallenge)
              .filter(OtpChallenge.destination == phone,
                      OtpChallenge.purpose == f"patient_login:{tenant['id']}",
                      OtpChallenge.consumed_at.is_(None))
              .order_by(OtpChallenge.created_at.desc()).first())
        if ch is None or ch.expires_at < _utcnow() or ch.attempts >= _OTP_MAX_ATTEMPTS:
            raise AppError("invalid_otp", "Invalid or expired code.", status=400)
        ch.attempts += 1
        if not verify_password(body.otp, ch.code_hash):
            raise AppError("invalid_otp", "Invalid or expired code.", status=400)
        ch.consumed_at = _utcnow()
    return {"access_token": create_patient_token(phone=phone, tenant_id=tenant["id"]),
            "token_type": "bearer", "scope": "patient.self", "phone": phone}
