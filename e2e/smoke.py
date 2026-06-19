"""Prod canary E2E smoke — the post-deploy gate for EVERY phase.

Books as the synthetic __canary__ clinic against a live BASE_URL and asserts the spine works:
clinic public -> book -> token -> queue grew -> idempotency holds. Run after blue-green switch;
non-zero exit signals rollback. Usage: BASE_URL=https://tovaitech.in/api/v1 python e2e/smoke.py
"""
from __future__ import annotations
import os, sys, uuid, httpx

BASE = os.environ.get("BASE_URL", "http://localhost:8077").rstrip("/")
SLUG = os.environ.get("CANARY_SLUG", "__canary__")

def main() -> int:
    with httpx.Client(base_url=BASE, timeout=15, trust_env=False) as c:
        pub = c.get(f"/clinics/{SLUG}"); pub.raise_for_status()
        d = pub.json()
        assert isinstance(d["queue_count"], int), "queue_count missing"
        assert "ml" in d["labels"], "Malayalam labels missing (Bhashini path)"
        before = d["queue_count"]
        doctor = d["doctors"][0]["id"]

        idem = "smoke-" + uuid.uuid4().hex[:10]
        payload = {"doctor_id": doctor, "mode": "join_queue",
                   "patients": [{"name": "Smoke Canary"}], "contact_phone": "+910000000000",
                   "reason": "smoke", "consent": True}
        h = {"X-Clinic-Slug": SLUG, "Idempotency-Key": idem}
        r1 = c.post("/bookings", json=payload, headers=h); 
        assert r1.status_code == 201, f"book failed: {r1.status_code} {r1.text}"
        tok = r1.json()["tokens"][0]
        assert tok["number"] and tok["short_code"], "token/short_code missing"

        r2 = c.post("/bookings", json=payload, headers=h)
        assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"], "idempotency broken"

        after = c.get(f"/clinics/{SLUG}").json()["queue_count"]
        assert after == before + 1, f"queue did not grow ({before}->{after})"
    print("SMOKE PASS:", BASE)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("SMOKE FAIL:", e); sys.exit(1)
