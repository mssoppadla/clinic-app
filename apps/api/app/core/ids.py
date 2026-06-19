"""ID + short-code helpers. UUIDs generated in Python so the ORM is portable
across SQLite (sandbox tests) and Postgres (real)."""
from __future__ import annotations

import secrets
import uuid

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


def new_id() -> str:
    return str(uuid.uuid4())


def short_code(n: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))
