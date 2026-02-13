# Windows Patch Advisory Automation Research

This document describes a Windows-focused automated patch advisory collection workflow, modeled after the structure of **OS Patch Advisory Automation Research**.

## Purpose

Replace manual Microsoft update checks with a **reliable, automated batch collection** pipeline for:
- Windows Server 2016
- Windows Server 2019
- Windows Server 2022
- Windows Server 2025

Target use case:
- Monthly patch review board preparation
- Exact period collection (e.g., previous calendar month)
- Repeatable outputs for reporting and audit

---

## Contents

### Documentation
- **`windows_server_update_monitor_design.md`**: architecture, data flow, test cases
- **`README_WINDOWS_PATCH_AUTOMATION.md`**: this file

### Script
- **`windows_server_update_monitor.py`**
  - Collects updates from Microsoft official sources
  - Parses KB-level details (summary / known issues)
  - Applies risk classification (LOW/MEDIUM/HIGH)
  - Writes report artifacts and cumulative DB
- **`run_monthly.sh`**
  - Calculates exact previous calendar month
  - Runs full collection for all Windows Server versions

### Output Artifacts
- `ws<version>_updates_<YYYYMMDD>.md`
- `ws<version>_updates_<YYYYMMDD>.json`
- `ws<version>_updates_latest.md`
- `ws<version>_patch_db.json` (deduplicated cumulative DB)
- `batch_data/ws<version>/KB<id>_<date>.json` (per-advisory files)

---

## Collection Strategy

| Source | Method | Reliability |
|---|---|---|
| Microsoft Learn (Windows Server release info) | Trusted feed parsing | High |
| Microsoft Support KB pages | Direct detail parsing | High |

### Why this approach?
- No search-engine dependency
- Deterministic date filtering
- Better coverage than ad-hoc manual checks
- Repeatable outputs for board review

---

## Quick Start

```bash
cd /Users/hmoo/.openclaw/workspace
python3 windows_server_update_monitor.py --version all --window-months 1 --outdir .
```

### Exact previous month (recommended for monthly review)
```bash
START_END=$(python3 - <<'PY'
from datetime import date,timedelta
t=date.today().replace(day=1)
e=t-timedelta(days=1)
s=e.replace(day=1)
print(s.isoformat(), e.isoformat())
PY
)

START=$(echo "$START_END" | awk '{print $1}')
END=$(echo "$START_END" | awk '{print $2}')

python3 windows_server_update_monitor.py --version all --start-date "$START" --end-date "$END" --outdir .
```

---

## Configuration Options

- `--version` : `all` | `2016` | `2019` | `2022` | `2025` | comma list
- `--exclude` : exclude versions (comma separated)
- `--window-months` : `1` | `3` | `6`
- `--start-date` / `--end-date` : explicit period (`YYYY-MM-DD`)
- `--max-kb` : max KB entries to inspect per version
- `--outdir` : output directory

---

## Typical Monthly Workflow

1. Compute previous calendar month (`start/end`)
2. Run collection for all Windows Server versions
3. Review per-version latest markdown reports
4. Review HIGH/MEDIUM advisories first
5. Use cumulative DB (`ws<version>_patch_db.json`) for trend/history
6. Publish patch review board summary

---

## Risk Classification (Current)

- **HIGH**: outage/restart/auth failures/RCE-like keywords
- **MEDIUM**: security/driver/compatibility concerns
- **LOW**: no known issues and low-risk signals

> Note: Heuristic-based classification. Final patch decisions should include environment-specific impact validation.

---

## Integration (Cron)

Recommended schedule:
- Monthly, day 1, 10:00 (Asia/Seoul)
- Run with explicit previous-month date range
- Send concise Korean summary with:
  1) version-wise major changes,
  2) known risks/affected services,
  3) operational checklist

---

## Known Limitations

- HTML structure changes on source pages may require parser updates
- Heuristic risk model (not CVSS-native)
- Network/transient fetch failures can affect single-run completeness

---

## Next Improvements

- Retry/backoff and partial-failure marking per KB
- CVE extraction + severity linkage
- XLSX export for board packs
- Diff report (this month vs last month)

---

## Revision History

- **2026-02-13**: Initial Windows automation README created
  - Trusted-source collection strategy documented
  - Date-range monthly workflow standardized
  - Output and review process defined
