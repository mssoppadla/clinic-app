"""Clinic slug helpers — used by onboarding to derive/validate the public path slug.

The slug is the public tenant key in tovaitech.in/appointments/<slug>, so it must be
URL-safe and must NOT collide with reserved routing paths (e.g. /appointments/onboard,
/appointments/assets/...). Lowercase, hyphen-separated, ASCII alnum only.
"""
from __future__ import annotations

import re

# Paths under /appointments/* that the web tier owns or reserves — never valid clinic slugs.
RESERVED_SLUGS = {
    "onboard", "assets", "admin", "api", "appointments", "clinics",
    "static", "health", "healthz", "register", "__canary__",
}

_MAX_LEN = 80


def slugify(text: str) -> str:
    """Normalize free text into a slug: lowercase, ascii alnum + single hyphens, trimmed."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)   # any run of non-alnum -> single hyphen
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:_MAX_LEN].rstrip("-")


def is_reserved(slug: str) -> bool:
    return slug in RESERVED_SLUGS


def is_valid(slug: str) -> bool:
    """A well-formed, non-reserved slug (3..80 chars, alnum/hyphen, no leading/trailing hyphen)."""
    if not slug or len(slug) < 3 or len(slug) > _MAX_LEN or is_reserved(slug):
        return False
    return re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) is not None
