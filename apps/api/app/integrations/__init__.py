"""Integration provider factory. Clients read runtime config (env+DB) themselves."""
from __future__ import annotations

from functools import lru_cache

from .bhashini import BhashiniClient
from .whatsapp import WhatsAppClient


@lru_cache
def whatsapp() -> WhatsAppClient:
    return WhatsAppClient()


@lru_cache
def bhashini() -> BhashiniClient:
    return BhashiniClient()
