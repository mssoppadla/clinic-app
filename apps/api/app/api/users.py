"""User management [C35, AC18] — clinic_admin manages their clinic's staff; superadmin manages
clinic_admins for any tenant. Admin-created users get a one-time temp password (force-reset on
first login); they can also reset via WhatsApp OTP (see auth.forgot/reset).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

_USERNAME_RE = re.compile(r"[a-z0-9._-]{3,80}")

from ..core.db import system_session
from ..core.errors import AppError
from ..core.security import generate_temp_password, hash_password
from ..models import User, UserRole
from .deps import require_clinic_staff, require_role

router = APIRouter(prefix="/users", tags=["users"])

CLINIC_ROLES = {"clinic_admin", "doctor", "front_desk", "triage"}
ALL_ROLES = CLINIC_ROLES | {"superadmin"}


def _roles(user: dict) -> list[dict]:
    return user.get("roles") or []


def _is_superadmin(user: dict) -> bool:
    return any(r.get("role") == "superadmin" for r in _roles(user))


def _admin_tenant_ids(user: dict) -> set[str]:
    return {r.get("tenant_id") for r in _roles(user)
            if r.get("role") == "clinic_admin" and r.get("tenant_id")}


def _user_view(db, u: User) -> dict:
    roles = [{"role": r.role, "tenant_id": r.tenant_id}
             for r in db.query(UserRole).filter(UserRole.user_id == u.id).all()]
    return {"id": u.id, "email": u.email, "username": u.username, "phone": u.phone,
            "status": u.status, "must_reset_password": u.must_reset_password, "roles": roles}


class CreateUserIn(BaseModel):
    email: str | None = None
    username: str | None = None      # alternate login id for clinics without an email
    phone: str | None = None
    role: str = Field(pattern="^(clinic_admin|doctor|front_desk|triage|superadmin)$")
    tenant_id: str | None = None     # superadmin supplies; clinic_admin uses their own


def _resolve_target_tenant(caller: dict, role: str, tenant_id: str | None) -> str | None:
    """Authorize + resolve which tenant the new role belongs to."""
    if _is_superadmin(caller):
        if role == "superadmin":
            return None
        if not tenant_id:
            raise AppError("tenant_required", "tenant_id is required for a clinic role.", status=422)
        return tenant_id
    # clinic_admin: only their own clinic, only clinic roles
    tids = _admin_tenant_ids(caller)
    if not tids:
        raise AppError("forbidden", "You don't manage any clinic.", status=403)
    own = next(iter(tids))
    if role not in CLINIC_ROLES:
        raise AppError("forbidden", "You can't assign that role.", status=403)
    if tenant_id and tenant_id != own:
        raise AppError("forbidden", "You can't manage another clinic's users.", status=403)
    return own


@router.post("", status_code=201)
def create_user(body: CreateUserIn, caller: dict = Depends(require_role("superadmin", "clinic_admin"))):
    email = ((body.email or "").strip().lower()) or None
    username = ((body.username or "").strip().lower()) or None
    if not email and not username:
        raise AppError("identifier_required", "Provide an email or a username.", status=422)
    if email and ("@" not in email or "." not in email):
        raise AppError("invalid_email", "A valid email is required.", status=422)
    if username and not _USERNAME_RE.fullmatch(username):
        raise AppError("invalid_username", "Username must be 3–80 chars: letters, numbers, . _ -",
                       status=422)
    tenant_id = _resolve_target_tenant(caller, body.role, body.tenant_id)
    temp = generate_temp_password()
    with system_session() as db:
        existing = None
        if email:
            existing = db.query(User).filter(User.email == email).first()
        if existing is None and username:
            existing = db.query(User).filter(User.username == username).first()
        if existing is not None and existing.status != "revoked":
            raise AppError("identifier_taken", "An active user with this email/username already exists.",
                           status=409)
        if existing is not None:
            # Re-creating a revoked email/username reactivates + updates that account.
            _assert_can_manage(db, caller, existing)
            existing.status = "active"
            existing.must_reset_password = True
            existing.password_hash = hash_password(temp)
            if email:
                existing.email = email
            if username:
                existing.username = username
            if body.phone is not None:
                existing.phone = body.phone or None
            db.query(UserRole).filter(UserRole.user_id == existing.id,
                                      UserRole.tenant_id == tenant_id).delete()
            db.add(UserRole(user_id=existing.id, tenant_id=tenant_id, role=body.role))
            db.flush()
            return {**_user_view(db, existing), "temp_password": temp, "reactivated": True}
        u = User(email=email, username=username, phone=(body.phone or None),
                 password_hash=hash_password(temp), must_reset_password=True, status="active")
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role=body.role))
        return {**_user_view(db, u), "temp_password": temp, "reactivated": False}


@router.get("/clinic")
def my_clinic(ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    """Resolve the clinic this team page is scoped to (from X-Clinic-Slug).
    Authorized for that clinic's admin or a superadmin — powers the hospital-specific team page."""
    t = ctx["tenant"]
    return {"tenant_id": t["id"], "slug": t["slug"], "name": t["name"]}


@router.get("")
def list_users(caller: dict = Depends(require_role("superadmin", "clinic_admin"))):
    with system_session() as db:
        if _is_superadmin(caller):
            rows = db.query(User).all()
        else:
            tids = _admin_tenant_ids(caller)
            uids = {r.user_id for r in db.query(UserRole).filter(UserRole.tenant_id.in_(tids)).all()}
            rows = [u for u in db.query(User).all() if u.id in uids]
        return {"users": [_user_view(db, u) for u in rows]}


def _assert_can_manage(db, caller: dict, target: User) -> None:
    if _is_superadmin(caller):
        return
    tids = _admin_tenant_ids(caller)
    target_tids = {r.tenant_id for r in db.query(UserRole).filter(UserRole.user_id == target.id).all()}
    if tids.isdisjoint(target_tids):
        raise AppError("forbidden", "That user isn't in your clinic.", status=403)


class RoleIn(BaseModel):
    role: str = Field(pattern="^(clinic_admin|doctor|front_desk|triage)$")


@router.patch("/{user_id}/role")
def set_role(user_id: str, body: RoleIn, caller: dict = Depends(require_role("superadmin", "clinic_admin"))):
    with system_session() as db:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise AppError("user_not_found", "No such user.", status=404)
        _assert_can_manage(db, caller, u)
        tenant_id = _resolve_target_tenant(caller, body.role, None if _is_superadmin(caller) else None)
        # for clinic_admin, tenant is their own; for superadmin keep the user's existing clinic tenant
        if _is_superadmin(caller):
            existing = db.query(UserRole).filter(UserRole.user_id == u.id,
                                                 UserRole.tenant_id.isnot(None)).first()
            tenant_id = existing.tenant_id if existing else None
        db.query(UserRole).filter(UserRole.user_id == u.id, UserRole.tenant_id == tenant_id).delete()
        db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role=body.role))
        db.flush()
        return _user_view(db, u)


@router.post("/{user_id}/revoke")
def revoke_user(user_id: str, caller: dict = Depends(require_role("superadmin", "clinic_admin"))):
    with system_session() as db:
        u = db.query(User).filter(User.id == user_id).first()
        if u is None:
            raise AppError("user_not_found", "No such user.", status=404)
        _assert_can_manage(db, caller, u)
        if u.id == caller.get("sub"):
            raise AppError("cannot_revoke_self", "You can't revoke your own account.", status=409)
        u.status = "revoked"
        return _user_view(db, u)
