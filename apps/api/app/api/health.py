"""Liveness/readiness probes for blue-green health gating."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from ..core.db import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "detail": str(exc)}
