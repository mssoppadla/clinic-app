#!/usr/bin/env python3
"""Upsert KEY=VALUE pairs into an env file, preserving all other lines/comments.

Used by .github/workflows/deploy.yml to push GitHub-managed app secrets into the VPS
/opt/clinic-app/.env on each deploy. The base64-encoded pairs are passed via the env var
`P` (not argv, so they don't show up in `ps`); empty values are skipped so a missing
GitHub secret never blanks an existing key.

Usage:  P="$(cat | ...)" python3 upsert_env.py /opt/clinic-app/.env
        (P = base64 of "KEY=VALUE\n..." lines)
"""
from __future__ import annotations

import base64
import os
import pathlib
import sys


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    raw = base64.b64decode(os.environ.get("P", "")).decode("utf-8")

    pairs: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and v != "":            # skip empty -> don't overwrite/blank an existing key
            pairs[k] = v

    existing = target.read_text().splitlines() if target.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for ln in existing:
        key = ln.split("=", 1)[0].strip() if ("=" in ln and not ln.lstrip().startswith("#")) else None
        if key in pairs:
            out.append(f"{key}={pairs[key]}")
            seen.add(key)
        else:
            out.append(ln)
    for k, v in pairs.items():
        if k not in seen:
            out.append(f"{k}={v}")

    target.write_text("\n".join(out) + "\n")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"upsert_env: set {sorted(pairs)} in {target}")


if __name__ == "__main__":
    main()
