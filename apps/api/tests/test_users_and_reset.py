"""Phase-2 slices 2b/2c: user management, forced first-login reset, WhatsApp-OTP reset,
and clinic_admin auto-creation on go-live approval."""
from __future__ import annotations


def _login(client, email, pw):
    return client.post("/auth/login", json={"email": email, "password": pw}).json()


def _mk_user(email, pw, role, tenant_id=None, must_reset=False, phone=None):
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, phone=phone, password_hash=hash_password(pw),
                     must_reset_password=must_reset, status="active")
            db.add(u); db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=tenant_id, role=role))


def test_superadmin_creates_clinic_admin(client, superadmin_headers):
    r = client.post("/users", headers=superadmin_headers,
                    json={"email": "ca@x.com", "role": "clinic_admin", "tenant_id": "t-1"})
    assert r.status_code == 201, r.text
    assert r.json()["temp_password"] and r.json()["must_reset_password"] is True


def test_clinic_admin_creates_staff_with_scoping(client):
    _mk_user("admin1@c.com", "pw12345678", "clinic_admin", tenant_id="t-100")
    tok = _login(client, "admin1@c.com", "pw12345678")["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    r = client.post("/users", headers=h, json={"email": "doc@c.com", "role": "doctor"})
    assert r.status_code == 201, r.text
    temp = r.json()["temp_password"]

    # can't target another clinic, can't grant superadmin
    assert client.post("/users", headers=h,
                       json={"email": "x@y.com", "role": "doctor", "tenant_id": "t-999"}).status_code == 403
    assert client.post("/users", headers=h,
                       json={"email": "z@y.com", "role": "superadmin"}).status_code in (403, 422)

    # the new doctor is forced to reset on first login
    assert _login(client, "doc@c.com", temp)["must_reset_password"] is True


def test_recreate_revoked_user_reactivates(client):
    _mk_user("admin3@c.com", "pw12345678", "clinic_admin", tenant_id="t-react")
    h = {"Authorization": f"Bearer {_login(client, 'admin3@c.com', 'pw12345678')['access_token']}"}

    created = client.post("/users", headers=h, json={"email": "doc3@c.com", "role": "doctor"}).json()
    uid = created["id"]
    assert client.post(f"/users/{uid}/revoke", headers=h).json()["status"] == "revoked"

    # re-create the same email -> reactivated (not 409), new temp password, can change role
    again = client.post("/users", headers=h, json={"email": "doc3@c.com", "role": "front_desk"})
    assert again.status_code == 201, again.text
    body = again.json()
    assert body["id"] == uid and body["reactivated"] is True
    assert body["status"] == "active" and body["temp_password"]
    assert any(r["role"] == "front_desk" for r in body["roles"])
    # the regenerated temp password works (and forces a reset)
    assert _login(client, "doc3@c.com", body["temp_password"])["must_reset_password"] is True


def test_recreate_active_user_still_conflicts(client):
    _mk_user("admin4@c.com", "pw12345678", "clinic_admin", tenant_id="t-c4")
    h = {"Authorization": f"Bearer {_login(client, 'admin4@c.com', 'pw12345678')['access_token']}"}
    client.post("/users", headers=h, json={"email": "dup@c.com", "role": "doctor"})
    r = client.post("/users", headers=h, json={"email": "dup@c.com", "role": "doctor"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "identifier_taken"


def test_username_only_user_login(client, superadmin_headers):
    r = client.post("/users", headers=superadmin_headers,
                    json={"username": "drmenon", "role": "doctor", "tenant_id": "t-u", "phone": "+919800000010"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] is None and r.json()["username"] == "drmenon"
    login = _login(client, "drmenon", r.json()["temp_password"])   # login by username
    assert login.get("access_token") and login["must_reset_password"] is True
    assert login["user"]["roles"][0]["role"] == "doctor"


def test_create_requires_email_or_username(client, superadmin_headers):
    r = client.post("/users", headers=superadmin_headers, json={"role": "doctor", "tenant_id": "t-u2"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "identifier_required"


def test_forgot_reset_by_username(client, superadmin_headers):
    from app.integrations.whatsapp import SENT_STUB
    client.post("/users", headers=superadmin_headers,
                json={"username": "nurse1", "role": "front_desk", "tenant_id": "t-u3", "phone": "+919800000011"})
    SENT_STUB.clear()
    assert client.post("/auth/forgot", json={"identifier": "nurse1"}).status_code == 200
    assert SENT_STUB
    code = SENT_STUB[-1]["params"]["code"]
    assert client.post("/auth/reset",
                       json={"identifier": "nurse1", "otp": code, "new_password": "newpass999"}).status_code == 200
    assert _login(client, "nurse1", "newpass999")["must_reset_password"] is False


def test_change_password_clears_reset_flag(client):
    _mk_user("staff@c.com", "temp12345", "doctor", tenant_id="t-1", must_reset=True)
    login = _login(client, "staff@c.com", "temp12345")
    assert login["must_reset_password"] is True
    r = client.post("/auth/change-password", headers={"Authorization": f"Bearer {login['access_token']}"},
                    json={"current_password": "temp12345", "new_password": "newpass123"})
    assert r.status_code == 200
    assert _login(client, "staff@c.com", "newpass123")["must_reset_password"] is False


def test_forgot_then_reset_via_whatsapp_otp(client):
    from app.integrations.whatsapp import SENT_STUB
    _mk_user("reset@c.com", "oldpass123", "doctor", tenant_id="t-1", phone="+919800000001")
    SENT_STUB.clear()
    assert client.post("/auth/forgot", json={"email": "reset@c.com"}).status_code == 200
    assert SENT_STUB, "OTP should be sent via the WhatsApp stub"
    code = SENT_STUB[-1]["params"]["code"]

    assert client.post("/auth/reset",
                       json={"email": "reset@c.com", "otp": "000000", "new_password": "brandnew123"}).status_code == 400
    assert client.post("/auth/reset",
                       json={"email": "reset@c.com", "otp": code, "new_password": "brandnew123"}).status_code == 200
    assert _login(client, "reset@c.com", "brandnew123")["must_reset_password"] is False


def test_forgot_unknown_email_is_silent(client):
    assert client.post("/auth/forgot", json={"email": "nobody@nowhere.com"}).status_code == 200


def test_list_clinics_powers_the_picker(client, superadmin_headers):
    slug = client.post("/onboarding/clinic",
                       json={"name": "Picker Clinic", "contact_email": "p@x.com"}).json()["slug"]
    client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug})
    r = client.get("/onboarding/clinics", headers=superadmin_headers)
    assert r.status_code == 200
    clinics = r.json()["clinics"]
    assert any(c["slug"] == slug and c["go_live"] for c in clinics)
    assert all(c["slug"] != "__canary__" for c in clinics)   # synthetic canary excluded


def test_list_clinics_forbidden_for_clinic_admin(client):
    _mk_user("ca2@c.com", "pw12345678", "clinic_admin", tenant_id="t-7")
    tok = _login(client, "ca2@c.com", "pw12345678")["access_token"]
    assert client.get("/onboarding/clinics", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_go_live_approval_autocreates_clinic_admin(client, superadmin_headers):
    slug = client.post("/onboarding/clinic",
                       json={"name": "Autocreate Clinic", "contact_email": "owner@auto.com"}).json()["slug"]
    appr = client.post("/onboarding/override", headers=superadmin_headers, json={"slug": slug}).json()
    assert appr["clinic_admin"]["email"] == "owner@auto.com"
    login = _login(client, "owner@auto.com", appr["clinic_admin"]["temp_password"])
    assert login["user"]["roles"][0]["role"] == "clinic_admin"
    assert login["must_reset_password"] is True
