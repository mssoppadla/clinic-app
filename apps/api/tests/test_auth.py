"""Phase-2 auth slice 2a: login/refresh + role-gated provider endpoints."""
from __future__ import annotations


def _make_superadmin(email="root@tovaitech.test", pw="rootpass123"):
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, password_hash=hash_password(pw),
                     must_reset_password=False, status="active")
            db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=None, role="superadmin"))


def test_login_rejects_bad_credentials(client):
    _make_superadmin()
    r = client.post("/auth/login", json={"email": "root@tovaitech.test", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_login_issues_tokens(client):
    _make_superadmin()
    r = client.post("/auth/login", json={"email": "root@tovaitech.test", "password": "rootpass123"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["roles"][0]["role"] == "superadmin"

    # refresh mints a fresh access token
    rr = client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert rr.status_code == 200 and rr.json()["access_token"]


def test_gated_endpoint_requires_auth(client):
    # no token -> 401
    assert client.get("/onboarding/pending").status_code == 401
    # bogus token -> 401
    assert client.get("/onboarding/pending",
                      headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_gated_endpoint_allows_superadmin(client, superadmin_headers):
    assert client.get("/onboarding/pending", headers=superadmin_headers).status_code == 200


def test_non_superadmin_is_forbidden(client):
    # a clinic_admin token must NOT reach a superadmin-only endpoint
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    email = "ca@clinic.test"
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, password_hash=hash_password("capass123"),
                     must_reset_password=False, status="active")
            db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id="some-tenant", role="clinic_admin"))
    tok = client.post("/auth/login", json={"email": email, "password": "capass123"}).json()["access_token"]
    r = client.get("/onboarding/pending", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
