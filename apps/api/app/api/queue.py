"""Live queue management [F-queue] — a doctor (or an admin) works today's patients:
see who's waiting/scheduled, 'Call next' to start serving one, then mark Done / No-show.

Scope rules mirror slots: a doctor sees & manages ONLY their own queue; a clinic_admin/superadmin
may act on any doctor in the clinic. The queue is a projection (queue_entries.state), updated here.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.db import TenantScope, session_scope
from ..core.errors import AppError
from ..models import BookingPatient, Doctor, QueueEntry, Session as ClinicSession, Token
from .deps import STAFF_ROLES, require_clinic_staff
from .slots import _assert_can_manage_doctor, _is_admin, _my_doctor_id

router = APIRouter(prefix="/queue", tags=["queue"])

# active = still in the room's flow; terminal = finished for the day
ACTIVE = ("scheduled", "waiting", "serving")
ALLOWED_STATES = ("waiting", "serving", "done", "no_show")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _doctor_scope(db, tenant_id: str, ctx: dict, requested: str | None) -> str | None:
    """A plain doctor is locked to their own profile; an admin may pass ?doctor= (or see all)."""
    if _is_admin(ctx):
        return requested
    mine = _my_doctor_id(db, tenant_id, ctx)
    if mine is None:
        raise AppError("no_doctor_profile", "Your login isn't linked to a doctor profile.", status=403)
    return mine


def _sessions_for(scope, doctor_id: str | None, date: str) -> dict:
    q = scope.query(ClinicSession).filter(ClinicSession.date == date)
    if doctor_id:
        q = q.filter(ClinicSession.doctor_id == doctor_id)
    return {s.id: s for s in q}


def _entry_view(scope, q: QueueEntry, doctor_name: str) -> dict:
    bp = scope.get(BookingPatient, id=q.booking_patient_id)
    tok = scope.query(Token).filter(Token.booking_patient_id == q.booking_patient_id).first()
    return {"id": q.id, "patient": bp.name if bp else "—", "token": tok.number if tok else "",
            "state": q.state, "position": q.position,
            "eta": q.eta_ts.isoformat() if q.eta_ts else None, "doctor": doctor_name}


def _sorted(entries: list[QueueEntry]) -> list[QueueEntry]:
    return sorted(entries, key=lambda q: (q.eta_ts or datetime.max.replace(tzinfo=None), q.position))


@router.get("")
def get_queue(date: str | None = None, doctor: str | None = None,
              ctx: dict = Depends(require_clinic_staff(*STAFF_ROLES))):
    """Today's queue (default) for a doctor — patients with their token, ETA and state."""
    tenant_id = ctx["tenant"]["id"]
    d = date or _today()
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        doctor_id = _doctor_scope(db, tenant_id, ctx, doctor)
        sessions = _sessions_for(scope, doctor_id, d)
        names = {doc.id: doc.name for doc in scope.query(Doctor)}
        sess_doc = {sid: names.get(s.doctor_id, "—") for sid, s in sessions.items()}
        entries = [q for q in scope.query(QueueEntry) if q.session_id in sessions]
        rows = [_entry_view(scope, q, sess_doc.get(q.session_id, "—")) for q in _sorted(entries)]
        serving = next((r for r in rows if r["state"] == "serving"), None)
        waiting = [r for r in rows if r["state"] in ("waiting", "scheduled")]
        done = [r for r in rows if r["state"] in ("done", "no_show")]
        return {"date": d, "doctor_id": doctor_id, "serving": serving,
                "waiting": waiting, "done": done, "counts": {
                    "waiting": len(waiting), "done": len(done), "total": len(rows)}}


def _entry_doctor(scope, q: QueueEntry) -> str:
    s = scope.get(ClinicSession, id=q.session_id)
    return s.doctor_id if s else ""


class StateIn(BaseModel):
    state: str = Field(pattern="^(waiting|serving|done|no_show)$")


@router.post("/{entry_id}/state")
def set_state(entry_id: str, body: StateIn, ctx: dict = Depends(require_clinic_staff(*STAFF_ROLES))):
    """Transition one patient's queue state (waiting → serving → done / no_show)."""
    tenant_id = ctx["tenant"]["id"]
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        q = scope.get(QueueEntry, id=entry_id)
        if q is None:
            raise AppError("entry_not_found", "No such queue entry.", status=404)
        _assert_can_manage_doctor(db, tenant_id, ctx, _entry_doctor(scope, q))
        q.state = body.state
        # keep the patient row's status roughly in sync for downstream views
        bp = scope.get(BookingPatient, id=q.booking_patient_id)
        if bp is not None and body.state in ("done", "no_show"):
            bp.status = body.state
        return {"id": q.id, "state": q.state}


class CallNextIn(BaseModel):
    doctor_id: str | None = None
    date: str | None = None


@router.post("/call-next")
def call_next(body: CallNextIn | None = None,
              ctx: dict = Depends(require_clinic_staff(*STAFF_ROLES))):
    """Start serving the earliest waiting/scheduled patient for the doctor (marks them 'serving')."""
    body = body or CallNextIn()
    tenant_id = ctx["tenant"]["id"]
    d = body.date or _today()
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        doctor_id = _doctor_scope(db, tenant_id, ctx, body.doctor_id)
        if doctor_id is None:
            raise AppError("doctor_required", "Pick a doctor to call the next patient.", status=422)
        sessions = _sessions_for(scope, doctor_id, d)
        waiting = _sorted([q for q in scope.query(QueueEntry)
                           if q.session_id in sessions and q.state in ("waiting", "scheduled")])
        if not waiting:
            return {"called": None, "message": "No one waiting."}
        nxt = waiting[0]
        nxt.state = "serving"
        bp = scope.get(BookingPatient, id=nxt.booking_patient_id)
        tok = scope.query(Token).filter(Token.booking_patient_id == nxt.booking_patient_id).first()
        return {"called": {"id": nxt.id, "patient": bp.name if bp else "—",
                           "token": tok.number if tok else "", "state": "serving"}}
