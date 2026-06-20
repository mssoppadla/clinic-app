"""End-to-end, STATEFUL two-hospital journey — one continuous story, not independent fixtures.

The same two clinics (A, B) are carried through every stage, so any cross-tenant leak surfaces:

  register (self-serve)  ->  platform admin activates (go-live + auto clinic_admin)
  -> clinic_admin first login (forced reset)
  -> clinic_admin adds a doctor (clinical) + staff logins (doctor user, front desk)
  -> doctor user adds their slots
  -> patient books a slot, hits capacity, joins the live queue
  -> patient WhatsApp-OTP login

At each stage we assert clinic A and clinic B stay isolated (A's staff can't touch B, a patient
on B never sees A's slots, an OTP for A can't be used on B). Methods run in definition order and
share module state via S, so a failure pinpoints the exact stage that broke.
"""
from __future__ import annotations

import datetime

# shared journey state, keyed by hospital label -> dict of slug/tokens/ids
S: dict = {}

HOSPITALS = [
    {"key": "A", "name": "Apollo Journey Clinic", "email": "admina@apollo.test", "phone": "+919800000100"},
    {"key": "B", "name": "Bethel Journey Clinic", "email": "adminb@bethel.test", "phone": "+919800000200"},
]


def _h(token: str, slug: str | None = None) -> dict:
    out = {"Authorization": f"Bearer {token}"}
    if slug:
        out["X-Clinic-Slug"] = slug
    return out


def _login_and_reset(client, ident: str, temp: str, new_pw: str) -> str:
    """An admin-created account's first run: forced reset -> usable token."""
    r1 = client.post("/auth/login", json={"identifier": ident, "password": temp}).json()
    assert r1.get("must_reset_password") is True, f"{ident} should be forced to reset"
    cp = client.post("/auth/change-password", headers=_h(r1["access_token"]),
                     json={"current_password": temp, "new_password": new_pw})
    assert cp.status_code == 200, cp.text
    r2 = client.post("/auth/login", json={"identifier": ident, "password": new_pw}).json()
    assert r2.get("must_reset_password") is False
    return r2["access_token"]


class TestTwoHospitalJourney:
    # -- Stage 1: self-serve registration -------------------------------------------------
    def test_01_register_two_hospitals_pending(self, client):
        for hsp in HOSPITALS:
            r = client.post("/onboarding/clinic", json={
                "name": hsp["name"], "contact_email": hsp["email"], "contact_phone": hsp["phone"]})
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["go_live"] is False                      # not live until approved
            S[hsp["key"]] = {"slug": body["slug"], "email": hsp["email"]}
        assert S["A"]["slug"] != S["B"]["slug"]
        # a not-live clinic rejects patient booking
        blocked = client.post("/bookings", headers={"X-Clinic-Slug": S["A"]["slug"]}, json={
            "doctor_id": "x", "mode": "join_queue",
            "patients": [{"name": "Too Early"}], "contact_phone": "+910000000000", "consent": True})
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "clinic_not_live"

    # -- Stage 2: platform admin activates each clinic ------------------------------------
    def test_02_platform_admin_activates_and_autocreates_admin(self, client, superadmin_headers):
        for hsp in HOSPITALS:
            slug = S[hsp["key"]]["slug"]
            r = client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["go_live"] is True
            assert body["clinic_admin"]["email"] == hsp["email"]
            S[hsp["key"]]["admin_temp"] = body["clinic_admin"]["temp_password"]
        # now live -> onboarding status reflects active + go_live (was pending before)
        st = client.get("/onboarding/status", params={"slug": S["A"]["slug"]}).json()
        assert st["status"] == "active" and st["go_live"] is True

    # -- Stage 3: clinic_admin first login + forced reset, with isolation ------------------
    def test_03_clinic_admins_first_login_and_isolation(self, client):
        for hsp in HOSPITALS:
            st = S[hsp["key"]]
            st["admin_token"] = _login_and_reset(client, st["email"], st["admin_temp"],
                                                 f"AdminPw-{hsp['key']}-99")
            # resolve this clinic (also captures tenant_id for later isolation asserts)
            me = client.get("/users/clinic", headers=_h(st["admin_token"], st["slug"]))
            assert me.status_code == 200, me.text
            st["tenant_id"] = me.json()["tenant_id"]
        # A's admin must NOT be able to act on B's clinic
        cross = client.get("/users/clinic", headers=_h(S["A"]["admin_token"], S["B"]["slug"]))
        assert cross.status_code == 403, "clinic A admin reached clinic B"

    # -- Stage 4: clinic_admin adds a (clinical) doctor on the slots page + staff logins ----
    def test_04_clinic_admin_adds_doctor_and_staff(self, client):
        for hsp in HOSPITALS:
            st = S[hsp["key"]]
            ah = _h(st["admin_token"], st["slug"])
            # The doctor's LOGIN and clinical profile are one record: create the doctor WITH login
            # credentials on the slots page. It appears in the picker and yields a doctor login.
            email = f"dr.{hsp['key'].lower()}@journey.test"
            doc = client.post("/slots/doctors", headers=ah, json={
                "name": f"Dr {hsp['key']}", "specialty": "General",
                "email": email, "phone": "+919800000300"})
            assert doc.status_code == 201, doc.text
            st["doctor_id"] = doc.json()["id"]
            assert doc.json()["has_login"] is True and doc.json()["login"]["temp_password"]
            st["doctor_user"] = {"email": email, "temp": doc.json()["login"]["temp_password"]}
            picker = client.get("/slots/doctors", headers=ah).json()["doctors"]
            assert any(d["id"] == st["doctor_id"] and d["has_login"] for d in picker)
            # a separate (non-clinical) front-desk login
            fd = client.post("/users", headers=ah,
                             json={"username": f"front{hsp['key'].lower()}", "role": "front_desk"})
            assert fd.status_code == 201, fd.text
        # isolation: A's admin can't add a doctor to B, nor create a user under B's tenant_id
        x1 = client.post("/slots/doctors", headers=_h(S["A"]["admin_token"], S["B"]["slug"]),
                         json={"name": "Intruder", "specialty": "X"})
        assert x1.status_code == 403, "A's admin added a doctor to B"
        x2 = client.post("/users", headers=_h(S["A"]["admin_token"], S["A"]["slug"]),
                         json={"email": "intruder@b.test", "role": "front_desk", "tenant_id": S["B"]["tenant_id"]})
        assert x2.status_code == 403, "A's admin created a user inside B"

    # -- Stage 5: the doctor logs into THEIR OWN profile and self-manages their schedule ----
    def test_05_doctor_self_manages_schedule(self, client):
        date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        S["_slot_date"] = date
        for hsp in HOSPITALS:
            st = S[hsp["key"]]
            # the doctor login created in stage 4 IS the clinical profile
            st["doctor_token"] = _login_and_reset(client, st["doctor_user"]["email"],
                                                  st["doctor_user"]["temp"], f"DocPw-{hsp['key']}-99")
            ctx = client.get("/slots/doctors", headers=_h(st["doctor_token"], st["slug"])).json()["me"]
            assert ctx["can_manage_all"] is False and ctx["doctor_id"] == st["doctor_id"]
            gen = client.post("/slots/generate", headers=_h(st["doctor_token"], st["slug"]), json={
                "doctor_id": st["doctor_id"], "date": date,
                "start": "09:00", "end": "11:00", "slot_minutes": 30, "capacity": 1})
            assert gen.status_code == 201 and gen.json()["created"] == 4
        # isolation: A's doctor can't generate against B's clinic, nor against another doctor_id
        wrong_clinic = client.post("/slots/generate", headers=_h(S["A"]["doctor_token"], S["B"]["slug"]),
                                   json={"doctor_id": S["A"]["doctor_id"], "date": date,
                                         "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
        assert wrong_clinic.status_code == 403, "A's doctor generated slots in B"
        # a locum doctor (no login) added by A's admin — A's doctor must not manage them
        locum = client.post("/slots/doctors", headers=_h(S["A"]["admin_token"], S["A"]["slug"]),
                            json={"name": "Locum A"}).json()["id"]
        not_mine = client.post("/slots/generate", headers=_h(S["A"]["doctor_token"], S["A"]["slug"]),
                               json={"doctor_id": locum, "date": date,
                                     "start": "09:00", "end": "10:00", "slot_minutes": 30, "capacity": 1})
        assert not_mine.status_code == 403, "A's doctor managed another doctor's schedule"

    # -- Stage 6: patient books a slot, hits capacity, joins the live queue ----------------
    def test_06_patient_books_slot_and_joins_queue(self, client):
        date = S["_slot_date"]
        for hsp in HOSPITALS:
            st = S[hsp["key"]]
            days = client.get(f"/clinics/{st['slug']}/availability/days",
                              params={"doctor": st["doctor_id"]}).json()["days"]
            assert any(d["date"] == date and d["available"] == 4 for d in days)
            slots = client.get(f"/clinics/{st['slug']}/availability",
                               params={"doctor": st["doctor_id"], "date": date}).json()["slots"]
            assert len(slots) == 4
            st["slot_id"] = slots[0]["id"]
            book = client.post("/bookings", headers={"X-Clinic-Slug": st["slug"]}, json={
                "doctor_id": st["doctor_id"], "mode": "slot", "slot_id": st["slot_id"],
                "patients": [{"name": f"Patient {hsp['key']}"}], "contact_phone": "+919811100000", "consent": True})
            assert book.status_code == 201, book.text
            # capacity 1 -> a second booking on the same slot is refused
            full = client.post("/bookings", headers={"X-Clinic-Slug": st["slug"]}, json={
                "doctor_id": st["doctor_id"], "mode": "slot", "slot_id": st["slot_id"],
                "patients": [{"name": "Overflow"}], "contact_phone": "+919811100001", "consent": True})
            assert full.status_code == 409 and full.json()["error"]["code"] == "slot_full"
            # join the live queue
            q = client.post("/bookings", headers={"X-Clinic-Slug": st["slug"]}, json={
                "doctor_id": st["doctor_id"], "mode": "join_queue",
                "patients": [{"name": f"Walkin {hsp['key']}"}], "contact_phone": "+919811100002", "consent": True})
            assert q.status_code == 201 and q.json()["tokens"][0]["number"].startswith("A-")
        # isolation: a patient on B can't see or book A's slot
        b_slots = {s["id"] for s in client.get(f"/clinics/{S['B']['slug']}/availability",
                   params={"doctor": S["B"]["doctor_id"], "date": date}).json()["slots"]}
        assert S["A"]["slot_id"] not in b_slots
        leak = client.post("/bookings", headers={"X-Clinic-Slug": S["B"]["slug"]}, json={
            "doctor_id": S["A"]["doctor_id"], "mode": "slot", "slot_id": S["A"]["slot_id"],
            "patients": [{"name": "Leak"}], "contact_phone": "+919811100003", "consent": True})
        assert leak.status_code == 404, "a patient booked clinic A's slot through clinic B"

    # -- Stage 7: the doctor takes leave -> their remaining open slots close ----------------
    def test_07_doctor_takes_leave(self, client):
        date = S["_slot_date"]
        st = S["A"]
        before = client.get(f"/clinics/{st['slug']}/availability",
                            params={"doctor": st["doctor_id"], "date": date}).json()["slots"]
        assert len(before) == 3        # 4 generated, 1 booked in stage 6
        # the doctor marks themselves on leave for the day (self-service)
        lv = client.post("/slots/leave", headers=_h(st["doctor_token"], st["slug"]),
                         json={"doctor_id": st["doctor_id"], "date": date})
        assert lv.status_code == 200 and lv.json()["closed"] == 3 and lv.json()["still_booked"] == 1
        # patients can no longer book that day; the already-booked slot is untouched
        after = client.get(f"/clinics/{st['slug']}/availability",
                           params={"doctor": st["doctor_id"], "date": date}).json()["slots"]
        assert after == []
        # clinic B's availability is unaffected by A's doctor taking leave
        b_open = client.get(f"/clinics/{S['B']['slug']}/availability",
                            params={"doctor": S["B"]["doctor_id"], "date": date}).json()["slots"]
        assert len(b_open) == 3

    # -- Stage 8: patient WhatsApp-OTP login, scoped per clinic ----------------------------
    def test_08_patient_whatsapp_otp_login(self, client):
        from app.integrations.whatsapp import SENT_STUB
        a, b = S["A"]["slug"], S["B"]["slug"]
        SENT_STUB.clear()
        req = client.post("/auth/otp/request", headers={"X-Clinic-Slug": a}, json={"phone": "+919812300000"})
        assert req.status_code == 200 and SENT_STUB
        code = SENT_STUB[-1]["params"]["code"]
        # the code issued for clinic A must not verify on clinic B
        wrong = client.post("/auth/otp/verify", headers={"X-Clinic-Slug": b},
                            json={"phone": "+919812300000", "otp": code})
        assert wrong.status_code == 400
        # correct clinic -> a patient token scoped to clinic A
        ok = client.post("/auth/otp/verify", headers={"X-Clinic-Slug": a},
                         json={"phone": "+919812300000", "otp": code})
        assert ok.status_code == 200 and ok.json()["scope"] == "patient.self"
        # the issued patient token is scoped to clinic A (tenant_id lives inside the JWT)
        from app.core.security import decode_token
        claims = decode_token(ok.json()["access_token"])
        assert claims["tenant_id"] == S["A"]["tenant_id"] and claims["scope"] == "patient.self"
