"""Test harness: isolated SQLite DB per run + TestClient. Proves the spine on localhost."""
from __future__ import annotations

import os
import tempfile

import pytest

# point the app at a throwaway SQLite file BEFORE importing app modules
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["APP_DATABASE_URL"] = f"sqlite+pysqlite:///{_tmp.name}"
os.environ["APP_ENV"] = "ci"
os.environ["APP_WHATSAPP_MODE"] = "stub"
os.environ["APP_BHASHINI_MODE"] = "stub"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import ensure_schema, seed_canary  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    ensure_schema()
    yield


@pytest.fixture()
def canary():
    return seed_canary()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def superadmin_headers(client):
    """A logged-in superadmin's Authorization header (for the gated provider/admin endpoints)."""
    from app.core.db import system_session
    from app.core.security import hash_password
    from app.models import User, UserRole
    email = "root@tovaitech.test"
    with system_session() as db:
        if db.query(User).filter(User.email == email).first() is None:
            u = User(email=email, password_hash=hash_password("rootpass123"),
                     must_reset_password=False, status="active")
            db.add(u)
            db.flush()
            db.add(UserRole(user_id=u.id, tenant_id=None, role="superadmin"))
    r = client.post("/auth/login", json={"email": email, "password": "rootpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _reset_integration_config():
    # integration_config is global (platform scope); reset before each test so providers
    # default to stub unless a test sets otherwise (prevents cross-test leakage).
    from app.core.db import session_scope
    from app.models import IntegrationConfig
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
    yield
