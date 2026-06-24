"""Routing for Tovaitech's SHARED WhatsApp number — one number serving many clinics.

A clinic with no number of its own advertises a deep link whose first message carries its slug
('...book at <slug>'). On inbound to the shared number (no clinic owns that phone_number_id), we
resolve the clinic from that code and remember the binding, so the patient's follow-up messages
('yes', '2') route to the same clinic. Clinics with their own number route by phone_number_id and
never reach here.
"""
from __future__ import annotations

import datetime
import re

from ..core import integration_config as cfg
from ..core.db import system_session
from ..models import Tenant, WhatsAppBinding

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _bind(db, phone: str, tenant_id: str) -> None:
    row = db.query(WhatsAppBinding).filter(WhatsAppBinding.phone == phone).first()
    if row is None:
        db.add(WhatsAppBinding(phone=phone, tenant_id=tenant_id, updated_at=_now()))
    else:
        row.tenant_id, row.updated_at = tenant_id, _now()


def _clinic_from_code(db, text: str) -> str | None:
    tokens = set(_SLUG_RE.findall((text or "").lower()))
    if not tokens:
        return None
    rows = (db.query(Tenant)
            .filter(Tenant.slug.in_(tokens), Tenant.go_live.is_(True),
                    Tenant.is_synthetic.is_(False)).all())
    return rows[0].id if rows else None


def resolve_shared_clinic(phone: str, text: str) -> str | None:
    """Resolve which clinic an inbound to the shared number is for. An explicit clinic code in the
    message wins (lets a patient switch clinics); otherwise fall back to the remembered binding."""
    with system_session() as db:
        tid = _clinic_from_code(db, text)
        if tid:
            _bind(db, phone, tid)
            return tid
        b = db.query(WhatsAppBinding).filter(WhatsAppBinding.phone == phone).first()
        return b.tenant_id if b else None


def shared_number_digits() -> str:
    """Tovaitech's shared number (digits only) for wa.me links — from the platform account."""
    return re.sub(r"\D", "", cfg.get_effective("whatsapp").get("display_number") or "")


def deep_link(slug: str) -> str | None:
    """The wa.me deep link a shared-number clinic puts on its booking page / QR poster."""
    num = shared_number_digits()
    return f"https://wa.me/{num}?text=Book%20at%20{slug}" if num else None
