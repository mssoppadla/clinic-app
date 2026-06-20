"""Patient OTP login (2d), post-go-live onboarding lock-down, and cancel/reschedule + refund."""
from __future__ import annotations

import datetime


def _staff_token(client, tenant_id, email, role="clinic_admin"):
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


# ---- Feature 1: patient WhatsApp-OTP login -------------------------------------------------

def test_patient_otp_login(client, canary):
    from app.integrations.whatsapp import SENT_STUB
    h = {"X-Clinic-Slug": canary["slug"]}
    SENT_STUB.clear()
    assert client.post("/auth/otp/request", headers=h, json={"phone": "+919811111111"}).status_code == 200
    assert SENT_STUB, "patient OTP should be sent via WhatsApp stub"
    code = SENT_STUB[-1]["params"]["code"]
    assert client.post("/auth/otp/verify", headers=h,
                       json={"phone": "+919811111111", "otp": "000000"}).status_code == 400
    v = client.post("/auth/otp/verify", headers=h, json={"phone": "+919811111111", "otp": code})
    assert v.status_code == 200 and v.json()["scope"] == "patient.self" and v.json()["access_token"]


# ---- Feature 2: post-go-live onboarding lock-down ------------------------------------------

def test_onboarding_mutations_open_pending_locked_when_live(client, superadmin_headers):
    reg = client.post("/onboarding/clinic", json={"name": "Lockdown Clinic",
                                                  "contact_email": "owner@lock.com"}).json()
    slug, tid = reg["slug"], None
    # pending -> add doctor by slug works (self-serve)
    assert client.post(f"/onboarding/clinic/{slug}/doctor", json={"name": "Dr A"}).status_code == 201
    assert client.post("/onboarding/appearance", json={"slug": slug, "branding": {"color": "#111111"}}).status_code == 200

    # approve -> live (and creates a clinic_admin)
    appr = client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug}).json()
    # now mutations require auth
    assert client.post(f"/onboarding/clinic/{slug}/doctor", json={"name": "Dr B"}).status_code == 401
    assert client.post("/onboarding/appearance",
                       json={"slug": slug, "branding": {"color": "#222222"}}).status_code == 401

    # the clinic's own admin can; a different clinic's admin cannot
    from app.core.db import system_session
    from app.models import Tenant
    with system_session() as db:
        tid = db.query(Tenant).filter(Tenant.slug == slug).first().id
    own = {"Authorization": f"Bearer {_staff_token(client, tid, 'ownadmin@lock.com')}"}
    other = {"Authorization": f"Bearer {_staff_token(client, 'other-tid', 'otheradmin@lock.com')}"}
    assert client.post(f"/onboarding/clinic/{slug}/doctor", headers=own, json={"name": "Dr C"}).status_code == 201
    assert client.post(f"/onboarding/clinic/{slug}/doctor", headers=other, json={"name": "Dr D"}).status_code == 403
    # superadmin can too
    assert client.post(f"/onboarding/clinic/{slug}/doctor", headers=superadmin_headers, json={"name": "Dr E"}).status_code == 201


# ---- Clinic-scoped staff authz: a clinic's staff can't touch another clinic's slots ---------

def test_slots_are_clinic_scoped(client, canary, superadmin_headers):
    # second clinic, made live
    reg = client.post("/onboarding/clinic", json={"name": "Other Hosp", "contact_email": "o@h.com"}).json()
    other_slug = reg["slug"]
    client.post("/onboarding/override", headers=superadmin_headers, json={"slug": other_slug})
    # a clinic_admin of the CANARY clinic
    tok = _staff_token(client, canary["tenant_id"], "scoped@x.com")
    auth = f"Bearer {tok}"
    # using their own clinic's slug -> allowed
    ok = client.get("/slots/doctors", headers={"Authorization": auth, "X-Clinic-Slug": canary["slug"]})
    assert ok.status_code == 200
    # pointing at ANOTHER clinic's slug -> 403 (even though they hold a staff role)
    no = client.get("/slots/doctors", headers={"Authorization": auth, "X-Clinic-Slug": other_slug})
    assert no.status_code == 403
    # superadmin may act on any clinic
    sa = client.get("/slots/doctors", headers={**superadmin_headers, "X-Clinic-Slug": other_slug})
    assert sa.status_code == 200


def test_provider_config_is_per_clinic(client, canary, superadmin_headers):
    reg = client.post("/onboarding/clinic", json={"name": "Hosp B", "contact_email": "b@h.com"}).json()
    b_slug = reg["slug"]
    client.post("/onboarding/override", headers=superadmin_headers, json={"slug": b_slug})
    # set WhatsApp live for clinic B only
    client.put("/admin/integrations/whatsapp", headers={**superadmin_headers, "X-Clinic-Slug": b_slug},
               json={"mode": "live", "token": "B-secret", "phone_number_id": "999"})
    b = client.get("/admin/integrations/status", headers={**superadmin_headers, "X-Clinic-Slug": b_slug}).json()
    canary_st = client.get("/admin/integrations/status",
                           headers={**superadmin_headers, "X-Clinic-Slug": canary["slug"]}).json()
    assert b["whatsapp"]["mode"] == "live" and b["whatsapp"]["config"]["phone_number_id"] == "999"
    assert canary_st["whatsapp"]["mode"] == "stub"   # clinic A unaffected -> per-clinic isolation


# ---- Feature 3: cancel / reschedule + refund hook ------------------------------------------

def _setup_slots(client, canary, n_days_ahead=1):
    h = {"Authorization": f"Bearer {_staff_token(client, canary['tenant_id'], 'slotadmin@x.com')}",
         "X-Clinic-Slug": canary["slug"]}
    date = (datetime.date.today() + datetime.timedelta(days=n_days_ahead)).isoformat()
    client.post("/slots/generate", headers=h, json={
        "doctor_id": canary["doctor_id"], "date": date,
        "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
    slots = client.get(f"/clinics/{canary['slug']}/availability",
                       params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]
    return h, date, slots


def _book(client, canary, slot_id, name="Pat"):
    return client.post("/bookings", headers={"X-Clinic-Slug": canary["slug"]}, json={
        "doctor_id": canary["doctor_id"], "mode": "slot", "slot_id": slot_id,
        "patients": [{"name": name}], "contact_phone": "+919822222222", "consent": True}).json()


def test_cancel_frees_slot(client, canary):
    h, date, slots = _setup_slots(client, canary, 2)
    b = _book(client, canary, slots[0]["id"])
    # slot now taken
    avail = client.get(f"/clinics/{canary['slug']}/availability",
                       params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]
    assert all(s["id"] != slots[0]["id"] for s in avail)
    # staff cancels -> freed, refund hook present (unpaid -> 0)
    r = client.post(f"/bookings/{b['id']}/cancel", headers=h, json={"reason": "patient request"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["refund_minor"] == 0
    avail2 = client.get(f"/clinics/{canary['slug']}/availability",
                        params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]
    assert any(s["id"] == slots[0]["id"] for s in avail2)


def test_reschedule_moves_capacity(client, canary):
    h, date, slots = _setup_slots(client, canary, 3)
    b = _book(client, canary, slots[0]["id"])
    r = client.post(f"/bookings/{b['id']}/reschedule", headers=h, json={"new_slot_id": slots[1]["id"]})
    assert r.status_code == 200
    avail = {s["id"] for s in client.get(f"/clinics/{canary['slug']}/availability",
             params={"doctor": canary["doctor_id"], "date": date}).json()["slots"]}
    assert slots[0]["id"] in avail        # old slot freed
    assert slots[1]["id"] not in avail    # new slot now taken
