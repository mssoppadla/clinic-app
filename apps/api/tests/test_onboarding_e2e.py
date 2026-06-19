"""End-to-end onboarding lifecycle for a SINGLE clinic, run as one sequential scenario:

  register -> (gated: booking blocked) -> add doctor & slots -> checklist all done
  -> provider approves go-live -> clinic is live -> patient books -> token issued -> in queue.

Each step builds on the state created by the previous one (one clinic, threaded through).
"""
from __future__ import annotations


def test_clinic_full_lifecycle_register_to_booking(client, superadmin_headers):
    # 1) REGISTER — new clinic, pending, not live
    reg = client.post("/onboarding/clinic", json={
        "name": "Riverside Multispecialty",
        "contact_name": "Dr. Anjali", "contact_email": "anjali@riverside.in",
        "languages": ["en", "ml"], "branding": {"hosting": "both"},
    })
    assert reg.status_code == 201, reg.text
    reg = reg.json()
    slug = reg["slug"]
    assert slug == "riverside-multispecialty"
    assert reg["go_live"] is False
    assert reg["readiness"]["mandatory_met"] is False

    # 2) GATED — the hosted page and booking are blocked until go-live
    assert client.get(f"/clinics/{slug}").status_code == 403
    blocked = client.post("/bookings",
                          json={"patients": [{"name": "Too Early"}], "contact_phone": "+919800001000",
                                "consent": True},
                          headers={"X-Clinic-Slug": slug})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "clinic_not_live"

    # 3) COMPLETE THE TODO — add a doctor + today's slots; the mandatory checklist turns green
    doc = client.post(f"/onboarding/clinic/{slug}/doctor", json={
        "name": "Dr. Pillai", "specialty": "General Medicine", "fee_inr": 300, "capacity": 30})
    assert doc.status_code == 201, doc.text
    doc = doc.json()
    doctor_id = doc["doctor_id"]
    assert doc["readiness"]["mandatory_met"] is True
    assert all(item["done"] for item in doc["readiness"]["mandatory"])

    # status endpoint agrees
    st = client.get("/onboarding/status", params={"slug": slug}).json()
    assert st["mandatory_met"] is True and st["go_live"] is False  # ready, but not yet approved

    # 4) ACTIVATE — provider approves go-live
    appr = client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug, "reason": "all checks passed"})
    assert appr.status_code == 200
    assert appr.json()["go_live"] is True and appr.json()["status"] == "active"

    # 5) LIVE — the hosted page now resolves and shows the doctor we added
    pub = client.get(f"/clinics/{slug}")
    assert pub.status_code == 200
    pub = pub.json()
    assert pub["queue_count"] == 0
    assert any(d["id"] == doctor_id for d in pub["doctors"])

    # 6) BOOK — a patient books an appointment for THIS clinic; a token is issued
    booking = client.post("/bookings", json={
        "doctor_id": doctor_id, "mode": "join_queue",
        "patients": [{"name": "Ravi Kumar", "reason": "fever"}],
        "contact_phone": "+919800001234", "reason": "fever & cough", "consent": True,
    }, headers={"X-Clinic-Slug": slug})
    assert booking.status_code == 201, booking.text
    booking = booking.json()
    assert booking["status"] == "confirmed"
    assert booking["party_size"] == 1
    assert booking["tokens"][0]["number"].startswith("A-")
    assert booking["tokens"][0]["short_code"]

    # 7) IN QUEUE — the booking shows up in the clinic's live queue
    assert client.get(f"/clinics/{slug}").json()["queue_count"] == 1
