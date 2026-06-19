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


@pytest.fixture(autouse=True)
def _reset_integration_config():
    # integration_config is global (platform scope); reset before each test so providers
    # default to stub unless a test sets otherwise (prevents cross-test leakage).
    from app.core.db import session_scope
    from app.models import IntegrationConfig
    with session_scope() as db:
        db.query(IntegrationConfig).delete()
    yield
