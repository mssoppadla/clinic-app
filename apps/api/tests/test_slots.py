"""Phase 1: staff generate slots -> patient sees availability -> books a slot -> slot fills.
Queue mode still works alongside."""
from __future__ import annotations

import datetime


def _staff_token(client, tenant_id, role="clinic_admin"):
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    email = f"{role}@slots.test"
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, password_hash=hash_password("pw12345678"),
                     must_reset_password=False, status="active")
            db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role=role))
    return client.post("/auth/login", json={"email": email, "password": "pw12345678"}).json()["access_token"]


def test_generate_availability_book_and_fill(client, canary):
    h = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}"}
    date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    g = client.post("/slots/generate", headers=h, json={
        "doctor_id": canary["doctor_id"], "date": date,
        "start": "10:00", "end": "11:00", "slot_minutes": 15, "capacity": 1})
    assert g.status_code == 201, g.text
    assert g.json()["created"] == 4          # 10:00, 10:15, 10:30, 10:45

    # re-generate is idempotent (skips existing)
    assert client.post("/slots/generate", headers=h, json={
        "doctor_id": canary["doctor_id"], "date": date,
        "start": "10:00", "end": "11:00", "slot_minutes": 15, "capacity": 1}).json()["skipped"] == 4

    slug = canary["slug"]
    days = client.get(f"/clinics/{slug}/availability/days", params={"doctor": canary["doctor_id"]}).json()["days"]
    assert any(d["date"] == date and d["available"] == 4 for d in days)

    slots = client.get(f"/clinics/{slug}/availability",
                       params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]
    assert len(slots) == 4 and slots[0]["label"]
    slot_id = slots[0]["id"]

    book = client.post("/bookings", headers={"X-Clinic-Slug": slug}, json={
        "doctor_id": canary["doctor_id"], "mode": "slot", "slot_id": slot_id,
        "patients": [{"name": "Ravi"}], "contact_phone": "+919800002000", "consent": True})
    assert book.status_code == 201, book.text
    assert book.json()["tokens"][0]["number"]            # a time label like "10:00 AM"

    # capacity 1 -> a second booking on the same slot is rejected (no oversell)
    full = client.post("/bookings", headers={"X-Clinic-Slug": slug}, json={
        "doctor_id": canary["doctor_id"], "mode": "slot", "slot_id": slot_id,
        "patients": [{"name": "Asha"}], "contact_phone": "+919800002001", "consent": True})
    assert full.status_code == 409 and full.json()["error"]["code"] == "slot_full"

    # the filled slot drops out of availability
    after = client.get(f"/clinics/{slug}/availability",
                       params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]
    assert all(s["id"] != slot_id for s in after) and len(after) == 3


def test_join_queue_still_works(client, canary):
    r = client.post("/bookings", headers={"X-Clinic-Slug": canary["slug"]}, json={
        "doctor_id": canary["doctor_id"], "mode": "join_queue",
        "patients": [{"name": "Q"}], "contact_phone": "+919800002099", "consent": True})
    assert r.status_code == 201 and r.json()["tokens"][0]["number"].startswith("A-")
