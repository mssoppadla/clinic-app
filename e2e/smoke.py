"""Prod canary E2E smoke — the post-deploy gate for EVERY phase.

Books as the synthetic __canary__ clinic against a live BASE_URL and asserts the spine works:
clinic public -> book -> token -> queue grew -> idempotency holds. Run after the container swap;
non-zero exit signals rollback. Uses only the Python standard library (no pip deps) so it runs
on the VPS host as well as in CI. Usage: BASE_URL=https://tovaitech.in/api/v1 python e2e/smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE_URL", "http://localhost:8077").rstrip("/")
SLUG = os.environ.get("CANARY_SLUG", "__canary__")


def _req(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main() -> int:
    status, d = _req("GET", f"/clinics/{SLUG}")
    assert status == 200, f"clinic fetch {status}"
    assert isinstance(d["queue_count"], int), "queue_count missing"
    assert "ml" in d["labels"], "Malayalam labels missing (Bhashini path)"
    before = d["queue_count"]
    doctor = d["doctors"][0]["id"]

    idem = "smoke-" + uuid.uuid4().hex[:10]
    payload = {"doctor_id": doctor, "mode": "join_queue",
               "patients": [{"name": "Smoke Canary"}], "contact_phone": "+910000000000",
               "reason": "smoke", "consent": True}
    h = {"X-Clinic-Slug": SLUG, "Idempotency-Key": idem}

    s1, b1 = _req("POST", "/bookings", payload, h)
    assert s1 == 201, f"book failed: {s1} {b1}"
    tok = b1["tokens"][0]
    assert tok["number"] and tok["short_code"], "token/short_code missing"

    s2, b2 = _req("POST", "/bookings", payload, h)
    assert s2 == 200 and b2["id"] == b1["id"], "idempotency broken"

    _, d2 = _req("GET", f"/clinics/{SLUG}")
    after = d2["queue_count"]
    assert after == before + 1, f"queue did not grow ({before}->{after})"
    print("SMOKE PASS:", BASE)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print("SMOKE FAIL:", e)
        sys.exit(1)
