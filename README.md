# Ruckus Aggregator

A swiss-army-knife tool that aggregates infrastructure metrics from the **top 500 most complex ServiceNow instances** using the Ruckus CLI (Squall API).

## What It Collects

For each instance, the tool pulls:
- **DB sizes** — combined, primary, per-database
- **Table sizes** — data length, index length, per-table breakdown
- **Row counts** — total rows, per-table row counts, top 20 tables
- **Node counts** — application nodes, app servers
- **Infrastructure** — capacity tier, DB type, release version, load balancing pools, service status

## How It Ranks Instances

Instances are ranked by a **composite complexity score** using up to 18 weighted dimensions:

| Dimension | Weight | Source |
|---|---|---|
| Transaction volume (90d avg) | 12% | prod_transactions CSV |
| DB size (GB) | 8% | prod_transactions CSV |
| Total row count | 8% | HI audit xlsx |
| ITSM tables | 8% | All_audit_table_statistics.xlsx |
| CMDB tables | 8% | All_audit_table_statistics.xlsx |
| Attachments | 6% | HI audit xlsx |
| Attachment docs | 6% | HI audit xlsx |
| ITOM/Discovery | 5% | All_audit_table_statistics.xlsx |
| Flows (High+Med) | 5% | HI audit xlsx |
| SecOps | 5% | All_audit_table_statistics.xlsx |
| ... and 8 more | | |

When audit xlsx files are not available, a **CSV-only fallback** uses 4 dimensions (txn volume, DB size, primary table size, capacity tier).

## Setup

```bash
pip install -r requirements.txt
```

Requirements: `openpyxl` (Excel), `rich` (progress/display). Python 3.8+.

**Prerequisites:**
- `ruckus` CLI installed and on PATH (`/usr/local/bin/ruckus`)
- LDAP credentials saved in keyring (or pass via `-u`/`-p`)
- `prod_transactions_03-31-26.csv` in `~/Downloads/`

## Usage

### Full run (rank → collect → report)
```bash
python main.py
```

### Top 100 instead of 500
```bash
python main.py --top 100
```

### Just rank instances (no collection)
```bash
python main.py --rank-only
```

### Report from cached data only (no live collection)
```bash
python main.py --skip-collect
```

### CSV-only scoring (skip audit xlsx files)
```bash
python main.py --csv-only
```

### Use GCC/FedRAMP environment
```bash
python main.py --gcc
```

### Explicit credentials + higher concurrency
```bash
python main.py -u myuser -p mypass --concurrency 25
```

## Output

All output goes to `~/Downloads/ruckus_aggregator_output/`:

| File | Description |
|---|---|
| `ruckus_dashboard.html` | Interactive HTML dashboard with search, sort, charts |
| `ruckus_aggregator_report.xlsx` | Excel workbook (Overview, Top Tables, Aggregates) |
| `ruckus_aggregated_data.json` | Full JSON for programmatic use |
| `cache/*.json` | Per-instance cached results (24h TTL) |

## Dashboard Features

- **5 tabs**: Overview, Databases, Tables, Nodes, Scoring
- **Sort** any column (click header)
- **Search** instances in real-time
- **Charts**: Top 20 by DB size, capacity tier distribution, DB type distribution
- **Export CSV** from any tab
- **Summary cards**: total DB size, total rows, avg nodes, top/median scores

## Architecture

```
main.py         CLI entry point — orchestrates the 3-step pipeline
scoring.py      Complexity scoring using CSV + optional audit xlsx
collector.py    Async ruckus CLI runner with caching and concurrency
reporter.py     HTML dashboard + Excel workbook generator
config.py       Paths, weights, constants
```

## Data Flow

```
prod_transactions CSV ──┐
                        ├─→ scoring.py ─→ top 500 ranked instances
HI audit xlsx files ────┘
                                │
                                ▼
                        collector.py ─→ ruckus CLI (batch JSON)
                                │         × 8 views per instance
                                │         × 15 concurrent
                                ▼
                        reporter.py ─→ HTML dashboard
                                     ─→ Excel workbook
                                     ─→ JSON dump
```
