"""Regression guard for a class of prod-only 500s: several columns store a short prefix + a
36-char tenant UUID (e.g. 'clinic:<uuid>', 'patient_login:<uuid>'). SQLite ignores varchar length
so these overflow silently in tests/local but raise StringDataRightTruncation on Postgres (prod).
Assert the declared column lengths are big enough. [see migration 0014]"""
from __future__ import annotations

from app.models import IntegrationConfig, OtpChallenge

UUID_LEN = 36  # new_id() -> canonical 36-char UUID string


def test_integration_config_scope_fits_clinic_prefixed_uuid():
    length = IntegrationConfig.__table__.c.scope.type.length
    assert length >= len("clinic:") + UUID_LEN, "scope too short for 'clinic:<uuid>' (43) -> prod 500"


def test_otp_challenge_purpose_fits_patient_login_prefixed_uuid():
    length = OtpChallenge.__table__.c.purpose.type.length
    assert length >= len("patient_login:") + UUID_LEN, \
        "purpose too short for 'patient_login:<uuid>' (50) -> prod 500"
