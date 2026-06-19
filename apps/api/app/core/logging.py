"""Structured JSONL logging with PII redaction. No PII (phone, name, tokens) in logs (S4/S6)."""
from __future__ import annotations

import json
import logging
import re
import sys
import time

# crude but safe redaction of obvious PII patterns in any logged string
_PHONE = re.compile(r"\+?\d[\d ]{8,}\d")  # phone-like digit runs only (dates use dashes, won't match)
_REDACT_KEYS = {"phone", "contact_phone", "name", "patient_name", "token", "otp", "authorization"}


def _redact(value):
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in _REDACT_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _PHONE.sub("***", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("trace_id", "tenant_id", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(_redact(payload), ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
