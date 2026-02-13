#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

RANGE=$(python3 - <<'PY'
from datetime import date, timedelta
first_this_month = date.today().replace(day=1)
end_prev = first_this_month - timedelta(days=1)
start_prev = end_prev.replace(day=1)
print(start_prev.isoformat(), end_prev.isoformat())
PY
)

START=$(echo "$RANGE" | awk '{print $1}')
END=$(echo "$RANGE" | awk '{print $2}')

echo "[INFO] Collecting Windows Server advisories: ${START} ~ ${END}"
python3 windows_server_update_monitor.py \
  --version all \
  --start-date "$START" \
  --end-date "$END" \
  --outdir .

echo "[DONE] Reports generated in $(pwd)"
