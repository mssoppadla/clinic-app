"""WhatsApp conversation agent. Two independent, clinic-selectable flows over ONE pipeline:

  * MENU (default, no AI): a deterministic numbered menu — 1 Book / 2 Queue / 3 Status — with
    numbered doctor/slot pickers. No LLM, no Anthropic key needed.
  * AI (opt-in, superadmin-enabled): Claude infers intent from free text (integrations.ai).

Both share the same action executors (queue status / join queue / book slot via domain.booking),
the same confirm-before-commit gate, and the same per-(clinic,phone) conversation state. Returns
the reply text; the webhook sends it. Always invoked in reply to an inbound (24h session window).
"""
from __future__ import annotations

import datetime

from ..core.config import get_settings
from ..core.db import TenantScope, session_scope, system_session
from ..core.errors import AppError
from ..core import integration_config as cfg
from ..domain import booking as domain
from ..integrations import ai
from ..models import Doctor, Slot, Tenant, WhatsAppPending

_YES = {"yes", "y", "confirm", "ok", "okay", "yeah", "yep", "haan", "ha"}
_NO = {"no", "n", "cancel", "stop", "nope"}
_RESET = {"menu", "hi", "hello", "start", "help", "restart"}
_TTL_MIN = 30


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def ai_enabled(tenant_id: str) -> bool:
    return cfg.get_clinic_flag(tenant_id, "ai_enabled", default=False)


def _confirm_enabled(tenant_id: str) -> bool:
    return cfg.get_clinic_flag(tenant_id, "confirm", default=get_settings().ai_confirm_before_action)


# ---- conversation state (one open row per clinic+phone) -------------------------------------

def _get_state(tenant_id: str, phone: str) -> dict | None:
    with system_session() as db:
        row = (db.query(WhatsAppPending)
               .filter(WhatsAppPending.tenant_id == tenant_id, WhatsAppPending.phone == phone,
                       WhatsAppPending.consumed_at.is_(None))
               .order_by(WhatsAppPending.created_at.desc()).first())
        if row is None or row.expires_at < _now():
            return None
        return dict(row.action or {})


def _set_state(tenant_id: str, phone: str, action: dict, summary: str = "") -> None:
    with system_session() as db:
        db.query(WhatsAppPending).filter(WhatsAppPending.tenant_id == tenant_id,
                                         WhatsAppPending.phone == phone,
                                         WhatsAppPending.consumed_at.is_(None)).delete()
        db.add(WhatsAppPending(tenant_id=tenant_id, phone=phone, action=action, summary=summary,
                               expires_at=_now() + datetime.timedelta(minutes=_TTL_MIN)))


def _clear_state(tenant_id: str, phone: str) -> None:
    with system_session() as db:
        db.query(WhatsAppPending).filter(WhatsAppPending.tenant_id == tenant_id,
                                         WhatsAppPending.phone == phone,
                                         WhatsAppPending.consumed_at.is_(None)).delete()


# ---- clinic context (doctors, open slots, queue) --------------------------------------------

def _context(tenant_id: str, phone: str, profile_name: str | None) -> dict:
    today = datetime.date.today().isoformat()
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        t = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        tenant = {"id": t.id, "slug": t.slug, "name": t.name,
                  "languages": t.languages or ["en"], "branding": t.branding or {}}
        pub = domain.clinic_public(scope, get_settings(), tenant)
        docs = [d for d in scope.query(Doctor) if d.deleted_at is None]
        names = {d.id: d.name for d in docs}
        raw = sorted((s for s in scope.query(Slot).filter(Slot.date >= today)
                      if s.status == "open" and s.booked < s.capacity),
                     key=lambda s: s.start_ts)[:8]
        slots = [{"id": s.id, "doctor_id": s.doctor_id, "doctor_name": names.get(s.doctor_id, ""),
                  "label": s.start_ts.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")} for s in raw]
    return {"clinic_name": t.name, "phone": phone, "patient_name": profile_name,
            "doctors": [{"id": d.id, "name": d.name} for d in docs], "slots": slots,
            "queue_count": pub.get("queue_count", 0), "avg_wait": pub.get("avg_wait_minutes", 0)}


# ---- action executors (shared by both flows) ------------------------------------------------

def _book(tenant_id: str, payload: dict, phone: str):
    with session_scope() as db:
        scope = TenantScope(db, tenant_id)
        return domain.create_booking(scope, get_settings(), payload=payload, idempotency_key=None,
                                     actor={"type": "patient", "id": phone})


def _execute(tenant_id: str, phone: str, action: dict, profile_name: str | None) -> str:
    tool, args = action.get("tool"), action.get("args", {})
    name = (profile_name or "").strip() or "Patient"
    if tool == "queue_status":
        ctx = _context(tenant_id, phone, profile_name)
        return (f"There are {ctx['queue_count']} patient(s) in the queue right now "
                f"(about {ctx['avg_wait']} min wait).")
    payload = {"doctor_id": args.get("doctor_id"), "patients": [{"name": name}],
               "contact_phone": phone, "consent": True}
    try:
        if tool == "join_queue":
            payload["mode"] = "join_queue"
            view, _ = _book(tenant_id, payload, phone)
            return f"✅ You're in the queue. Your token is {view['tokens'][0]['number']}."
        if tool == "book_slot":
            payload.update(mode="slot", slot_id=args.get("slot_id"))
            view, _ = _book(tenant_id, payload, phone)
            tok = view["tokens"][0]
            return f"✅ Booked! Appointment {tok['number']}. Track code: {tok['short_code']}."
    except AppError as e:
        if e.code == "slot_full":
            return "Sorry — that slot was just taken. Reply 'menu' to see what's still available."
        if e.code == "no_session":
            return "Sorry, that doctor has no open session today. Reply 'menu' to try again."
        return "Sorry, I couldn't complete that. Reply 'menu' to start again."
    return "Reply 'menu' to begin."


def _summary(action: dict, ctx: dict) -> str:
    tool, args = action.get("tool"), action.get("args", {})
    if tool == "book_slot":
        s = next((x for x in ctx["slots"] if x["id"] == args.get("slot_id")), None)
        return f"Book {s['label']} with {s['doctor_name']}?" if s else "Confirm this booking?"
    if tool == "join_queue":
        d = next((x for x in ctx["doctors"] if x["id"] == args.get("doctor_id")), None)
        return f"Join today's walk-in queue with {d['name']}?" if d else "Join the queue?"
    return "Confirm?"


def _maybe_confirm(tenant_id: str, phone: str, action: dict, ctx: dict, profile_name: str | None) -> str:
    if action.get("tool") == "queue_status":
        return _execute(tenant_id, phone, action, profile_name)
    if _confirm_enabled(tenant_id):
        summary = _summary(action, ctx)
        _set_state(tenant_id, phone, {"kind": "confirm", "action": action}, summary)
        return f"{summary}\n\nReply YES to confirm or NO to cancel."
    _clear_state(tenant_id, phone)
    return _execute(tenant_id, phone, action, profile_name)


# ---- menu (rule-based) flow -----------------------------------------------------------------

def _num(t: str):
    t = t.strip()
    return int(t) if t.isdigit() else None


def _main_menu(ctx: dict) -> str:
    return (f"Hi! Welcome to {ctx['clinic_name']}. Reply with a number:\n"
            "1. Book an appointment slot\n2. Join today's queue\n3. Check queue status")


def _doctor_list(ctx: dict, intent: str) -> str:
    lines = "\n".join(f"{i+1}. {d['name']}" for i, d in enumerate(ctx["doctors"]))
    verb = "book with" if intent == "book" else "join the queue for"
    return f"Which doctor would you like to {verb}?\n{lines}"


def _slot_list(slots: list) -> str:
    lines = "\n".join(f"{i+1}. {s['label']}" for i, s in enumerate(slots))
    return f"Available times:\n{lines}\n\nReply with the number to book."


def _begin(tenant_id: str, phone: str, ctx: dict, intent: str, profile_name: str | None) -> str:
    docs = ctx["doctors"]
    if not docs:
        _clear_state(tenant_id, phone)
        return "Sorry, no doctors are available yet. Please try again later."
    if len(docs) == 1:
        return _after_doctor(tenant_id, phone, ctx, docs[0]["id"], intent, profile_name)
    _set_state(tenant_id, phone, {"kind": "menu", "step": "pick_doctor", "intent": intent,
                                  "doctors": docs})
    return _doctor_list(ctx, intent)


def _after_doctor(tenant_id: str, phone: str, ctx: dict, doctor_id: str, intent: str,
                  profile_name: str | None) -> str:
    if intent == "queue":
        return _maybe_confirm(tenant_id, phone, {"tool": "join_queue", "args": {"doctor_id": doctor_id}},
                              ctx, profile_name)
    slots = [s for s in ctx["slots"] if s["doctor_id"] == doctor_id]
    if not slots:
        _clear_state(tenant_id, phone)
        return "No open slots for that doctor right now. Reply 2 to join the queue instead."
    _set_state(tenant_id, phone, {"kind": "menu", "step": "pick_slot", "slots": slots})
    return _slot_list(slots)


def _menu_flow(tenant_id: str, phone: str, text: str, profile_name: str | None, state: dict | None) -> str:
    ctx = _context(tenant_id, phone, profile_name)
    t = text.strip().lower()
    step = (state or {}).get("step")
    if step is None or t in _RESET:
        _set_state(tenant_id, phone, {"kind": "menu", "step": "main"})
        return _main_menu(ctx)
    if step == "main":
        if t in {"1", "book"}:
            return _begin(tenant_id, phone, ctx, "book", profile_name)
        if t in {"2", "queue", "join"}:
            return _begin(tenant_id, phone, ctx, "queue", profile_name)
        if t in {"3", "status"}:
            _clear_state(tenant_id, phone)
            return _execute(tenant_id, phone, {"tool": "queue_status"}, profile_name)
        return "Please reply 1, 2, or 3.\n\n" + _main_menu(ctx)
    if step == "pick_doctor":
        docs = (state or {}).get("doctors", [])
        i = _num(t)
        if i is None or not (1 <= i <= len(docs)):
            return "Please reply with the doctor's number."
        return _after_doctor(tenant_id, phone, ctx, docs[i - 1]["id"], state.get("intent"), profile_name)
    if step == "pick_slot":
        slots = (state or {}).get("slots", [])
        i = _num(t)
        if i is None or not (1 <= i <= len(slots)):
            return "Please reply with the slot number from the list."
        s = slots[i - 1]
        return _maybe_confirm(tenant_id, phone,
                              {"tool": "book_slot", "args": {"doctor_id": s["doctor_id"], "slot_id": s["id"]}},
                              ctx, profile_name)
    _set_state(tenant_id, phone, {"kind": "menu", "step": "main"})
    return _main_menu(ctx)


# ---- AI flow --------------------------------------------------------------------------------

def _ai_flow(tenant_id: str, phone: str, text: str, profile_name: str | None) -> str:
    ctx = _context(tenant_id, phone, profile_name)
    decision = ai.infer(text, ctx)
    if decision.get("type") == "reply":
        return decision.get("text") or "Sorry, could you rephrase?"
    return _maybe_confirm(tenant_id, phone,
                          {"tool": decision.get("tool"), "args": decision.get("args", {})},
                          ctx, profile_name)


# ---- entry point ----------------------------------------------------------------------------

def handle_message(tenant_id: str, phone: str, text: str, profile_name: str | None = None) -> str:
    """Process one inbound message for a clinic and return the reply text."""
    t = (text or "").strip().lower()
    state = _get_state(tenant_id, phone)
    if state and state.get("kind") == "confirm":
        if t in _YES:
            reply = _execute(tenant_id, phone, state["action"], profile_name)
            _clear_state(tenant_id, phone)
            return reply
        if t in _NO:
            _clear_state(tenant_id, phone)
            return "No problem — cancelled. Reply 'menu' to start again."
        _clear_state(tenant_id, phone)         # not yes/no -> drop the pending, treat as new turn
        state = None
    if ai_enabled(tenant_id):
        return _ai_flow(tenant_id, phone, text, profile_name)
    return _menu_flow(tenant_id, phone, text, profile_name,
                      state if state and state.get("kind") == "menu" else None)
