"""Pydantic request/response schemas — mirror openapi_v1.yaml."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PatientIn(BaseModel):
    name: str
    abha_ref: str | None = None
    reason: str | None = None


class BookingCreate(BaseModel):
    doctor_id: str
    mode: str = "join_queue"
    slot_id: str | None = None
    patients: list[PatientIn] = Field(min_length=1, max_length=3)
    contact_phone: str
    whatsapp_same: bool = True
    reason: str | None = None
    consent: bool = False


class Money(BaseModel):
    amount_minor: int
    currency: str = "INR"
