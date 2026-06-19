"""Onboarding [C34]: register -> pending (not live) -> provider approve -> live."""
from __future__ import annotations


def test_slug_available_normalizes_and_rejects_reserved(client):
    r = client.get("/onboarding/clinic/slug-available", params={"slug": "Green Cross Clinic!"})
    body = r.json()
    assert body["normalized"] == "green-cross-clinic"
    assert body["available"] is True

    r = client.get("/onboarding/clinic/slug-available", params={"slug": "onboard"})
    assert r.json()["available"] is False
    assert r.json()["reason"] == "reserved"


def test_register_then_gated_then_approved(client, superadmin_headers):
    # 1. register -> created, not live
    r = client.post("/onboarding/clinic", json={
        "name": "Green Cross", "contact_name": "Dr. Menon",
        "contact_email": "menon@example.com", "languages": ["en", "ml"]})
    assert r.status_code == 201, r.text
    reg = r.json()
    slug = reg["slug"]
    assert slug == "green-cross"
    assert reg["go_live"] is False
    assert reg["hosted_page"] == f"/appointments/{slug}"
    assert reg["readiness"]["mandatory_met"] is False  # no doctor/slots yet

    # 2. hosted page is gated until go-live
    r = client.get(f"/clinics/{slug}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "clinic_not_live"

    # 3. shows up in the provider's pending list
    pending = client.get("/onboarding/pending", headers=superadmin_headers).json()["pending"]
    assert any(p["slug"] == slug for p in pending)

    # 4. provider approves go-live -> now live
    r = client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug, "reason": "verified"})
    assert r.status_code == 200
    assert r.json()["go_live"] is True and r.json()["status"] == "active"

    r = client.get(f"/clinics/{slug}")
    assert r.status_code == 200
    assert r.json()["slug"] == slug


def test_appearance_config_roundtrip_and_reflected_publicly(client, superadmin_headers):
    # register with initial branding
    reg = client.post("/onboarding/clinic", json={
        "name": "Sunrise Clinic",
        "branding": {"color": "#aa0000", "headline": "Sunrise", "bogus": "drop-me"}}).json()
    slug = reg["slug"]

    # configurator can read it pre-go-live (not gated)
    appr = client.get("/onboarding/appearance", params={"slug": slug}).json()
    assert appr["branding"]["color"] == "#aa0000"
    assert appr["branding"]["headline"] == "Sunrise"
    assert "bogus" not in appr["branding"]            # whitelisted

    # update (merge) appearance
    r = client.post("/onboarding/appearance", json={
        "slug": slug, "branding": {"accent": "#0000aa", "book_label": "Reserve now", "show_header": True}})
    assert r.status_code == 200
    b = r.json()["branding"]
    assert b["color"] == "#aa0000" and b["accent"] == "#0000aa"  # merged, not replaced
    assert b["book_label"] == "Reserve now" and b["show_header"] is True

    # after go-live, the public clinic view carries the branding (hosted + embed render it)
    client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug})
    pub = client.get(f"/clinics/{slug}").json()
    assert pub["branding"]["accent"] == "#0000aa"
    assert pub["branding"]["book_label"] == "Reserve now"


def test_add_doctor_completes_mandatory_readiness(client):
    slug = client.post("/onboarding/clinic", json={"name": "Lakeside Clinic"}).json()["slug"]
    # before: doctor_slots is not done -> not ready
    st = client.get("/onboarding/status", params={"slug": slug}).json()
    assert st["mandatory_met"] is False
    assert any(m["key"] == "doctor_slots" and not m["done"] for m in st["mandatory"])

    # add a doctor + slot
    r = client.post(f"/onboarding/clinic/{slug}/doctor",
                    json={"name": "Dr. Roy", "specialty": "ENT", "fee_inr": 300, "capacity": 30})
    assert r.status_code == 201, r.text
    rd = r.json()["readiness"]
    assert rd["mandatory_met"] is True
    assert any(m["key"] == "doctor_slots" and m["done"] for m in rd["mandatory"])


def test_duplicate_name_gets_unique_slug(client):
    a = client.post("/onboarding/clinic", json={"name": "City Hospital"}).json()
    b = client.post("/onboarding/clinic", json={"name": "City Hospital"}).json()
    assert a["slug"] == "city-hospital"
    assert b["slug"] == "city-hospital-2"
