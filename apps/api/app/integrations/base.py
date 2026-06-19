"""Integration provider interfaces. Implementations are selected by env mode
(stub | live) so live credentials drop in without touching call sites."""
from __future__ import annotations

from typing import Protocol


class WhatsAppProvider(Protocol):
    def send_template(self, *, tenant_id: str, to_phone: str, template: str,
                      params: dict) -> dict: ...


class LanguageProvider(Protocol):
    def localize(self, *, tenant_id: str, keys: dict[str, str], target_lang: str) -> dict[str, str]: ...
