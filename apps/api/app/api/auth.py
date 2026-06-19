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

from ..core.db import system_session
from ..core.errors import AppError
from ..core.security import (create_access_token, create_refresh_token, decode_token,
                             generate_otp, hash_password, verify_password)
from ..integrations import whatsapp
from ..models import OtpChallenge, User, UserRole
from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("auth")
_OTP_TTL_MIN = 10
_OTP_MAX_ATTEMPTS = 5


def _utcnow() -> datetime:
    # naive UTC to match how DateTime columns round-trip (SQLite/Postgres store no tz),
    # so OTP expiry comparisons don't mix aware/naive datetimes.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _roles_for(db, user_id: str) -> list[dict]:
    rows = db.query(UserRole).filter(UserRole.user_id == user_id).all()
    return [{"role": r.role, "tenant_id": r.tenant_id} for r in rows]


def _login_payload(db, user: User) -> dict:
    roles = _roles_for(db, user.id)
    return {
        "access_token": create_access_token(sub=user.id, roles=roles),
        "refresh_token": create_refresh_token(sub=user.id),
        "token_type": "bearer",
        "must_reset_password": user.must_reset_password,
        "user": {"id": user.id, "email": user.email, "roles": roles},
    }


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginIn):
    with system_session() as db:   # users/user_roles are identity infra (not RLS-scoped)
        user = db.query(User).filter(User.email == str(body.email).lower()).first()
        if user is None or user.status != "active" or not verify_password(body.password, user.password_hash):
            raise AppError("invalid_credentials", "Wrong email or password.", status=401)
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
        return {"access_token": create_access_token(sub=user.id, roles=_roles_for(db, user.id)),
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
    email: str


@router.post("/forgot")
def forgot_password(body: ForgotIn):
    """Send a password-reset OTP to the user's WhatsApp number. Always 200 (no account
    enumeration)."""
    email = (body.email or "").strip().lower()
    with system_session() as db:
        u = db.query(User).filter(User.email == email).first()
        if u is not None and u.status == "active" and u.phone:
            code = generate_otp()
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
    email: str
    otp: str
    new_password: str


@router.post("/reset")
def reset_password(body: ResetIn):
    if len(body.new_password) < 8:
        raise AppError("weak_password", "Password must be at least 8 characters.", status=422)
    email = (body.email or "").strip().lower()
    with system_session() as db:
        u = db.query(User).filter(User.email == email).first()
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
