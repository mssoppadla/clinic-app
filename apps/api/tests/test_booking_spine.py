"""The Phase-0 walking-skeleton proof: book -> token -> appears in queue, end to end."""
from __future__ import annotations


def test_clinic_public_returns_bilingual_labels(client, canary):
    r = client.get(f"/clinics/{canary['slug']}")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == canary["slug"]
    assert isinstance(body["queue_count"], int)
    assert "en" in body["labels"]
    # English always present; Malayalam in addition (canary opts into en,ml)
    assert "ml" in body["labels"]
    assert body["labels"]["en"]["Your token"] == "Your token"
    assert body["labels"]["ml"]["Your token"] != "Your token"  # localized via Bhashini fallback


def test_book_then_token_then_queue(client, canary):
    before = client.get(f"/clinics/{canary['slug']}").json()["queue_count"]
    payload = {
        "doctor_id": canary["doctor_id"],
        "mode": "join_queue",
        "patients": [{"name": "Arun Menon", "reason": "fever"}],
        "contact_phone": "+919800000001",
        "reason": "fever & cough",
        "consent": True,
    }
    r = client.post("/bookings", json=payload, headers={"X-Clinic-Slug": canary["slug"]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "confirmed"
    assert b["party_size"] == 1
    assert b["reason"] == "fever & cough"
    assert b["tokens"][0]["number"].startswith("A-")
    assert b["tokens"][0]["short_code"]
    assert b["tokens"][0]["eta"] is not None
    # queue grew by exactly one
    after = client.get(f"/clinics/{canary['slug']}").json()["queue_count"]
    assert after == before + 1


def test_multi_patient_overflow_up_to_three(client, canary):
    payload = {
        "doctor_id": canary["doctor_id"],
        "mode": "join_queue",
        "patients": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        "contact_phone": "+919800000002",
        "consent": True,
    }
    r = client.post("/bookings", json=payload, headers={"X-Clinic-Slug": canary["slug"]})
    assert r.status_code == 201
    assert len(r.json()["tokens"]) == 3


def test_consent_required(client, canary):
    payload = {
        "doctor_id": canary["doctor_id"],
        "patients": [{"name": "NoConsent"}],
        "contact_phone": "+919800000003",
        "consent": False,
    }
    r = client.post("/bookings", json=payload, headers={"X-Clinic-Slug": canary["slug"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "consent_required"
