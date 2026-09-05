# Heimdall Data Quality & Feature Backlog

> Live tracker for gaps surfaced during dashboard build. Items are ordered by impact and grouped by theme.

## Data Quality

| # | Gap | Count / Impact | Suggested Fix | Status |
|---|---|---|---|---|
| DQ-1 | Blank company on instances | 750 local, 1112 Render (before xlsx backfill) | Backfilled 362 on Render from `cmdb_ci_service` xlsx; remaining need alternate mapping or ServiceNow lookup | Partial |
| DQ-2 | Zero node count | 1,518 (10.2%) | Audit aggregator node parsing; some instances may be dormant or have no `nodes` array | Open |
| DQ-3 | Zero DB size | 1,594 (10.7%) | Audit `db_gb_csv` source; may be missing Ruckus data for test/internal instances | Open |
| DQ-4 | Cannot determine CSP status | 2,122 (14.3%) have no datacenter | Need node hostname data or ServiceNow `cmdb_ci_service` source to get datacenter | Open |
| DQ-5 | Case collector only fetched ~422k of 2.1M records | Default 365-day lookback + skips empty account.name | Updated `case_collector.py` with `--all` month-windowed pagination; still need to run full sync | Capable |
| DQ-6 | `case_records` empty on Render | Case data only on local MariaDB | Sync local 422k cases to Render or re-run collector against Render PostgreSQL | Open |
| DQ-7 | Account→instance mapping incomplete | Many cases imported with `instance=NULL` | Use `cmdb_ci_service` xlsx to build stronger account→instance map and re-derive in collector | Open |

## Features

| # | Feature | Status |
|---|---|---|
| F-1 | Right-click "Show Matching" / "Filter Out" on table cells | Done |
| F-2 | CSP (Cloud Service Provider) column instead of HS emoji | Done |
| F-3 | Per-instance Cases tab in expanded row | Done |
| F-4 | Data-quality dashboard card showing DQ-1..DQ-4 counts | Open |
| F-5 | Backfill company from alternate ServiceNow source for remaining 750 | Open |
| F-6 | Full historical case sync (2.1M) | Open (collector ready) |

## Commands for reference

```bash
# Full case history
.venv/bin/python3 dashboard/backend/case_collector.py --all

# Specific date range
.venv/bin/python3 dashboard/backend/case_collector.py --start 2023-01-01 --end 2026-08-31
```
