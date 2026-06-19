"""Appointment slots (Phase 1) — clinic staff generate timed slots for a doctor; patients book
them (see /clinics/{slug}/availability + POST /bookings mode=slot). [F7, F11c]"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.db import TenantScope, session_scope
from ..core.errors import AppError
from ..models import Doctor, Session as ClinicSession, Slot
from .deps import require_role

router = APIRouter(prefix="/slots", tags=["slots"])

STAFF = ("clinic_admin", "doctor", "front_desk", "triage")


def _staff_tenant(caller: dict) -> str:
    for r in caller.get("roles") or []:
        if r.get("role") in STAFF and r.get("tenant_id"):
            return r["tenant_id"]
    raise AppError("forbidden", "You don't manage a clinic.", status=403)


def _slot_view(s: Slot) -> dict:
    return {"id": s.id, "start": s.start_ts.isoformat(), "end": s.end_ts.isoformat(),
            "capacity": s.capacity, "booked": s.booked,
            "available": max(0, s.capacity - s.booked), "status": s.status}


class GenerateIn(BaseModel):
    doctor_id: str
    date: str                                   # YYYY-MM-DD
    start: str                                  # HH:MM
    end: str                                    # HH:MM
    slot_minutes: int = Field(default=15, ge=5, le=240)
    capacity: int = Field(default=1, ge=1, le=50)


@router.post("/generate", status_code=201)
def generate_slots(body: GenerateIn, caller: dict = Depends(require_role(*STAFF))):
    tenant_id = _staff_tenant(caller)
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
def my_clinic_doctors(caller: dict = Depends(require_role(*STAFF))):
    """Doctors in the caller's clinic — for the slot-creation picker."""
    tenant_id = _staff_tenant(caller)
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        docs = [d for d in scope.query(Doctor) if d.deleted_at is None]
        return {"doctors": [{"id": d.id, "name": d.name, "specialty": d.specialty} for d in docs]}


@router.get("")
def list_slots(doctor: str, date: str, caller: dict = Depends(require_role(*STAFF))):
    tenant_id = _staff_tenant(caller)
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        slots = sorted(scope.query(Slot).filter(Slot.doctor_id == doctor, Slot.date == date),
                       key=lambda s: s.start_ts)
        return {"date": date, "doctor_id": doctor, "slots": [_slot_view(s) for s in slots]}
