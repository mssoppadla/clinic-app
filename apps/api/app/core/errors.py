"""Uniform error envelope (matches openapi Error schema)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 field_errors: list[dict] | None = None, retryable: bool = False):
        self.code = code
        self.message = message
        self.status = status
        self.field_errors = field_errors or []
        self.retryable = retryable


def envelope(code: str, message: str, trace_id: str = "", field_errors=None, retryable=False) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "trace_id": trace_id,
            "retryable": retryable,
        }
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", "")
    return JSONResponse(
        status_code=exc.status,
        content=envelope(exc.code, exc.message, trace_id, exc.field_errors, exc.retryable),
    )
