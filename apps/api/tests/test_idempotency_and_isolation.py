"""Idempotency (no duplicate booking) + tenant isolation (app-layer guard)."""
from __future__ import annotations

from app.core.db import TenantScope, session_scope
from app.models import Booking, QueueEntry, Tenant


def test_idempotent_replay_returns_same_no_duplicate(client, canary):
    payload = {
        "doctor_id": canary["doctor_id"],
        "patients": [{"name": "Idem Test"}],
        "contact_phone": "+919811111111",
        "consent": True,
    }
    h = {"X-Clinic-Slug": canary["slug"], "Idempotency-Key": "fixed-key-123"}
    r1 = client.post("/bookings", json=payload, headers=h)
    r2 = client.post("/bookings", json=payload, headers=h)
    assert r1.status_code == 201
    assert r2.status_code == 200  # replayed, not created
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["tokens"][0]["number"] == r2.json()["tokens"][0]["number"]


def test_tenant_scope_blocks_cross_tenant_reads(client, canary):
    # create a second tenant and confirm its scope cannot see the canary's bookings
    with session_scope() as db:
        other = db.query(Tenant).filter(Tenant.slug == "other-clinic").first()
        if other is None:
            other = Tenant(slug="other-clinic", name="Other", status="active", languages=["en"])
            db.add(other)
            db.flush()
        other_id = other.id

    # canary has bookings from earlier tests; other tenant must see none of them
    with session_scope() as db:
        canary_scope = TenantScope(db, canary["tenant_id"])
        other_scope = TenantScope(db, other_id)
        canary_bookings = list(canary_scope.query(Booking))
        other_bookings = list(other_scope.query(Booking))
        assert len(canary_bookings) >= 1
        assert all(b.tenant_id == canary["tenant_id"] for b in canary_bookings)
        assert other_bookings == []


def test_cross_tenant_write_blocked(client, canary):
    with session_scope() as db:
        scope = TenantScope(db, canary["tenant_id"])
        bad = QueueEntry(tenant_id="some-other-tenant", session_id="x",
                         booking_patient_id="y", position=1)
        try:
            scope.add(bad)
            assert False, "expected cross-tenant write to be blocked"
        except PermissionError:
            pass
