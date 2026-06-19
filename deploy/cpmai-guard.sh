#!/usr/bin/env bash
# Snapshot row counts of critical tables before a deploy; after deploy compare and ABORT
# (signal rollback) if any table shrank unexpectedly. Protects cpmai + existing clinic data.
# Usage: cpmai-guard.sh snapshot|verify  (uses $DATABASE_URL via psql)
set -euo pipefail
SNAP="${SNAP_FILE:-/tmp/clinic_rowcounts.txt}"
TABLES="${GUARD_TABLES:-tenants doctors patients bookings booking_events tokens queue_entries}"
counts(){ for t in $TABLES; do n=$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo NA); echo "$t=$n"; done; }
case "${1:-}" in
  snapshot) counts > "$SNAP"; echo "snapshot written to $SNAP"; cat "$SNAP" ;;
  verify)
    fail=0
    while IFS='=' read -r t before; do
      [ "$before" = "NA" ] && continue
      after=$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM $t" 2>/dev/null || echo NA)
      if [ "$after" != "NA" ] && [ "$after" -lt "$before" ]; then
        echo "DATA LOSS on $t: before=$before after=$after"; fail=1
      fi
    done < "$SNAP"
    [ "$fail" = "1" ] && { echo "GUARD FAILED -> rollback"; exit 1; }
    echo "OK - no data loss detected." ;;
  *) echo "usage: $0 snapshot|verify"; exit 2 ;;
esac
