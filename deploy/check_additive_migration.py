"""Reject non-additive DDL in Alembic migrations (expand-contract / backward-compat gate).
Fails CI if any migration contains DROP TABLE/COLUMN or RENAME (breaking changes).
Allowed breaking changes must go through a new /api/v2 + explicit override label."""
from __future__ import annotations
import re, sys, pathlib

FORBIDDEN = [r"\bDROP\s+TABLE\b", r"\bDROP\s+COLUMN\b", r"\bRENAME\s+TO\b",
             r"\bRENAME\s+COLUMN\b", r"op\.drop_table", r"op\.drop_column", r"op\.alter_column\(.*new_column_name"]
root = pathlib.Path(__file__).resolve().parents[1] / "apps/api/migrations/versions"
bad = []
for f in root.glob("*.py"):
    txt = f.read_text()
    # only the UPGRADE path must be additive; downgrades are expected to drop/revert
    txt = txt.split("def downgrade")[0]
    for pat in FORBIDDEN:
        for m in re.finditer(pat, txt, re.I):
            bad.append(f"{f.name}: {m.group(0)}")
if bad:
    print("NON-ADDITIVE MIGRATION DETECTED (backward-compat gate failed):")
    print("\n".join(bad)); sys.exit(1)
print("OK - all migrations are additive (expand-contract).")
