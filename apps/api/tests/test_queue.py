"""Live queue management: a doctor works their own queue (call next -> serving -> done/no-show),
scoped to their own profile; admins can act on any doctor in the clinic."""
from __future__ import annotations

import datetime


def _staff_token(client, tenant_id, email="qadmin@x.test", role="clinic_admin"):
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, password_hash=hash_password("pw12345678"),
                     must_reset_password=False, status="active")
            db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role=role))
    return client.post("/auth/login", json={"email": email, "password": "pw12345678"}).json()["access_token"]


def _login_reset(client, ident, temp, new_pw, slug):
    r1 = client.post("/auth/login", json={"identifier": ident, "password": temp}).json()
    assert r1["must_reset_password"] is True
    client.post("/auth/change-password", headers={"Authorization": f"Bearer {r1['access_token']}"},
                json={"current_password": temp, "new_password": new_pw})
    tok = client.post("/auth/login", json={"identifier": ident, "password": new_pw}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}", "X-Clinic-Slug": slug}


def _book(client, slug, doctor_id, name, phone, slot_id=None):
    body = {"doctor_id": doctor_id, "patients": [{"name": name}], "contact_phone": phone, "consent": True}
    body.update({"mode": "slot", "slot_id": slot_id} if slot_id else {"mode": "join_queue"})
    return client.post("/bookings", headers={"X-Clinic-Slug": slug}, json=body)


def test_queue_lifecycle_and_doctor_self_scope(client, canary):
    slug = canary["slug"]
    admin = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}", "X-Clinic-Slug": slug}
    today = datetime.date.today().isoformat()
    # a doctor WITH a login + today's slots
    made = client.post("/slots/doctors", headers=admin,
                       json={"name": "Dr Queue", "email": "drqueue@x.test"}).json()
    doc_id, temp = made["id"], made["login"]["temp_password"]
    client.post("/slots/generate", headers=admin, json={
        "doctor_id": doc_id, "date": today, "start": "09:00", "end": "10:00",
        "slot_minutes": 30, "capacity": 1})
    slots = client.get(f"/clinics/{slug}/availability", params={"doctor": doc_id, "date": today}).json()["slots"]
    # one scheduled (slot) patient + one walk-in (queue) patient
    assert _book(client, slug, doc_id, "Slot Pat", "+919800009001", slots[0]["id"]).status_code == 201
    assert _book(client, slug, doc_id, "Walk Pat", "+919800009002").status_code == 201

    # the doctor signs in and sees ONLY their own queue, auto-scoped (no doctor param needed)
    dh = _login_reset(client, "drqueue@x.test", temp, "drqueuepw12", slug)
    q = client.get("/queue", headers=dh).json()
    assert q["doctor_id"] == doc_id
    assert q["counts"]["total"] == 2 and q["counts"]["waiting"] == 2 and q["serving"] is None

    # call next -> someone is being served, waiting drops by one
    called = client.post("/queue/call-next", headers=dh, json={}).json()
    assert called["called"]["state"] == "serving"
    q2 = client.get("/queue", headers=dh).json()
    assert q2["serving"] is not None and q2["counts"]["waiting"] == 1

    # mark the served patient done, then no-show the last waiting one
    assert client.post(f"/queue/{q2['serving']['id']}/state", headers=dh, json={"state": "done"}).status_code == 200
    last = client.get("/queue", headers=dh).json()["waiting"][0]
    assert client.post(f"/queue/{last['id']}/state", headers=dh, json={"state": "no_show"}).status_code == 200
    final = client.get("/queue", headers=dh).json()
    assert final["counts"]["waiting"] == 0 and final["counts"]["done"] == 2


def test_doctor_cannot_touch_another_doctors_queue(client, canary):
    slug = canary["slug"]
    admin = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}", "X-Clinic-Slug": slug}
    today = datetime.date.today().isoformat()
    # doctor A (with login) and doctor B (locum, no login) + a patient for B
    a = client.post("/slots/doctors", headers=admin, json={"name": "Dr A", "email": "dra.q@x.test"}).json()
    b_id = client.post("/slots/doctors", headers=admin, json={"name": "Dr B"}).json()["id"]
    client.post("/slots/generate", headers=admin, json={
        "doctor_id": b_id, "date": today, "start": "09:00", "end": "09:30", "slot_minutes": 30, "capacity": 1})
    bslots = client.get(f"/clinics/{slug}/availability", params={"doctor": b_id, "date": today}).json()["slots"]
    _book(client, slug, b_id, "B Patient", "+919800009010", bslots[0]["id"])

    dh = _login_reset(client, "dra.q@x.test", a["login"]["temp_password"], "draqpw12345", slug)
    # doctor A's queue is their own (empty) — B's patient is NOT visible, even if A asks for B
    qa = client.get("/queue", headers=dh, params={"doctor": b_id}).json()
    assert qa["doctor_id"] == a["id"] and qa["counts"]["total"] == 0
    # A cannot call-next for B either
    assert client.post("/queue/call-next", headers=dh, json={"doctor_id": b_id}).json()["called"] is None
