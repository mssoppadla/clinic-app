"""Notification dispatcher — the ONE place a clinic's WhatsApp notification for a lifecycle event
is produced: resolve the clinic's template + its ordered params from REAL booking data, send it
(best-effort, never raises into the caller), log it in `notifications`, meter it, and do it at most
once (idempotent via a unique dedupe_key — the claim that makes it safe across worker replicas).

Config-driven with safe defaults: a clinic that hasn't configured anything keeps its current
behaviour. Richer templates defined in the admin UI carry their own param_map, and the dispatcher
resolves the fuller set of variables automatically — no code change per template.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from ..core.db import system_session
from ..integrations import whatsapp
from ..models import (Booking, BookingPatient, ClinicMessageSetting, Doctor, MessageTemplate,
                      Notification, Slot, Tenant, Token, UsageEvent)

log = logging.getLogger("domain.notifications")

# Per-event defaults, used when the clinic has no clinic_message_settings row AND no catalog entry.
# param_map = ordered resolver keys for the template body {{1}}..{{n}}. Defaults are kept MINIMAL so
# they match the currently-approved templates (no regression). A richer template defined via the
# admin UI supplies its own param_map and the fuller set is resolved automatically.
DEFAULTS: dict[str, dict] = {
    "booking_confirmed":   {"template": "booking_confirmed",    "enabled": True,  "language": "en_US",
                            "param_map": ["token_number"]},
    "booking_cancelled":   {"template": "booking_cancelled",    "enabled": True,  "language": "en_US",
                            "param_map": ["patient_name"]},
    "booking_rescheduled": {"template": "booking_rescheduled",  "enabled": True,  "language": "en_US",
                            "param_map": ["patient_name", "appointment_datetime"]},
    "reminder":            {"template": "appointment_reminder", "enabled": True,  "language": "en_US",
                            "param_map": ["patient_name", "doctor_name", "appointment_datetime"]},
}

# All the human values a template can reference. A template's param_map picks + orders from these,
# so any template shape (token-only or a full "Hi <name>, Dr <doc> at <time>…") is supported.
RESOLVER_KEYS = ("patient_name", "doctor_name", "appointment_datetime", "token_number",
                 "clinic_name", "clinic_address", "clinic_phone")


def _fmt_dt(dt, tz_name: str) -> str:
    if not dt:
        return ""
    try:
        return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name)).strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return dt.strftime("%b %d, %Y %I:%M %p")


def _resolve_context(db, tenant, booking_id: str | None) -> dict:
    """Pull the real, human values a template can reference, from the booking's projections."""
    ctx = {k: "" for k in RESOLVER_KEYS}
    if tenant is not None:
        ctx["clinic_name"] = tenant.name or ""
        ctx["clinic_phone"] = tenant.contact_phone or ""
    if not booking_id:
        return ctx
    b = db.query(Booking).filter(Booking.id == booking_id).first()
    if b is None:
        return ctx
    bp = db.query(BookingPatient).filter(BookingPatient.booking_id == b.id).first()
    tok = db.query(Token).filter(Token.booking_patient_id == bp.id).first() if bp else None
    doc = db.query(Doctor).filter(Doctor.id == b.doctor_id).first()
    slot = db.query(Slot).filter(Slot.id == b.slot_id).first() if b.slot_id else None
    tzname = (tenant.timezone if tenant else None) or "Asia/Kolkata"
    ctx.update({
        "patient_name": (bp.name if bp else "") or "",
        "doctor_name": (doc.name if doc else "") or "",
        "token_number": (tok.number if tok else "") or "",
        "appointment_datetime": _fmt_dt(slot.start_ts if slot else None, tzname),
    })
    return ctx


def _template_for(db, tenant_id: str, event_type: str, setting) -> MessageTemplate | None:
    """The MessageTemplate to use: the clinic's explicitly chosen one, else the clinic's own
    approved catalog entry, else the platform's approved entry, else None (-> hardcoded DEFAULT)."""
    if setting is not None and setting.template_id:
        t = db.query(MessageTemplate).filter(MessageTemplate.id == setting.template_id).first()
        if t is not None:
            return t
    for scope in (f"clinic:{tenant_id}", "platform"):
        t = (db.query(MessageTemplate)
             .filter(MessageTemplate.scope == scope, MessageTemplate.event_type == event_type,
                     MessageTemplate.meta_status == "approved").first())
        if t is not None:
            return t
    return None


def notify(*, event_type: str, tenant_id: str, to_phone: str, booking_id: str | None = None,
           offset=None, extra: dict | None = None) -> dict:
    """Send the notification for one event. Returns {status: sent|failed|skipped, ...}. Never raises."""
    d = DEFAULTS.get(event_type, {"template": event_type, "enabled": True,
                                  "language": "en_US", "param_map": []})
    try:
        with system_session() as db:
            setting = (db.query(ClinicMessageSetting)
                       .filter(ClinicMessageSetting.tenant_id == tenant_id,
                               ClinicMessageSetting.event_type == event_type).first())
            if setting is not None and not setting.enabled:
                return {"status": "skipped", "reason": "disabled"}

            dedupe_key = f"{booking_id or to_phone}:{event_type}:{'' if offset is None else offset}"
            existing = db.query(Notification).filter(Notification.dedupe_key == dedupe_key).first()
            if existing is not None and existing.status in ("queued", "sent"):
                return {"status": "skipped", "reason": "duplicate", "id": existing.id}

            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            tmpl = _template_for(db, tenant_id, event_type, setting)
            template_name = tmpl.meta_name if tmpl else d["template"]
            language = ((setting.language if setting and setting.language else None)
                        or (tmpl.language if tmpl else None) or d["language"])
            param_map = (tmpl.param_map if tmpl and tmpl.param_map else d["param_map"]) or []

            ctx = _resolve_context(db, tenant, booking_id)
            if setting is not None and setting.variables:
                ctx.update(setting.variables)      # clinic static overrides (display_name/address/footer)
            if extra:
                ctx.update(extra)
            body_params = [str(ctx.get(k, "")) for k in param_map]

            # claim the dedupe_key first (queued), so a racing worker replica loses on the unique key
            note = Notification(
                tenant_id=tenant_id, booking_id=booking_id, event_type=event_type,
                template_id=(tmpl.id if tmpl else None), to_phone=to_phone, status="queued",
                dedupe_key=dedupe_key,
                params={"template": template_name, "language": language, "body": body_params})
            db.add(note)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return {"status": "skipped", "reason": "duplicate"}

            res = whatsapp().send_template(tenant_id=tenant_id, to_phone=to_phone,
                                           template=template_name, language=language,
                                           body_params=body_params)
            note.status = "sent" if res.get("ok") else "failed"
            note.wa_message_id = res.get("id")
            if not res.get("ok"):
                note.error = str(res.get("error", ""))[:500]
            note.sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.add(UsageEvent(tenant_id=tenant_id, provider="whatsapp", kind="template", units=1,
                              meta={"event_type": event_type, "template": template_name}))
            return {"status": note.status, "id": note.id, "template": template_name,
                    "language": language, "body_params": body_params}
    except Exception:
        log.exception("notify failed event=%s tenant=%s", event_type, tenant_id)
        return {"status": "failed", "reason": "exception"}
