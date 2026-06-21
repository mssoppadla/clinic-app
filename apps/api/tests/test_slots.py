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
    # staff pages are clinic-scoped: the slug identifies which hospital's slots
    h = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}",
         "X-Clinic-Slug": canary["slug"]}
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


def test_staff_add_doctor_shows_in_picker_and_takes_slots(client, canary):
    """A doctor added on the slots page appears in the picker right away and can take slots —
    including ad-hoc/visiting doctors that have no login of their own."""
    h = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}",
         "X-Clinic-Slug": canary["slug"]}
    add = client.post("/slots/doctors", headers=h, json={"name": "Dr Locum", "specialty": "GP"})
    assert add.status_code == 201, add.text
    doc_id = add.json()["id"]
    picker = client.get("/slots/doctors", headers=h).json()["doctors"]
    assert any(d["id"] == doc_id for d in picker)            # visible immediately
    date = (datetime.date.today() + datetime.timedelta(days=4)).isoformat()
    gen = client.post("/slots/generate", headers=h, json={
        "doctor_id": doc_id, "date": date, "start": "09:00", "end": "10:00",
        "slot_minutes": 30, "capacity": 1})
    assert gen.status_code == 201 and gen.json()["created"] == 2


def test_doctor_login_is_same_as_clinical_profile(client, canary):
    """A doctor created WITH login credentials becomes a single record: the login they sign in
    with maps to the very same clinical doctor profile that holds their slots."""
    admin = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}",
             "X-Clinic-Slug": canary["slug"]}
    r = client.post("/slots/doctors", headers=admin, json={
        "name": "Dr Asha", "specialty": "Pediatrics",
        "email": "asha@clinic.test", "phone": "+919800009001"})
    assert r.status_code == 201, r.text
    doc_id, temp = r.json()["id"], r.json()["login"]["temp_password"]
    assert r.json()["has_login"] is True

    # that login signs in, resets, and sees THEIR OWN profile as their self-service doctor
    first = client.post("/auth/login", json={"identifier": "asha@clinic.test", "password": temp}).json()
    assert first["must_reset_password"] is True
    client.post("/auth/change-password", headers={"Authorization": f"Bearer {first['access_token']}"},
                json={"current_password": temp, "new_password": "ashapw12345"})
    tok = client.post("/auth/login", json={"identifier": "asha@clinic.test", "password": "ashapw12345"}).json()["access_token"]
    dh = {"Authorization": f"Bearer {tok}", "X-Clinic-Slug": canary["slug"]}
    me = client.get("/slots/doctors", headers=dh).json()["me"]
    assert me["can_manage_all"] is False and me["doctor_id"] == doc_id   # login == clinical profile

    # the doctor manages their own slots; a locum (no login) added by admin stays admin-managed
    locum = client.post("/slots/doctors", headers=admin, json={"name": "Visiting Dr"}).json()
    assert locum["has_login"] is False and locum["login"] is None
    date = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    own = client.post("/slots/generate", headers=dh, json={
        "doctor_id": doc_id, "date": date, "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
    assert own.status_code == 201
    other = client.post("/slots/generate", headers=dh, json={
        "doctor_id": locum["id"], "date": date, "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
    assert other.status_code == 403   # a doctor can't manage another doctor's schedule


def test_doctor_leave_closes_open_slots_only(client, canary):
    """Leave closes a doctor's OPEN slots for a day (removing them from availability) but leaves
    already-booked slots intact."""
    admin = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}",
             "X-Clinic-Slug": canary["slug"]}
    doc_id = client.post("/slots/doctors", headers=admin, json={"name": "Dr Leave"}).json()["id"]
    date = (datetime.date.today() + datetime.timedelta(days=6)).isoformat()
    client.post("/slots/generate", headers=admin, json={
        "doctor_id": doc_id, "date": date, "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
    slots = client.get(f"/clinics/{canary['slug']}/availability",
                       params={"doctor": doc_id, "date": date}).json()["slots"]
    assert len(slots) == 2
    # book one, then mark leave: the booked one survives, the open one is closed
    client.post("/bookings", headers={"X-Clinic-Slug": canary["slug"]}, json={
        "doctor_id": doc_id, "mode": "slot", "slot_id": slots[0]["id"],
        "patients": [{"name": "Booked"}], "contact_phone": "+919800009100", "consent": True})
    lv = client.post("/slots/leave", headers=admin, json={"doctor_id": doc_id, "date": date})
    assert lv.status_code == 200 and lv.json()["closed"] == 1 and lv.json()["still_booked"] == 1
    after = client.get(f"/clinics/{canary['slug']}/availability",
                       params={"doctor": doc_id, "date": date}).json()["slots"]
    assert after == []   # nothing left bookable that day


def test_doctor_and_slots_setup_before_go_live_completes_checklist(client, superadmin_headers):
    """Backend data can be set up BEFORE go-live: a platform admin adds a doctor + slots to a
    still-pending clinic, and the go-live readiness checklist ('a doctor with slots') flips to met."""
    slug = client.post("/onboarding/clinic",
                       json={"name": "Pre Live Clinic", "contact_email": "pre@live.test"}).json()["slug"]
    # pending clinic shows the checklist item as NOT done
    pend = client.get("/onboarding/pending", headers=superadmin_headers).json()["pending"]
    me = next(c for c in pend if c["slug"] == slug)
    assert me["readiness"]["mandatory_met"] is False

    h = {**superadmin_headers, "X-Clinic-Slug": slug}     # superadmin may act on any clinic
    doc_id = client.post("/slots/doctors", headers=h, json={"name": "Dr Early"}).json()["id"]
    date = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    assert client.post("/slots/generate", headers=h, json={
        "doctor_id": doc_id, "date": date, "start": "09:00", "end": "10:00",
        "slot_minutes": 30, "capacity": 1}).status_code == 201

    # now the mandatory checklist is satisfied — ready to approve go-live
    pend2 = client.get("/onboarding/pending", headers=superadmin_headers).json()["pending"]
    me2 = next(c for c in pend2 if c["slug"] == slug)
    assert me2["readiness"]["mandatory_met"] is True


def test_onboarding_doctor_creates_real_timed_slots(client, superadmin_headers):
    """A doctor added via the (self-serve) onboarding flow gets REAL timed slots that then show on
    the Slots management page — one consistent 'slot' everywhere, no queue-session/timed mismatch."""
    slug = client.post("/onboarding/clinic",
                       json={"name": "Onboard Slots Clinic", "contact_email": "os@t.com"}).json()["slug"]
    r = client.post(f"/onboarding/clinic/{slug}/doctor",
                    json={"name": "Dr Onboard", "session_label": "Morning"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slots_created"] == 12          # 09:00–12:00 at 15-min slots
    doc_id = body["doctor_id"]
    # those exact slots are visible on the Slots management page (superadmin acting on the clinic)
    h = {**superadmin_headers, "X-Clinic-Slug": slug}
    today = datetime.date.today().isoformat()
    listed = client.get("/slots", params={"doctor": doc_id, "date": today}, headers=h).json()["slots"]
    assert len(listed) == 12


def test_dev_otp_code_used_only_in_stub(client, canary, monkeypatch):
    """A configured dev OTP code lets testers walk the WhatsApp-OTP flow with a fixed code — but
    only in stub mode (never when WhatsApp is live)."""
    import app.api.auth as auth
    from app.core.config import Settings

    def fake_settings():
        s = Settings(); object.__setattr__(s, "dev_otp_code", "424242"); return s
    monkeypatch.setattr(auth, "get_settings", fake_settings)

    h = {"X-Clinic-Slug": canary["slug"]}
    assert client.post("/auth/otp/request", headers=h, json={"phone": "+919800007777"}).status_code == 200
    # the fixed dev code verifies (stub mode); a different code does not
    assert client.post("/auth/otp/verify", headers=h,
                       json={"phone": "+919800007777", "otp": "111111"}).status_code == 400
    ok = client.post("/auth/otp/verify", headers=h, json={"phone": "+919800007777", "otp": "424242"})
    assert ok.status_code == 200 and ok.json()["scope"] == "patient.self"


def _reset_login(client, ident, temp, new_pw):
    first = client.post("/auth/login", json={"identifier": ident, "password": temp}).json()
    client.post("/auth/change-password", headers={"Authorization": f"Bearer {first['access_token']}"},
                json={"current_password": temp, "new_password": new_pw})
    return client.post("/auth/login", json={"identifier": ident, "password": new_pw}).json()["access_token"]


def test_link_login_to_existing_doctor_preserves_profile(client, canary):
    """Backward-compat: a doctor login created the OLD way (no linked profile) can be attached to
    an existing Doctor profile, so that login self-services that profile (and its slots/bookings)."""
    admin = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'])}",
             "X-Clinic-Slug": canary["slug"]}
    # an existing doctor profile with slots, and a separately-created doctor login (unlinked)
    doc_id = client.post("/slots/doctors", headers=admin, json={"name": "Legacy Doc"}).json()["id"]
    cu = client.post("/users", headers=admin,
                     json={"email": "legacy@doc.test", "role": "doctor", "tenant_id": canary["tenant_id"]}).json()
    dtok = _reset_login(client, "legacy@doc.test", cu["temp_password"], "legacypw123")
    dh = {"Authorization": f"Bearer {dtok}", "X-Clinic-Slug": canary["slug"]}
    # before linking: the login has no profile -> queue is refused (the bug we guard against)
    assert client.get("/slots/doctors", headers=dh).json()["me"]["doctor_id"] is None
    assert client.get("/queue", headers=dh).status_code == 403
    # admin links the existing login to the existing profile (reuses the account, no new password)
    r = client.post(f"/slots/doctors/{doc_id}/link-login", headers=admin, json={"email": "legacy@doc.test"})
    assert r.status_code == 200 and r.json()["has_login"] is True and r.json()["new_login"] is None
    # now that login self-services that very profile, and the queue loads
    assert client.get("/slots/doctors", headers=dh).json()["me"]["doctor_id"] == doc_id
    assert client.get("/queue", headers=dh).status_code == 200


def test_join_queue_still_works(client, canary):
    r = client.post("/bookings", headers={"X-Clinic-Slug": canary["slug"]}, json={
        "doctor_id": canary["doctor_id"], "mode": "join_queue",
        "patients": [{"name": "Q"}], "contact_phone": "+919800002099", "consent": True})
    assert r.status_code == 201 and r.json()["tokens"][0]["number"].startswith("A-")
