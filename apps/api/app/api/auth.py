"""Auth & identity [C35] — staff/admin login, refresh, logout.

Phase-2 slice 2a: email + password login (no Google/passkey), short-lived JWT access +
refresh. User management, first-login forced reset, and WhatsApp-OTP password reset land in
the next slices. Patient OTP login is a later slice.
"""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.db import system_session
from ..core.errors import AppError
from ..core.security import create_access_token, create_refresh_token, decode_token, verify_password
from ..models import User, UserRole
from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


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
