"""Appointment slots (Phase 1) — clinics manage doctors + their bookable slots; patients book
them (see /clinics/{slug}/availability + POST /bookings mode=slot). [F7, F11c]

Who can do what (all scoped to the clinic in X-Clinic-Slug):
  * add a doctor profile   -> clinic_admin or superadmin (admins set the clinic up)
  * manage slots / leave   -> the doctor themselves (their own profile only), OR an admin
A doctor's LOGIN and their CLINICAL profile are the same record: a doctor created with login
credentials gets a User(role=doctor) linked to their Doctor row (Doctor.user_id), and can then
sign in to manage their own schedule, per-slot capacity (patients per slot) and leave.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.db import TenantScope, session_scope, system_session
from ..core.errors import AppError
from ..core.security import generate_temp_password, hash_password
from ..models import Doctor, Session as ClinicSession, Slot, User, UserRole
from .deps import STAFF_ROLES, require_clinic_staff

router = APIRouter(prefix="/slots", tags=["slots"])

# kept as a module alias so other endpoints (bookings cancel/reschedule) share the same set
STAFF = STAFF_ROLES
_USERNAME_RE = re.compile(r"[a-z0-9._-]{3,80}")


def _slot_view(s: Slot) -> dict:
    return {"id": s.id, "start": s.start_ts.isoformat(), "end": s.end_ts.isoformat(),
            "capacity": s.capacity, "booked": s.booked,
            "available": max(0, s.capacity - s.booked), "status": s.status}


def _is_admin(ctx: dict) -> bool:
    """Caller can manage ALL doctors in this clinic (clinic_admin of it, or a superadmin)."""
    tid = ctx["tenant"]["id"]
    roles = ctx["user"].get("roles") or []
    return (any(r.get("role") == "superadmin" for r in roles)
            or any(r.get("role") == "clinic_admin" and r.get("tenant_id") == tid for r in roles))


def _my_doctor_id(db, tenant_id: str, ctx: dict) -> str | None:
    """The Doctor profile linked to the signed-in user, if any (their self-service profile)."""
    sub = ctx["user"].get("sub")
    if not sub:
        return None
    doc = db.query(Doctor).filter(Doctor.tenant_id == tenant_id, Doctor.user_id == sub,
                                  Doctor.deleted_at.is_(None)).first()
    return doc.id if doc else None


def _assert_can_manage_doctor(db, tenant_id: str, ctx: dict, doctor_id: str) -> None:
    """Admins manage any doctor in the clinic; a doctor may only manage their own profile."""
    if _is_admin(ctx):
        return
    if _my_doctor_id(db, tenant_id, ctx) == doctor_id:
        return
    raise AppError("forbidden", "You can only manage your own schedule.", status=403)


class GenerateIn(BaseModel):
    doctor_id: str
    date: str                                   # YYYY-MM-DD
    start: str                                  # HH:MM
    end: str                                    # HH:MM
    slot_minutes: int = Field(default=15, ge=5, le=240)
    capacity: int = Field(default=1, ge=1, le=50)   # patients the doctor handles per slot


@router.post("/generate", status_code=201)
def generate_slots(body: GenerateIn, ctx: dict = Depends(require_clinic_staff(*STAFF))):
    tenant_id = ctx["tenant"]["id"]
    try:
        start_dt = datetime.strptime(f"{body.date} {body.start}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{body.date} {body.end}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise AppError("invalid_time", "Use date YYYY-MM-DD and times HH:MM.", status=422)
    if end_dt <= start_dt:
        raise AppError("invalid_window", "End time must be after start time.", status=422)

    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        doctor = scope.get(Doctor, id=body.doctor_id)
        if doctor is None:
            raise AppError("doctor_not_found", "Unknown doctor.", status=404)
        _assert_can_manage_doctor(db, tenant_id, ctx, doctor.id)
        session = scope.query(ClinicSession).filter(ClinicSession.doctor_id == doctor.id,
                                                    ClinicSession.date == body.date).first()
        if session is None:
            session = ClinicSession(tenant_id=tenant_id, doctor_id=doctor.id, date=body.date,
                                    label="", start_ts=start_dt, capacity=body.capacity)
            scope.add(session)
            scope.flush()
        existing = {s.start_ts for s in scope.query(Slot).filter(Slot.doctor_id == doctor.id,
                                                                 Slot.date == body.date)}
        created = skipped = 0
        t = start_dt
        while t + timedelta(minutes=body.slot_minutes) <= end_dt:
            e = t + timedelta(minutes=body.slot_minutes)
            if t in existing:
                skipped += 1
            else:
                scope.add(Slot(tenant_id=tenant_id, doctor_id=doctor.id, session_id=session.id,
                               date=body.date, start_ts=t, end_ts=e, capacity=body.capacity,
                               booked=0, status="open"))
                created += 1
            t = e
        return {"date": body.date, "doctor_id": doctor.id, "created": created, "skipped": skipped}


@router.get("/doctors")
def my_clinic_doctors(ctx: dict = Depends(require_clinic_staff(*STAFF))):
    """Doctors in this clinic + the caller's context, so the UI can scope self-service:
    `me.can_manage_all` (admins) vs `me.doctor_id` (a doctor manages only their own profile)."""
    tenant_id = ctx["tenant"]["id"]
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        docs = [d for d in scope.query(Doctor) if d.deleted_at is None]
        # surface each doctor's login identifier (if they have one) — login == clinical profile
        users = {u.id: u for u in db.query(User).filter(
            User.id.in_([d.user_id for d in docs if d.user_id])).all()} if docs else {}
        out = []
        for d in docs:
            u = users.get(d.user_id) if d.user_id else None
            out.append({"id": d.id, "name": d.name, "specialty": d.specialty,
                        "has_login": u is not None,
                        "login": (u.email or u.username) if u else None})
        me = {"can_manage_all": _is_admin(ctx), "doctor_id": _my_doctor_id(db, tenant_id, ctx)}
        return {"doctors": out, "me": me}


class NewDoctorIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    specialty: str | None = Field(default=None, max_length=120)
    fee_inr: float | None = None
    # optional login so the doctor can self-manage; omit for an ad-hoc/visiting doctor
    email: str | None = None
    username: str | None = None
    phone: str | None = None


@router.post("/doctors", status_code=201)
def add_clinic_doctor(body: NewDoctorIn, ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    """Add a bookable doctor (admins only: clinic_admin or superadmin). Works BEFORE go-live so a
    clinic is fully set up before opening. If login credentials are given, a linked doctor login
    is created (temp password, forced reset) so that doctor can self-manage their schedule; omit
    them for an ad-hoc/visiting doctor with no login. [F7]"""
    tenant_id = ctx["tenant"]["id"]
    email = ((body.email or "").strip().lower()) or None
    username = ((body.username or "").strip().lower()) or None
    if email and ("@" not in email or "." not in email):
        raise AppError("invalid_email", "A valid email is required.", status=422)
    if username and not _USERNAME_RE.fullmatch(username):
        raise AppError("invalid_username", "Username must be 3–80 chars: letters, numbers, . _ -",
                       status=422)
    with system_session() as db:    # bypasses RLS; tenant_id comes from the authorized context
        doctor = Doctor(tenant_id=tenant_id, name=body.name.strip(),
                        specialty=(body.specialty or "").strip(),
                        fee_minor=int(round((body.fee_inr or 0) * 100)))
        db.add(doctor)
        db.flush()
        login = None
        if email or username:
            clash = None
            if email:
                clash = db.query(User).filter(User.email == email).first()
            if clash is None and username:
                clash = db.query(User).filter(User.username == username).first()
            if clash is not None:
                raise AppError("identifier_taken",
                               "A user with this email/username already exists.", status=409)
            temp = generate_temp_password()
            u = User(email=email, username=username, phone=(body.phone or None),
                     password_hash=hash_password(temp), must_reset_password=True, status="active")
            db.add(u)
            db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role="doctor"))
            doctor.user_id = u.id      # the login IS this clinical profile
            login = {"email": email, "username": username, "temp_password": temp}
        return {"id": doctor.id, "name": doctor.name, "specialty": doctor.specialty,
                "has_login": login is not None, "login": login}


class LinkLoginIn(BaseModel):
    email: str | None = None
    username: str | None = None


@router.post("/doctors/{doctor_id}/link-login")
def link_doctor_login(doctor_id: str, body: LinkLoginIn,
                      ctx: dict = Depends(require_clinic_staff("clinic_admin"))):
    """Backward-compat / recovery: attach a login to an EXISTING doctor profile so that login
    becomes the doctor's self-service account (preserving the profile's existing slots/bookings).
    If a user with the given email/username already exists it's reused (and given a doctor role for
    this clinic); otherwise a new login is created with a temp password. Admins only."""
    tenant_id = ctx["tenant"]["id"]
    email = ((body.email or "").strip().lower()) or None
    username = ((body.username or "").strip().lower()) or None
    if not email and not username:
        raise AppError("identifier_required", "Provide an email or username to link.", status=422)
    with system_session() as db:
        doctor = db.query(Doctor).filter(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id,
                                         Doctor.deleted_at.is_(None)).first()
        if doctor is None:
            raise AppError("doctor_not_found", "Unknown doctor.", status=404)
        user = None
        if email:
            user = db.query(User).filter(User.email == email).first()
        if user is None and username:
            user = db.query(User).filter(User.username == username).first()
        login = None
        if user is None:
            temp = generate_temp_password()
            user = User(email=email, username=username, password_hash=hash_password(temp),
                        must_reset_password=True, status="active")
            db.add(user); db.flush()
            login = {"login": email or username, "temp_password": temp}
        # ensure this user holds a doctor role for this clinic
        has_role = db.query(UserRole).filter(UserRole.user_id == user.id,
                                             UserRole.tenant_id == tenant_id,
                                             UserRole.role == "doctor").first()
        if has_role is None:
            db.add(UserRole(user_id=user.id, tenant_id=tenant_id, role="doctor"))
        doctor.user_id = user.id
        return {"id": doctor.id, "name": doctor.name, "has_login": True,
                "linked": (user.email or user.username), "new_login": login}


@router.get("")
def list_slots(doctor: str, date: str, ctx: dict = Depends(require_clinic_staff(*STAFF))):
    tenant_id = ctx["tenant"]["id"]
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        slots = sorted(scope.query(Slot).filter(Slot.doctor_id == doctor, Slot.date == date),
                       key=lambda s: s.start_ts)
        return {"date": date, "doctor_id": doctor, "slots": [_slot_view(s) for s in slots]}


class LeaveIn(BaseModel):
    doctor_id: str
    date: str                                   # YYYY-MM-DD


@router.post("/leave")
def take_leave(body: LeaveIn, ctx: dict = Depends(require_clinic_staff(*STAFF))):
    """Mark the doctor on leave for a day: closes their OPEN (unbooked) slots so patients can no
    longer book them. Already-booked slots are kept (cancel/reschedule those explicitly). A doctor
    may do this for their own day; admins for any doctor in the clinic."""
    tenant_id = ctx["tenant"]["id"]
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        _assert_can_manage_doctor(db, tenant_id, ctx, body.doctor_id)
        closed = still_booked = 0
        for s in scope.query(Slot).filter(Slot.doctor_id == body.doctor_id, Slot.date == body.date):
            if s.booked > 0:
                still_booked += 1
            elif s.status == "open":
                s.status = "closed"
                closed += 1
        return {"date": body.date, "doctor_id": body.doctor_id,
                "closed": closed, "still_booked": still_booked}
