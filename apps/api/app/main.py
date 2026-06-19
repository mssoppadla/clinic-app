"""FastAPI application entry point."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api import admin, auth, bookings, clinics, health, onboarding, slots, users
from .core.config import get_settings
from .core.errors import AppError, app_error_handler
from .core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="Clinic Booking SaaS API", version="1.0.0",
              root_path=f"/api/{settings.api_version}" if settings.env != "local" else "")

# CORS origins are configurable (no hardcoding); empty in prod where same-origin is used.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    request.state.trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


app.add_exception_handler(AppError, app_error_handler)

app.include_router(health.router)
app.include_router(clinics.router)
app.include_router(bookings.router)
app.include_router(admin.router)
app.include_router(onboarding.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(slots.router)


@app.get("/")
def root():
    return {"app": settings.app_name, "version": "1.0.0", "env": settings.env}
