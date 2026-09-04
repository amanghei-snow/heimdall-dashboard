"""
Report generator: interactive HTML dashboard + Excel workbook.
"""
import json
import os
import time
from datetime import datetime
from typing import Dict, List, Tuple

from rich.console import Console

import config
from collector import (
    extract_capacity_tier,
    extract_db_info,
    extract_instance_info,
    extract_node_info,
    extract_table_info,
)
from history import compute_aggregator_deltas, get_previous_aggregator_snapshot, save_aggregator_run

console = Console()


# ── Build aggregated row data ────────────────────────────────────────────────

def _build_rows(
    ranked: List[Tuple[str, float, int, dict]],
    collected: Dict[str, dict],
    csv_data: Dict[str, dict],
    company_map: Dict[str, str] = None,
) -> List[dict]:
    """
    Merge scoring, CSV, and ruckus-collected data into flat rows
    for the dashboard and Excel export.
    """
    cmap = company_map or {}
    rows = []

    # Load previous snapshot so we can fall back when collection fails
    prev_snapshot = get_previous_aggregator_snapshot() or {}

    for rank, (inst, score, coverage, dims) in enumerate(ranked, 1):
        cd = collected.get(inst, {})
        csv_d = csv_data.get(inst, {})

        db_info = extract_db_info(cd)
        table_info = extract_table_info(cd)
        node_info = extract_node_info(cd)
        inst_info = extract_instance_info(cd)

        has_ruckus_data = len(cd.get("views", {})) > 0

        total_table_rows = sum(t.get("rows", 0) or 0 for t in table_info)
        total_data_size = sum(t.get("data_length", 0) or 0 for t in table_info)
        total_index_size = sum(t.get("index_length", 0) or 0 for t in table_info)
        table_count = len(table_info)

        # Top 20 tables by row count — deduplicate by name (same table across shards)
        _seen_tables = {}
        for t in table_info:
            tname = t.get("name", "")
            trows = t.get("rows", 0) or 0
            if tname not in _seen_tables or trows > (_seen_tables[tname].get("rows", 0) or 0):
                _seen_tables[tname] = t
        top_tables = sorted(_seen_tables.values(), key=lambda t: t.get("rows", 0) or 0, reverse=True)[:20]

        # If collection failed (empty views), fall back to previous values
        prev = prev_snapshot.get(inst, {})
        if not has_ruckus_data and prev:
            total_table_rows = total_table_rows or prev.get("total_table_rows", 0)
            table_count = table_count or prev.get("table_count", 0)
            total_data_size = total_data_size or int(prev.get("total_data_size_gb", 0) * (1024**3))
            total_index_size = total_index_size or int(prev.get("total_index_size_gb", 0) * (1024**3))
            # Preserve top_tables from previous cache when current collection failed
            if not top_tables:
                prev_cache_path = os.path.join(config.CACHE_DIR, f"{inst}.json.prev")
                if not os.path.exists(prev_cache_path):
                    prev_cache_path = None
                if prev_cache_path:
                    try:
                        with open(prev_cache_path) as _f:
                            _prev_cd = json.load(_f)
                        _prev_ti = extract_table_info(_prev_cd)
                        if _prev_ti:
                            top_tables = sorted(_prev_ti, key=lambda t: t.get("rows", 0) or 0, reverse=True)[:20]
                    except Exception:
                        pass

        row = {
            "rank": rank,
            "instance": inst,
            "company": cmap.get(inst, ""),
            "score": round(score, 6),
            "coverage": coverage,
            # CSV data (fill tier from ruckus if CSV is missing)
            "txn_90d": csv_d.get("txn_90d", 0),
            "db_gb_csv": csv_d.get("db_gb", 0),
            "primary_table_gb": csv_d.get("primary_table_gb", 0),
            "capacity_tier": csv_d.get("capacity_tier") or extract_capacity_tier(cd),
            "db_type": csv_d.get("db_type", ""),
            "release_patch": csv_d.get("release_patch", ""),
            "release_family": csv_d.get("release_family", ""),
            # Ruckus-collected DB info
            "db_total_size_gb": round(db_info["total_size_gb"], 2) if has_ruckus_data else round(prev.get("db_total_size_gb", 0), 2),
            "db_count": len(db_info["databases"]) if has_ruckus_data else (prev.get("db_count", 0) if prev else 0),
            # Ruckus-collected table info
            "table_count": table_count,
            "total_table_rows": total_table_rows,
            "total_data_size_gb": round(total_data_size / (1024**3), 2) if total_data_size > 0 else 0,
            "total_index_size_gb": round(total_index_size / (1024**3), 2) if total_index_size > 0 else 0,
            "top_tables": top_tables,
            # Ruckus-collected node info
            "node_count": node_info["node_count"] if has_ruckus_data else prev.get("node_count", 0),
            "nodes": node_info["nodes"],
            "app_server_count": len(node_info["app_servers"]),
            # Scoring dimensions (from audit data if available)
            "dims": dims,
            # Collection status
            "has_ruckus_data": has_ruckus_data,
            "used_fallback": not has_ruckus_data and bool(prev),
            "collection_errors": cd.get("errors", []),
        }
        rows.append(row)

    # Compute deltas against previous aggregator run
    deltas = compute_aggregator_deltas(rows)
    for row in rows:
        d = deltas.get(row["instance"], {})
        row["d_total_table_rows"] = d.get("d_total_table_rows", None)
        row["d_table_count"] = d.get("d_table_count", None)
        row["d_db_total_size_gb"] = d.get("d_db_total_size_gb", None)
        row["d_node_count"] = d.get("d_node_count", None)
        row["d_txn_90d"] = d.get("d_txn_90d", None)
        row["d_db_gb_csv"] = d.get("d_db_gb_csv", None)
        row["d_score"] = d.get("d_score", None)
        row["has_delta"] = len(d) > 0

    return rows


# ── HTML Report ──────────────────────────────────────────────────────────────

def _generate_html(rows: List[dict], generated_at: str) -> str:
    """Generate the full interactive HTML dashboard."""

    # Pre-compute aggregates for summary cards
    total_instances = len(rows)
    total_rows_all = sum(r["total_table_rows"] for r in rows)
    total_db_size = sum(r["db_gb_csv"] for r in rows)
    total_db_size_display = (
        f"{total_db_size / 1024:,.1f} TB" if total_db_size >= 1024
        else f"{total_db_size:,.1f} GB"
    )
    avg_nodes = (
        sum(r["node_count"] for r in rows if r["node_count"] > 0)
        / max(1, sum(1 for r in rows if r["node_count"] > 0))
    )
    total_tables = sum(r["table_count"] for r in rows)
    instances_with_data = sum(1 for r in rows if r["has_ruckus_data"])
    instances_with_delta = sum(1 for r in rows if r.get("has_delta"))
    total_row_delta = sum(r.get("d_total_table_rows") or 0 for r in rows if r.get("has_delta"))

    # Capacity tier distribution
    tier_counts = {}
    for r in rows:
        t = r["capacity_tier"] or "unknown"
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # DB type distribution
    dbtype_counts = {}
    for r in rows:
        t = r["db_type"] or "unknown"
        dbtype_counts[t] = dbtype_counts.get(t, 0) + 1

    # Serialize row data for JavaScript — only include fields used by the dashboard
    _JS_KEYS = {
        "rank", "instance", "company", "score", "coverage",
        "txn_90d", "db_gb_csv", "primary_table_gb", "capacity_tier",
        "db_type", "release_family",
        "db_total_size_gb", "db_count",
        "table_count", "total_table_rows",
        "total_data_size_gb", "total_index_size_gb",
        "node_count", "app_server_count",
        "has_ruckus_data",
        "d_total_table_rows", "d_table_count", "d_db_total_size_gb",
        "d_node_count", "d_txn_90d", "d_db_gb_csv", "d_score",
        "has_delta",
    }
    slim_rows = []
    top_tables_map = {}  # rank -> top_tables (separate from ALL_ROWS to cut payload)
    _MAX_TT_ROWS = 500  # only ship top_tables for first 500 by rank
    for r in rows:
        sr = {k: r[k] for k in _JS_KEYS if k in r}
        slim_rows.append(sr)
        if r.get("top_tables") and r["rank"] <= _MAX_TT_ROWS:
            top_tables_map[r["rank"]] = [
                {"name": t["name"], "rows": t.get("rows", 0),
                 "data_length": t.get("data_length", 0),
                 "index_length": t.get("index_length", 0)}
                for t in r["top_tables"][:20]
            ]
    # Escape for embedding in JS string literal (single quotes)
    def _js_str(obj):
        s = json.dumps(obj, default=str, separators=(',', ':'))
        return s.replace('\\', '\\\\').replace("'", "\\'").replace('</script', '<\\/script')
    js_rows_str = _js_str(slim_rows)
    js_top_tables_str = _js_str(top_tables_map)
    tier_json = json.dumps(tier_counts)
    dbtype_json = json.dumps(dbtype_counts)

    # Top 20 by DB size for chart
    top_by_db = sorted(rows, key=lambda r: r["db_gb_csv"], reverse=True)[:20]
    chart_labels = json.dumps([r["instance"] for r in top_by_db])
    chart_db_sizes = json.dumps([round(r["db_gb_csv"], 1) for r in top_by_db])
    chart_txn = json.dumps([round(r["txn_90d"], 0) for r in top_by_db])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ruckus Aggregator Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #f8faf8; color: #1e293b; }}
  .glass {{ background: #ffffff; border: 1px solid #d1e7d1; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
  .card {{ transition: transform 0.2s, box-shadow 0.2s; }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
  table {{ border-collapse: collapse; }}
  th {{ cursor: pointer; user-select: none; position: sticky; top: 0; z-index: 10; }}
  th:hover {{ background: #dcfce7; }}
  .sort-asc::after {{ content: ' \\25B2'; font-size: 0.7em; }}
  .sort-desc::after {{ content: ' \\25BC'; font-size: 0.7em; }}
  tr:hover td {{ background: #f0fdf4; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }}
  .badge-green {{ background: #dcfce7; color: #15803d; }}
  .badge-blue {{ background: #dbeafe; color: #1d4ed8; }}
  .badge-yellow {{ background: #fef9c3; color: #a16207; }}
  .badge-red {{ background: #fee2e2; color: #dc2626; }}
  .search-input {{ background: #ffffff; border: 1px solid #bbf7d0; color: #1e293b; }}
  .search-input:focus {{ border-color: #22c55e; outline: none; box-shadow: 0 0 0 3px rgba(34,197,94,0.2); }}
  .tab-btn {{ padding: 8px 20px; border-radius: 8px 8px 0 0; cursor: pointer; transition: all 0.2s; }}
  .tab-btn.active {{ background: #dcfce7; color: #15803d; border-bottom: 2px solid #22c55e; font-weight: 600; }}
  .tab-btn:not(.active) {{ background: transparent; color: #64748b; }}
  .tab-btn:not(.active):hover {{ background: #f0fdf4; }}
  .expand-btn {{ cursor: pointer; padding: 2px 6px; border-radius: 4px; background: #dcfce7; color: #15803d; font-size: 0.75rem; }}
  .expand-btn:hover {{ background: #bbf7d0; }}
  .detail-row {{ display: none; }}
  .detail-row.open {{ display: table-row; }}
  #mainTable {{ max-height: 70vh; overflow-y: auto; }}
  .instance-link {{ color: #15803d; cursor: pointer; font-weight: 600; text-decoration: none; }}
  .instance-link:hover {{ color: #166534; text-decoration: underline; }}
  .analyze-modal {{ position: fixed; inset: 0; z-index: 100; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.4); }}
  .analyze-modal-box {{ background: #fff; border-radius: 16px; padding: 24px; min-width: 360px; box-shadow: 0 20px 60px rgba(0,0,0,0.2); }}
  .filter-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 16px; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; }}
  .filter-chip {{ display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 9999px; background: #dcfce7; color: #15803d; font-size: 0.75rem; font-weight: 600; }}
  .filter-chip button {{ background: none; border: none; color: #dc2626; cursor: pointer; font-size: 0.85rem; padding: 0 2px; line-height: 1; }}
  .filter-select, .filter-input {{ font-size: 0.8rem; padding: 4px 8px; border: 1px solid #bbf7d0; border-radius: 6px; background: #fff; color: #1e293b; }}
  .filter-select:focus, .filter-input:focus {{ border-color: #22c55e; outline: none; box-shadow: 0 0 0 2px rgba(34,197,94,0.2); }}
  .filter-add-btn {{ font-size: 0.8rem; padding: 4px 12px; border-radius: 6px; background: #22c55e; color: #fff; border: none; cursor: pointer; font-weight: 600; }}
  .filter-add-btn:hover {{ background: #16a34a; }}
  .filter-clear-btn {{ font-size: 0.75rem; padding: 4px 10px; border-radius: 6px; background: #fee2e2; color: #dc2626; border: none; cursor: pointer; font-weight: 600; }}
  .filter-clear-btn:hover {{ background: #fecaca; }}
  .ctx-menu {{ position: fixed; z-index: 200; background: #fff; border: 1px solid #d1e7d1; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.15); min-width: 180px; padding: 4px 0; font-size: 13px; }}
  .ctx-menu-item {{ padding: 6px 14px; cursor: pointer; display: flex; align-items: center; gap: 8px; }}
  .ctx-menu-item:hover {{ background: #f0fdf4; }}
  .ctx-menu-sep {{ height: 1px; background: #d1e7d1; margin: 4px 0; }}
  td {{ cursor: context-menu; }}
  /* Expandable top-tables detail row */
  .top-tbl-toggle {{ cursor: pointer; color: #16a34a; font-weight: 600; white-space: nowrap; }}
  .top-tbl-toggle:hover {{ text-decoration: underline; }}
  .detail-row {{ display: none; }}
  .detail-row.open {{ display: table-row; }}
  .top-tbl-grid {{ font-size: 0.75rem; }}
  .top-tbl-grid table {{ width: 100%; border-collapse: collapse; }}
  .top-tbl-grid th {{ text-align: left; padding: 3px 8px; font-weight: 600; color: #475569; border-bottom: 1px solid #d1e7d1; font-size: 0.7rem; text-transform: uppercase; }}
  .top-tbl-grid td {{ padding: 3px 8px; border-bottom: 1px solid #f0fdf4; }}
  .top-tbl-grid tr:hover td {{ background: #f0fdf4; }}
  .sub-sort-hdr {{ cursor: pointer; user-select: none; }}
  .sub-sort-hdr:hover {{ background: #dcfce7; }}
  /* Per-column search row */
  .col-search-row {{ display: none; }}
  .col-search-row.visible {{ display: table-row; }}
  .col-search-row input {{ width: 100%; font-size: 0.75rem; padding: 3px 6px; border: 1px solid #d1e7d1; border-radius: 4px; background: #fff; color: #1e293b; box-sizing: border-box; }}
  .col-search-row input:focus {{ border-color: #22c55e; outline: none; box-shadow: 0 0 0 2px rgba(34,197,94,0.15); }}
  .col-search-row td {{ padding: 4px 3px; background: #f0fdf4; }}
  .col-search-toggle {{ display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 4px; border: 1px solid #d1e7d1; background: #fff; cursor: pointer; font-size: 12px; color: #64748b; transition: all 0.15s; vertical-align: middle; }}
  .col-search-toggle:hover {{ background: #dcfce7; color: #16a34a; }}
  .col-search-toggle.active {{ background: #dcfce7; border-color: #22c55e; color: #16a34a; }}
  /* Header context menu */
  .hdr-ctx-menu {{ position: fixed; z-index: 300; background: #fff; border: 1px solid #d1e7d1; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.15); min-width: 170px; padding: 4px 0; font-size: 13px; }}
  .hdr-ctx-menu-item {{ padding: 7px 14px; cursor: pointer; display: flex; align-items: center; gap: 8px; }}
  .hdr-ctx-menu-item:hover {{ background: #f0fdf4; }}
</style>
</head>
<body class="min-h-screen">

<!-- Header -->
<header class="glass border-b border-green-200 px-6 py-4 sticky top-0 z-50" style="background:#ffffff;">
  <div class="max-w-[1800px] mx-auto flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-bold text-green-700">
        Ruckus Aggregator
      </h1>
      <p class="text-sm text-slate-500">Top {total_instances} instances by complexity — {generated_at}</p>
    </div>
    <div class="flex items-center gap-4">
      <input id="globalSearch" type="text" placeholder="Search by instance or company..."
             class="search-input rounded-lg px-4 py-2 text-sm w-72" />
      <a href="/collect" class="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium transition inline-block">
        Collect Data
      </a>
      <a href="/analysis" class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition inline-block">
        Customer Analysis
      </a>
      <button onclick="exportCSV()" class="px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition">
        Export CSV
      </button>
      <button onclick="switchTab('rap')" class="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition">
        RAP
      </button>
    </div>
  </div>
</header>

<main class="max-w-[1800px] mx-auto px-6 py-6 space-y-6">

<!-- Summary Cards -->
<div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Instances</p>
    <p class="text-2xl font-bold text-green-700 mt-1">{total_instances:,}</p>
    <p class="text-xs text-slate-400 mt-1">{instances_with_data} with live data</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Total DB Size</p>
    <p class="text-2xl font-bold text-green-600 mt-1">{total_db_size_display}</p>
    <p class="text-xs text-slate-400 mt-1">Combined across all</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Rows (Top Tables)</p>
    <p class="text-2xl font-bold text-blue-600 mt-1">{total_rows_all:,}</p>
    <p class="text-xs text-slate-400 mt-1">Top ~50 unique tables per DB</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Avg Nodes</p>
    <p class="text-2xl font-bold text-purple-600 mt-1">{avg_nodes:.1f}</p>
    <p class="text-xs text-slate-400 mt-1">Per instance</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Top Score</p>
    <p class="text-2xl font-bold text-amber-600 mt-1">{rows[0]["score"]:.4f}</p>
    <p class="text-xs text-slate-400 mt-1">{rows[0]["instance"]}</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Median Score</p>
    <p class="text-2xl font-bold text-teal-600 mt-1">{rows[len(rows)//2]["score"]:.4f}</p>
    <p class="text-xs text-slate-400 mt-1">Rank #{len(rows)//2}</p>
  </div>
  <div class="glass rounded-xl p-4 card">
    <p class="text-xs text-slate-500 uppercase tracking-wide">Row Delta</p>
    <p class="text-2xl font-bold {'text-red-600' if total_row_delta > 0 else 'text-green-600' if total_row_delta < 0 else 'text-slate-400'} mt-1">{'+' if total_row_delta > 0 else ''}{total_row_delta:,}</p>
    <p class="text-xs text-slate-400 mt-1">{instances_with_delta} with prior run</p>
  </div>
</div>

<!-- Charts Row -->
<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
  <div class="glass rounded-xl p-5 lg:col-span-2">
    <h3 class="text-sm font-semibold text-slate-600 mb-3">Top 20 by DB Size (GB) &amp; Txn/Day</h3>
    <canvas id="dbChart" height="200"></canvas>
  </div>
  <div class="glass rounded-xl p-5 space-y-4">
    <div>
      <h3 class="text-sm font-semibold text-slate-600 mb-3">Capacity Tier Distribution</h3>
      <canvas id="tierChart" height="140"></canvas>
    </div>
    <div>
      <h3 class="text-sm font-semibold text-slate-600 mb-3">DB Type Distribution</h3>
      <canvas id="dbtypeChart" height="140"></canvas>
    </div>
  </div>
</div>

<!-- Tabs -->
<div class="flex gap-1 border-b border-green-200">
  <div class="tab-btn active" onclick="switchTab('overview')">Overview</div>
  <div class="tab-btn" onclick="switchTab('databases')">Databases</div>
  <div class="tab-btn" onclick="switchTab('tables')">Tables</div>
  <div class="tab-btn" onclick="switchTab('nodes')">Nodes</div>
  <div class="tab-btn" onclick="switchTab('scoring')">Scoring</div>
</div>

<!-- Filter Bar -->
<div class="filter-bar" id="filterBar">
  <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Filter:</span>
  <select id="filterCol" class="filter-select">
    <option value="node_count">Nodes</option>
    <option value="score">Score</option>
    <option value="txn_90d">Txn/Day (90d)</option>
    <option value="db_gb_csv">DB Size (GB)</option>
    <option value="table_count">Tables</option>
    <option value="total_table_rows">Total Rows</option>
    <option value="db_total_size_gb">DB Size (Live)</option>
    <option value="db_count">DB Count</option>
    <option value="total_data_size_gb">Data GB</option>
    <option value="total_index_size_gb">Index GB</option>
    <option value="app_server_count">App Servers</option>
    <option value="coverage">Coverage</option>
    <option value="rank">Rank</option>
    <option value="capacity_tier" data-type="str">Tier</option>
    <option value="db_type" data-type="str">DB Type</option>
    <option value="release_family" data-type="str">Release</option>
    <option value="instance" data-type="str">Instance</option>
    <option value="company" data-type="str">Company</option>
    <option value="d_total_table_rows">Rows \u0394</option>
    <option value="d_table_count">Tables \u0394</option>
    <option value="d_db_gb_csv">DB GB \u0394</option>
    <option value="d_db_total_size_gb">Live DB \u0394</option>
  </select>
  <select id="filterOp" class="filter-select">
    <option value=">">&gt;</option>
    <option value=">=">&ge;</option>
    <option value="<">&lt;</option>
    <option value="<=">&le;</option>
    <option value="=">=</option>
    <option value="!=">!=</option>
    <option value="contains">contains</option>
  </select>
  <input id="filterVal" class="filter-input" type="text" placeholder="value" style="width:120px"
         onkeydown="if(event.key==='Enter')addFilter()" />
  <button class="filter-add-btn" onclick="addFilter()">+ Add</button>
  <div id="filterChips" style="display:inline-flex;flex-wrap:wrap;gap:6px;"></div>
  <button class="filter-clear-btn" id="filterClearBtn" onclick="clearFilters()" style="display:none">Clear All</button>
</div>

<!-- Main Table -->
<div id="mainTable" class="glass rounded-xl overflow-auto">
<table class="w-full text-sm" id="dataTable">
<thead id="tableHead" class="bg-green-50 text-slate-700">
</thead>
<tbody id="tableBody" class="text-slate-700">
</tbody>
</table>
</div>

<!-- Instance Count -->
<div id="instanceCount" class="text-center text-sm text-slate-500 py-2">
  Showing <span id="visibleCount">{total_instances}</span> of {total_instances} instances
</div>

<!-- RAP Panel (hidden by default) -->
<div id="rapPanel" style="display:none">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div class="glass rounded-xl p-4 card">
      <p class="text-xs text-slate-500 uppercase tracking-wide">Session Clicks</p>
      <p class="text-2xl font-bold text-indigo-600 mt-1" id="rapSessionClicks">0</p>
      <p class="text-xs text-slate-400 mt-1">This session</p>
    </div>
    <div class="glass rounded-xl p-4 card">
      <p class="text-xs text-slate-500 uppercase tracking-wide">Avg Render</p>
      <p class="text-2xl font-bold text-indigo-600 mt-1" id="rapAvgRender">0 ms</p>
      <p class="text-xs text-slate-400 mt-1">This session</p>
    </div>
    <div class="glass rounded-xl p-4 card">
      <p class="text-xs text-slate-500 uppercase tracking-wide">Max Render</p>
      <p class="text-2xl font-bold text-amber-600 mt-1" id="rapMaxRender">0 ms</p>
      <p class="text-xs text-slate-400 mt-1">This session</p>
    </div>
    <div class="glass rounded-xl p-4 card">
      <p class="text-xs text-slate-500 uppercase tracking-wide">Total Sessions</p>
      <p class="text-2xl font-bold text-green-600 mt-1" id="rapTotalSessions">0</p>
      <p class="text-xs text-slate-400 mt-1">All time</p>
    </div>
  </div>
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
    <div class="glass rounded-xl p-5">
      <h3 class="text-sm font-semibold text-slate-600 mb-3">Render Times (this session)</h3>
      <canvas id="rapRenderChart" height="200"></canvas>
    </div>
    <div class="glass rounded-xl p-5">
      <h3 class="text-sm font-semibold text-slate-600 mb-3">Clicks Per Minute (this session)</h3>
      <canvas id="rapClickChart" height="200"></canvas>
    </div>
  </div>
  <div class="glass rounded-xl overflow-auto">
    <h3 class="text-sm font-semibold text-slate-600 px-5 pt-4 pb-2">Session History</h3>
    <table class="w-full text-sm">
      <thead class="bg-indigo-50 text-slate-700">
        <tr>
          <th class="px-3 py-2 text-left text-xs font-medium uppercase">#</th>
          <th class="px-3 py-2 text-left text-xs font-medium uppercase">Date</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Duration</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Clicks</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Renders</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Avg Render</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Max Render</th>
          <th class="px-3 py-2 text-right text-xs font-medium uppercase">Tabs Visited</th>
        </tr>
      </thead>
      <tbody id="rapSessionTable" class="text-slate-700"></tbody>
    </table>
  </div>
  <div class="text-right mt-2">
    <button onclick="clearRapHistory()" class="text-xs text-red-500 hover:text-red-700 underline">Clear History</button>
  </div>
</div>

</main>

<!-- Right-click context menu (cell) -->
<div id="ctxMenu" class="ctx-menu" style="display:none">
  <div class="ctx-menu-item" onclick="ctxApply('=')"><span style="color:#16a34a">&#9654;</span> Show Matching</div>
  <div class="ctx-menu-item" onclick="ctxApply('!=')"><span style="color:#dc2626">&#10006;</span> Filter Out</div>
  <div class="ctx-menu-sep"></div>
  <div class="ctx-menu-item" onclick="ctxApply('>')"><span style="color:#2563eb">&gt;</span> Greater Than</div>
  <div class="ctx-menu-item" onclick="ctxApply('<')"><span style="color:#ca8a04">&lt;</span> Less Than</div>
</div>

<!-- Right-click context menu (header) -->
<div id="hdrCtxMenu" class="hdr-ctx-menu" style="display:none">
  <div class="hdr-ctx-menu-item" onclick="hdrCtxSort('asc')"><span style="color:#16a34a">&#9650;</span> Sort (a to z)</div>
  <div class="hdr-ctx-menu-item" onclick="hdrCtxSort('desc')"><span style="color:#dc2626">&#9660;</span> Sort (z to a)</div>
</div>

<script>
const ALL_ROWS = JSON.parse('{js_rows_str}');
const TOP_TABLES = JSON.parse('{js_top_tables_str}');
const TIER_DATA = {tier_json};
const DBTYPE_DATA = {dbtype_json};

let currentTab = 'overview';
let sortCol = 'rank';
let sortDir = 'asc';
let searchTerm = '';
let activeFilters = [];
let colSearchVisible = false;
let colSearchTerms = {{}};  // key -> search string
const MAX_RENDER_ROWS = 500;  // cap DOM rows for performance

function debounce(fn, ms) {{
  let tid;
  return function(...args) {{ clearTimeout(tid); tid = setTimeout(() => fn.apply(this, args), ms); }};
}}

// ── Column filters ──────────────────────────────────────────────────────────
function getFilterColLabel(key) {{
  const sel = document.getElementById('filterCol');
  for (const opt of sel.options) {{ if (opt.value === key) return opt.text; }}
  return key;
}}

function isStrCol(key) {{
  const sel = document.getElementById('filterCol');
  for (const opt of sel.options) {{ if (opt.value === key) return opt.dataset.type === 'str'; }}
  return false;
}}

function addFilter() {{
  const col = document.getElementById('filterCol').value;
  const op = document.getElementById('filterOp').value;
  const raw = document.getElementById('filterVal').value.trim();
  if (!raw) return;
  const strMode = isStrCol(col) || op === 'contains';
  const val = strMode ? raw : Number(raw);
  if (!strMode && isNaN(val)) {{ alert('Enter a numeric value for this column'); return; }}
  activeFilters.push({{ col, op, val, label: getFilterColLabel(col) }});
  document.getElementById('filterVal').value = '';
  renderChips();
  renderTable();
}}

function removeFilter(idx) {{
  activeFilters.splice(idx, 1);
  renderChips();
  renderTable();
}}

function clearFilters() {{
  activeFilters = [];
  renderChips();
  renderTable();
}}

function renderChips() {{
  const box = document.getElementById('filterChips');
  const clearBtn = document.getElementById('filterClearBtn');
  if (!activeFilters.length) {{ box.innerHTML = ''; clearBtn.style.display = 'none'; return; }}
  clearBtn.style.display = '';
  box.innerHTML = activeFilters.map((f, i) =>
    `<span class="filter-chip">${{f.label}} ${{f.op}} ${{f.val}}<button onclick="removeFilter(${{i}})">&times;</button></span>`
  ).join('');
}}

function applyFilters(rows) {{
  if (!activeFilters.length) return rows;
  return rows.filter(r => {{
    return activeFilters.every(f => {{
      let v = r[f.col];
      if (v === null || v === undefined) return false;
      if (typeof f.val === 'string') {{
        const sv = String(v).toLowerCase();
        const fv = f.val.toLowerCase();
        switch (f.op) {{
          case 'contains': return sv.includes(fv);
          case '=': return sv === fv;
          case '!=': return sv !== fv;
          default: return sv.includes(fv);
        }}
      }} else {{
        const nv = Number(v);
        if (isNaN(nv)) return false;
        switch (f.op) {{
          case '>': return nv > f.val;
          case '>=': return nv >= f.val;
          case '<': return nv < f.val;
          case '<=': return nv <= f.val;
          case '=': return nv === f.val;
          case '!=': return nv !== f.val;
          default: return true;
        }}
      }}
    }});
  }});
}}

// Tab definitions
const TABS = {{
  overview: [
    {{key:'rank', label:'#', w:'w-12', fmt:'num'}},
    {{key:'instance', label:'Instance', w:'w-48', fmt:'instance'}},
    {{key:'company', label:'Company', w:'w-48', fmt:'str'}},
    {{key:'score', label:'Score', w:'w-20', fmt:'score'}},
    {{key:'txn_90d', label:'Txn/Day (90d)', w:'w-28', fmt:'num'}},
    {{key:'db_gb_csv', label:'DB Size', w:'w-24', fmt:'size'}},
    {{key:'node_count', label:'Nodes', w:'w-16', fmt:'num'}},
    {{key:'table_count', label:'Top Tables', w:'w-16', fmt:'top_tbl_link'}},
    {{key:'d_table_count', label:'Tables \u0394', w:'w-20', fmt:'delta'}},
    {{key:'capacity_tier', label:'Tier', w:'w-28', fmt:'tier'}},
    {{key:'db_type', label:'DB Type', w:'w-20', fmt:'str'}},
    {{key:'has_ruckus_data', label:'Live', w:'w-12', fmt:'bool'}},
  ],
  databases: [
    {{key:'rank', label:'#', w:'w-12', fmt:'num'}},
    {{key:'instance', label:'Instance', w:'w-48', fmt:'instance'}},
    {{key:'company', label:'Company', w:'w-48', fmt:'str'}},
    {{key:'db_gb_csv', label:'Combined (CSV)', w:'w-28', fmt:'size'}},
    {{key:'d_db_gb_csv', label:'DB \u0394', w:'w-20', fmt:'delta_dec'}},
    {{key:'primary_table_gb', label:'Primary', w:'w-24', fmt:'size'}},
    {{key:'db_total_size_gb', label:'DB Size (Live)', w:'w-24', fmt:'size'}},
    {{key:'d_db_total_size_gb', label:'Live \u0394', w:'w-20', fmt:'delta_dec'}},
    {{key:'db_count', label:'DB Count', w:'w-16', fmt:'num'}},
    {{key:'total_data_size_gb', label:'Data Size', w:'w-20', fmt:'size'}},
    {{key:'total_index_size_gb', label:'Index Size', w:'w-20', fmt:'size'}},
    {{key:'capacity_tier', label:'Tier', w:'w-28', fmt:'tier'}},
    {{key:'db_type', label:'DB Type', w:'w-20', fmt:'str'}},
  ],
  tables: [
    {{key:'rank', label:'#', w:'w-12', fmt:'num'}},
    {{key:'instance', label:'Instance', w:'w-48', fmt:'instance'}},
    {{key:'company', label:'Company', w:'w-48', fmt:'str'}},
    {{key:'table_count', label:'Top Tables', w:'w-16', fmt:'num'}},
    {{key:'total_table_rows', label:'Rows (Top 100)', w:'w-28', fmt:'num'}},
    {{key:'total_data_size_gb', label:'Data Size', w:'w-24', fmt:'size'}},
    {{key:'total_index_size_gb', label:'Index Size', w:'w-24', fmt:'size'}},
    {{key:'db_gb_csv', label:'Combined', w:'w-24', fmt:'size'}},
  ],
  nodes: [
    {{key:'rank', label:'#', w:'w-12', fmt:'num'}},
    {{key:'instance', label:'Instance', w:'w-48', fmt:'instance'}},
    {{key:'company', label:'Company', w:'w-48', fmt:'str'}},
    {{key:'node_count', label:'Nodes', w:'w-16', fmt:'num'}},
    {{key:'app_server_count', label:'App Servers', w:'w-20', fmt:'num'}},
    {{key:'capacity_tier', label:'Tier', w:'w-28', fmt:'tier'}},
    {{key:'txn_90d', label:'Txn/Day', w:'w-24', fmt:'num'}},
    {{key:'db_gb_csv', label:'DB Size', w:'w-24', fmt:'size'}},
  ],
  scoring: [
    {{key:'rank', label:'#', w:'w-12', fmt:'num'}},
    {{key:'instance', label:'Instance', w:'w-48', fmt:'instance'}},
    {{key:'company', label:'Company', w:'w-48', fmt:'str'}},
    {{key:'score', label:'Score', w:'w-20', fmt:'score'}},
    {{key:'coverage', label:'Coverage', w:'w-16', fmt:'num'}},
    {{key:'txn_90d', label:'Txn/Day', w:'w-24', fmt:'num'}},
    {{key:'db_gb_csv', label:'DB Size', w:'w-20', fmt:'size'}},
    {{key:'capacity_tier', label:'Tier', w:'w-24', fmt:'tier'}},
  ],
}};

function fmtSizeGB(gb) {{
  if (gb === null || gb === undefined || gb === 0) return '0 GB';
  const n = Number(gb);
  if (Math.abs(n) >= 1024) return (n / 1024).toLocaleString(undefined, {{minimumFractionDigits:1, maximumFractionDigits:1}}) + ' TB';
  return n.toLocaleString(undefined, {{minimumFractionDigits:1, maximumFractionDigits:1}}) + ' GB';
}}

function fmtSizeBytes(bytes) {{
  if (!bytes) return '0 GB';
  const gb = bytes / (1024*1024*1024);
  if (gb >= 1024) return (gb / 1024).toLocaleString(undefined, {{minimumFractionDigits:1, maximumFractionDigits:1}}) + ' TB';
  return gb.toLocaleString(undefined, {{minimumFractionDigits:1, maximumFractionDigits:1}}) + ' GB';
}}

function fmt(val, type) {{
  if (val === null || val === undefined || val === '') return '<span class="text-slate-400">—</span>';
  switch(type) {{
    case 'num': return Number(val).toLocaleString();
    case 'dec1': return Number(val).toLocaleString(undefined, {{minimumFractionDigits:1, maximumFractionDigits:1}});
    case 'dec2': return Number(val).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
    case 'size': return fmtSizeGB(val);
    case 'score': return Number(val).toFixed(4);
    case 'tier':
      const t = String(val).replace('postgresdb-','').replace('mysqldb-','');
      const colors = {{small:'badge-blue',medium:'badge-green',large:'badge-yellow',xlarge:'badge-red',yotta:'badge-red',zetta:'badge-red'}};
      return `<span class="badge ${{colors[t]||'badge-blue'}}">${{t||'—'}}</span>`;
    case 'instance':
      return `<span class="instance-link" onclick="showAnalyzeModal('${{String(val)}}')">${{String(val)}}</span>`;
    case 'delta': {{
      if (val === null || val === undefined) return '<span class="text-slate-300">&mdash;</span>';
      const n = Number(val);
      if (n === 0) return '<span class="text-slate-400">0</span>';
      const cls = n > 0 ? 'text-red-600' : 'text-green-600';
      const sign = n > 0 ? '+' : '';
      return `<span class="font-semibold ${{cls}}">${{sign}}${{n.toLocaleString()}}</span>`;
    }}
    case 'delta_dec': {{
      if (val === null || val === undefined) return '<span class="text-slate-300">&mdash;</span>';
      const n = Number(val);
      if (Math.abs(n) < 0.05) return '<span class="text-slate-400">0.0</span>';
      const cls = n > 0 ? 'text-red-600' : 'text-green-600';
      const sign = n > 0 ? '+' : '';
      return `<span class="font-semibold ${{cls}}">${{sign}}${{n.toFixed(1)}}</span>`;
    }}
    case 'bool':
      return val ? '<span class="badge badge-green">Yes</span>' : '<span class="badge badge-red">No</span>';
    case 'top_tbl_link':
      return `<span class="top-tbl-toggle">${{Number(val||0).toLocaleString()}} &#9662;</span>`;
    default: return String(val);
  }}
}}

function renderTable() {{
  const _renderStart = performance.now();
  const cols = TABS[currentTab];
  let filtered = ALL_ROWS.filter(r => !searchTerm || r.instance.toLowerCase().includes(searchTerm) || (r.company && r.company.toLowerCase().includes(searchTerm)));
  filtered = applyFilters(filtered);
  // Apply per-column search filters
  for (const [key, term] of Object.entries(colSearchTerms)) {{
    if (!term) continue;
    const lterm = term.toLowerCase();
    filtered = filtered.filter(r => {{
      const v = r[key];
      if (v === null || v === undefined) return false;
      return String(v).toLowerCase().includes(lterm);
    }});
  }}
  filtered.sort((a,b) => {{
    let va = a[sortCol], vb = b[sortCol];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va == null) va = sortDir === 'asc' ? Infinity : -Infinity;
    if (vb == null) vb = sortDir === 'asc' ? Infinity : -Infinity;
    return sortDir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  }});

  // Header
  let hh = '<tr>';
  cols.forEach((c, ci) => {{
    const cls = sortCol === c.key ? (sortDir === 'asc' ? 'sort-asc' : 'sort-desc') : '';
    const searchBtn = ci === 0 ? `<span id="colSearchToggle" class="col-search-toggle ${{colSearchVisible ? 'active' : ''}}" onclick="event.stopPropagation();toggleColSearch()" title="Toggle column search">&#128269;</span> ` : '';
    hh += `<th class="px-3 py-3 text-left text-xs font-medium uppercase tracking-wider ${{c.w}} ${{cls}}" data-col="${{c.key}}">${{searchBtn}}${{c.label}}</th>`;
  }});
  hh += '</tr>';
  // Column search row
  hh += `<tr class="col-search-row ${{colSearchVisible ? 'visible' : ''}}">`;
  cols.forEach(c => {{
    const val = colSearchTerms[c.key] || '';
    hh += `<td class="${{c.w}}"><input type="text" placeholder="Search" data-search-col="${{c.key}}" value="${{val.replace(/"/g,'&quot;')}}" /></td>`;
  }});
  hh += '</tr>';
  document.getElementById('tableHead').innerHTML = hh;

  // Body (cap rows for DOM performance)
  const totalFiltered = filtered.length;
  const renderSlice = filtered.slice(0, MAX_RENDER_ROWS);
  let bb = '';
  renderSlice.forEach(r => {{
    bb += '<tr class="border-b border-green-100">';
    cols.forEach((c, ci) => {{
      bb += `<td class="px-3 py-2 ${{c.w}}" data-col="${{ci}}">${{fmt(r[c.key], c.fmt)}}</td>`;
    }});
    bb += '</tr>';

    // Expandable top-20 tables detail row
    const _tt = TOP_TABLES[r.rank];
    if ((currentTab === 'overview' || currentTab === 'tables') && _tt && _tt.length > 0) {{
      bb += `<tr class="detail-row" id="detail-${{r.rank}}"><td colspan="${{cols.length}}" class="px-4 py-3 bg-green-50">`;
      bb += `<div class="top-tbl-grid" data-rank="${{r.rank}}"><table>`;
      bb += '<thead><tr>';
      bb += `<th style="width:30px">#</th>`;
      bb += `<th class="sub-sort-hdr" data-sub-col="name" data-sub-rank="${{r.rank}}">Table Name</th>`;
      bb += `<th class="sub-sort-hdr" style="text-align:right" data-sub-col="rows" data-sub-rank="${{r.rank}}">Rows &#9660;</th>`;
      bb += `<th class="sub-sort-hdr" style="text-align:right" data-sub-col="data_length" data-sub-rank="${{r.rank}}">Data</th>`;
      bb += `<th class="sub-sort-hdr" style="text-align:right" data-sub-col="index_length" data-sub-rank="${{r.rank}}">Index</th>`;
      bb += `<th class="sub-sort-hdr" style="text-align:right" data-sub-col="total_size" data-sub-rank="${{r.rank}}">Total</th>`;
      bb += '</tr></thead><tbody class="sub-tbl-body">';
      _tt.slice(0,20).forEach((t, i) => {{
        bb += `<tr><td class="text-slate-400">${{i+1}}</td><td class="text-slate-700 font-medium">${{t.name}}</td><td style="text-align:right">${{Number(t.rows||0).toLocaleString()}}</td><td style="text-align:right">${{fmtSizeBytes(t.data_length||0)}}</td><td style="text-align:right">${{fmtSizeBytes(t.index_length||0)}}</td><td style="text-align:right;font-weight:600">${{fmtSizeBytes((t.data_length||0)+(t.index_length||0))}}</td></tr>`;
      }});
      bb += '</tbody></table></div></td></tr>';
    }}
  }});
  if (totalFiltered > MAX_RENDER_ROWS) {{
    bb += `<tr><td colspan="${{cols.length}}" class="px-4 py-3 text-center text-sm text-slate-500 bg-amber-50">Showing first ${{MAX_RENDER_ROWS}} of ${{totalFiltered.toLocaleString()}} matching rows. Use search or filters to narrow results.</td></tr>`;
  }}
  document.getElementById('tableBody').innerHTML = bb;
  document.getElementById('visibleCount').textContent = totalFiltered;

  // Re-attach sort handlers (left-click on header)
  document.querySelectorAll('#tableHead th').forEach(th => {{
    th.addEventListener('click', () => {{
      const col = th.dataset.col;
      if (sortCol === col) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      else {{ sortCol = col; sortDir = 'asc'; }}
      renderTable();
    }});
  }});
  // Toggle top-tables detail rows on click
  document.querySelectorAll('.top-tbl-toggle').forEach(el => {{
    el.addEventListener('click', (e) => {{
      e.stopPropagation();
      const tr = el.closest('tr');
      const detailRow = tr.nextElementSibling;
      if (detailRow && detailRow.classList.contains('detail-row')) {{
        detailRow.classList.toggle('open');
        el.innerHTML = detailRow.classList.contains('open')
          ? el.innerHTML.replace('\u25BE', '\u25B4')
          : el.innerHTML.replace('\u25B4', '\u25BE');
      }}
    }});
  }});
  // Sub-table column sort handlers
  document.querySelectorAll('.sub-sort-hdr').forEach(hdr => {{
    hdr.addEventListener('click', (e) => {{
      e.stopPropagation();
      const col = hdr.dataset.subCol;
      const rank = parseInt(hdr.dataset.subRank);
      const ttData = TOP_TABLES[rank];
      if (!ttData) return;
      // Determine sort direction: toggle if same column
      const grid = hdr.closest('.top-tbl-grid');
      const prevCol = grid.dataset.sortCol || 'rows';
      const prevDir = grid.dataset.sortDir || 'desc';
      let dir = 'desc';
      if (prevCol === col) dir = prevDir === 'desc' ? 'asc' : 'desc';
      grid.dataset.sortCol = col;
      grid.dataset.sortDir = dir;
      // Sort
      const sorted = ttData.slice(0,20).sort((a,b) => {{
        let va = a[col], vb = b[col];
        if (typeof va === 'string') {{ va = va.toLowerCase(); vb = (vb||'').toLowerCase(); }}
        if (va == null) va = 0; if (vb == null) vb = 0;
        return dir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
      }});
      // Re-render body
      const tbody = grid.querySelector('.sub-tbl-body');
      let html = '';
      sorted.forEach((t, i) => {{
        html += `<tr><td class="text-slate-400">${{i+1}}</td><td class="text-slate-700 font-medium">${{t.name}}</td><td style="text-align:right">${{Number(t.rows||0).toLocaleString()}}</td><td style="text-align:right">${{fmtSizeBytes(t.data_length||0)}}</td><td style="text-align:right">${{fmtSizeBytes(t.index_length||0)}}</td><td style="text-align:right;font-weight:600">${{fmtSizeBytes((t.data_length||0)+(t.index_length||0))}}</td></tr>`;
      }});
      tbody.innerHTML = html;
      // Update sort indicators on headers
      grid.querySelectorAll('.sub-sort-hdr').forEach(h => {{
        let label = h.textContent.replace(/ [▲▼]/g, '').trim();
        if (h.dataset.subCol === col) label += dir === 'asc' ? ' \u25B2' : ' \u25BC';
        h.textContent = label;
      }});
    }});
  }});
  // Re-attach column search input handlers (debounced)
  document.querySelectorAll('.col-search-row input[data-search-col]').forEach(inp => {{
    inp.addEventListener('input', debounce((e) => {{
      const colKey = e.target.dataset.searchCol;
      colSearchTerms[colKey] = e.target.value;
      renderTable();
      const restored = document.querySelector(`input[data-search-col="${{colKey}}"]`);
      if (restored) {{ restored.focus(); restored.selectionStart = restored.selectionEnd = restored.value.length; }}
    }}, 150));
  }});
  rapTrack('render', {{ ms: Math.round(performance.now() - _renderStart), tab: currentTab }});
}}

function switchTab(tab) {{
  rapTrack('tab', tab);
  currentTab = tab;
  sortCol = 'rank'; sortDir = 'asc';
  colSearchTerms = {{}};
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const tabBtn = document.querySelector(`.tab-btn[onclick*="${{tab}}"]`);
  if (tabBtn) tabBtn.classList.add('active');
  const isRap = tab === 'rap';
  document.getElementById('mainTable').style.display = isRap ? 'none' : '';
  document.getElementById('instanceCount').style.display = isRap ? 'none' : '';
  document.getElementById('filterBar').style.display = isRap ? 'none' : '';
  document.getElementById('rapPanel').style.display = isRap ? '' : 'none';
  if (isRap) {{ renderRap(); }} else {{ renderTable(); }}
}}

function exportCSV() {{
  const cols = TABS[currentTab];
  let csv = cols.map(c=>c.label).join(',') + '\\n';
  ALL_ROWS.forEach(r => {{
    csv += cols.map(c => {{
      let v = r[c.key]; if (v===null||v===undefined) v='';
      return typeof v==='string' && v.includes(',') ? `"${{v}}"` : v;
    }}).join(',') + '\\n';
  }});
  const blob = new Blob([csv], {{type:'text/csv'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `ruckus_${{currentTab}}_${{new Date().toISOString().slice(0,10)}}.csv`;
  a.click();
}}

// Search (debounced)
document.getElementById('globalSearch').addEventListener('input', debounce(e => {{
  searchTerm = e.target.value.toLowerCase();
  renderTable();
}}, 150));

// Charts
function initCharts() {{
  // DB Size + Txn chart
  new Chart(document.getElementById('dbChart'), {{
    type: 'bar',
    data: {{
      labels: {chart_labels},
      datasets: [
        {{ label: 'DB Size (GB)', data: {chart_db_sizes}, backgroundColor: 'rgba(52,211,153,0.6)', yAxisID: 'y' }},
        {{ label: 'Txn/Day (90d)', data: {chart_txn}, backgroundColor: 'rgba(99,102,241,0.6)', yAxisID: 'y1' }},
      ]
    }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ticks: {{ color: '#475569', font: {{ size: 9 }} }}, grid: {{ display: false }} }},
        y: {{ position: 'left', title: {{ display: true, text: 'GB', color: '#475569' }}, ticks: {{ color: '#475569' }}, grid: {{ color: 'rgba(0,0,0,0.06)' }} }},
        y1: {{ position: 'right', title: {{ display: true, text: 'Txn/Day', color: '#475569' }}, ticks: {{ color: '#475569' }}, grid: {{ display: false }} }},
      }},
      plugins: {{ legend: {{ labels: {{ color: '#475569' }} }} }}
    }}
  }});

  // Tier donut
  const tierLabels = Object.keys(TIER_DATA).map(k=>k.replace('postgresdb-','').replace('mysqldb-',''));
  new Chart(document.getElementById('tierChart'), {{
    type: 'doughnut',
    data: {{
      labels: tierLabels,
      datasets: [{{ data: Object.values(TIER_DATA), backgroundColor: ['#6366f1','#34d399','#facc15','#f87171','#a78bfa','#38bdf8','#fb923c'] }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#475569', boxWidth: 12, font: {{ size: 10 }} }} }} }} }}
  }});

  // DB type donut
  new Chart(document.getElementById('dbtypeChart'), {{
    type: 'doughnut',
    data: {{
      labels: Object.keys(DBTYPE_DATA),
      datasets: [{{ data: Object.values(DBTYPE_DATA), backgroundColor: ['#34d399','#6366f1','#facc15','#f87171'] }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#475569', boxWidth: 12, font: {{ size: 10 }} }} }} }} }}
  }});
}}

// Analyze modal
function showAnalyzeModal(instance) {{
  const existing = document.getElementById('analyzeModal');
  if (existing) existing.remove();

  const row = ALL_ROWS.find(r => r.instance === instance);
  const company = row ? row.company : '';
  const tier = row ? (row.capacity_tier || '').replace('postgresdb-','') : '';
  const dbType = row ? (row.db_type || '') : '';
  const dbSize = row ? Number(row.db_gb_csv || 0).toLocaleString(undefined, {{maximumFractionDigits:1}}) : '?';

  const modal = document.createElement('div');
  modal.id = 'analyzeModal';
  modal.className = 'analyze-modal';
  modal.onclick = (e) => {{ if (e.target === modal) modal.remove(); }};
  modal.innerHTML = `
    <div class="analyze-modal-box">
      <h3 class="text-lg font-bold text-green-800 mb-1">${{instance}}</h3>
      <p class="text-sm text-slate-500 mb-4">${{company}}</p>
      <div class="grid grid-cols-3 gap-3 mb-4 text-xs">
        <div class="bg-green-50 rounded-lg p-2 text-center"><p class="text-slate-400">Tier</p><p class="font-bold text-green-700">${{tier || '\u2014'}}</p></div>
        <div class="bg-green-50 rounded-lg p-2 text-center"><p class="text-slate-400">DB Type</p><p class="font-bold text-green-700">${{dbType || '\u2014'}}</p></div>
        <div class="bg-green-50 rounded-lg p-2 text-center"><p class="text-slate-400">DB Size</p><p class="font-bold text-green-700">${{dbSize}} GB</p></div>
      </div>
      <div class="flex gap-2">
        <a href="/instance?name=${{encodeURIComponent(instance)}}" class="flex-1 text-center px-4 py-2.5 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition">
          View History
        </a>
        <a href="/analysis?instance=${{encodeURIComponent(instance)}}" class="flex-1 text-center px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition">
          Run Analysis
        </a>
        <button onclick="document.getElementById('analyzeModal').remove()" class="px-4 py-2.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-600 text-sm font-medium transition">
          Cancel
        </button>
      </div>
      <p class="text-[10px] text-slate-400 mt-3 text-center">View history or run a new analysis (30\u2013120 seconds)</p>
    </div>
  `;
  document.body.appendChild(modal);
}}

// ── Per-column search toggle ──────────────────────────────────────────────
function toggleColSearch() {{
  colSearchVisible = !colSearchVisible;
  if (!colSearchVisible) colSearchTerms = {{}};
  renderTable();
}}

// ── Right-click context menu (header) ────────────────────────────────────
let _hdrCtxCol = null;

document.addEventListener('contextmenu', function(e) {{
  const th = e.target.closest('th[data-col]');
  if (th && th.closest('#tableHead')) {{
    e.preventDefault();
    _hdrCtxCol = th.dataset.col;
    const menu = document.getElementById('hdrCtxMenu');
    document.getElementById('ctxMenu').style.display = 'none';
    menu.style.display = '';
    menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
    menu.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px';
    return;
  }}
  // Hide header menu if clicking elsewhere
  document.getElementById('hdrCtxMenu').style.display = 'none';

  const td = e.target.closest('td[data-col]');
  if (!td || !td.closest('#tableBody')) return;
  e.preventDefault();
  const cols = TABS[currentTab];
  const ci = parseInt(td.dataset.col);
  const col = cols[ci];
  if (!col) return;
  // Find row data
  const tr = td.closest('tr');
  const allRows = Array.from(tr.parentNode.querySelectorAll('tr:not(.detail-row)'));
  const ri = allRows.indexOf(tr);
  let filtered = ALL_ROWS.filter(r => !searchTerm || r.instance.toLowerCase().includes(searchTerm) || (r.company && r.company.toLowerCase().includes(searchTerm)));
  filtered = applyFilters(filtered);
  filtered.sort((a,b) => {{
    let va = a[sortCol], vb = b[sortCol];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va == null) va = sortDir === 'asc' ? Infinity : -Infinity;
    if (vb == null) vb = sortDir === 'asc' ? Infinity : -Infinity;
    return sortDir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  }});
  const row = filtered[ri];
  if (!row) return;
  _ctxCol = col.key;
  _ctxVal = row[col.key];
  const menu = document.getElementById('ctxMenu');
  menu.style.display = '';
  menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 160) + 'px';
}});

document.addEventListener('click', () => {{ document.getElementById('ctxMenu').style.display = 'none'; document.getElementById('hdrCtxMenu').style.display = 'none'; }});
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') {{ document.getElementById('ctxMenu').style.display = 'none'; document.getElementById('hdrCtxMenu').style.display = 'none'; }} }});

function hdrCtxSort(dir) {{
  document.getElementById('hdrCtxMenu').style.display = 'none';
  if (!_hdrCtxCol) return;
  sortCol = _hdrCtxCol;
  sortDir = dir;
  renderTable();
}}

function ctxApply(op) {{
  document.getElementById('ctxMenu').style.display = 'none';
  if (_ctxCol === null) return;
  const strMode = isStrCol(_ctxCol);
  const val = (strMode || typeof _ctxVal === 'string') ? String(_ctxVal || '') : Number(_ctxVal || 0);
  const displayVal = (typeof val === 'number') ? val.toLocaleString() : val;
  activeFilters.push({{ col: _ctxCol, op: op, val: val, label: getFilterColLabel(_ctxCol) }});
  renderChips();
  renderTable();
}}

// ── RAP: Ruckus Aggregator Performance ───────────────────────────────────
const RAP_KEY = 'ruckus_rap_history';
const rapSession = {{
  start: Date.now(),
  clicks: 0,
  renders: [],       // array of {{ts, ms, tab}}
  tabsVisited: new Set(),
  clickTimes: [],    // timestamps for clicks-per-minute
}};

function rapTrack(type, detail) {{
  if (type === 'click') {{
    rapSession.clicks++;
    rapSession.clickTimes.push(Date.now());
  }} else if (type === 'render') {{
    rapSession.renders.push({{ ts: Date.now(), ms: detail.ms, tab: detail.tab }});
  }} else if (type === 'tab') {{
    rapSession.tabsVisited.add(detail);
  }}
  rapSave();
}}

function rapSave() {{
  try {{
    const history = JSON.parse(localStorage.getItem(RAP_KEY) || '[]');
    // Update or append current session
    const sid = rapSession.start;
    const entry = {{
      id: sid,
      date: new Date(sid).toISOString(),
      duration: Date.now() - sid,
      clicks: rapSession.clicks,
      renderCount: rapSession.renders.length,
      avgRender: rapSession.renders.length > 0 ? Math.round(rapSession.renders.reduce((s,r) => s + r.ms, 0) / rapSession.renders.length) : 0,
      maxRender: rapSession.renders.length > 0 ? Math.round(Math.max(...rapSession.renders.map(r => r.ms))) : 0,
      tabsVisited: [...rapSession.tabsVisited].join(', '),
    }};
    const idx = history.findIndex(h => h.id === sid);
    if (idx >= 0) history[idx] = entry; else history.push(entry);
    localStorage.setItem(RAP_KEY, JSON.stringify(history));
  }} catch(e) {{}}
}}

function rapGetHistory() {{
  try {{ return JSON.parse(localStorage.getItem(RAP_KEY) || '[]'); }} catch(e) {{ return []; }}
}}

function clearRapHistory() {{
  if (confirm('Clear all RAP session history?')) {{
    localStorage.removeItem(RAP_KEY);
    renderRap();
  }}
}}

let rapRenderChart = null;
let rapClickChart = null;

function renderRap() {{
  const history = rapGetHistory();
  const s = rapSession;
  const avgMs = s.renders.length > 0 ? Math.round(s.renders.reduce((a,r) => a + r.ms, 0) / s.renders.length) : 0;
  const maxMs = s.renders.length > 0 ? Math.round(Math.max(...s.renders.map(r => r.ms))) : 0;

  document.getElementById('rapSessionClicks').textContent = s.clicks.toLocaleString();
  document.getElementById('rapAvgRender').textContent = avgMs + ' ms';
  document.getElementById('rapMaxRender').textContent = maxMs + ' ms';
  document.getElementById('rapTotalSessions').textContent = history.length.toLocaleString();

  // Render time chart
  if (rapRenderChart) rapRenderChart.destroy();
  const renderLabels = s.renders.map((r, i) => i + 1);
  const renderData = s.renders.map(r => r.ms);
  const renderColors = renderData.map(ms => ms > 200 ? '#ef4444' : ms > 100 ? '#f59e0b' : '#22c55e');
  rapRenderChart = new Chart(document.getElementById('rapRenderChart'), {{
    type: 'bar',
    data: {{
      labels: renderLabels,
      datasets: [{{ label: 'Render ms', data: renderData, backgroundColor: renderColors, borderRadius: 3 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }}, tooltip: {{
        callbacks: {{ afterLabel: (ctx) => s.renders[ctx.dataIndex] ? 'Tab: ' + s.renders[ctx.dataIndex].tab : '' }}
      }} }},
      scales: {{
        y: {{ beginAtZero: true, title: {{ display: true, text: 'ms' }} }},
        x: {{ title: {{ display: true, text: 'Render #' }} }}
      }}
    }}
  }});

  // Clicks per minute chart
  if (rapClickChart) rapClickChart.destroy();
  const now = Date.now();
  const sessionMinutes = Math.max(1, Math.ceil((now - s.start) / 60000));
  const cpmLabels = [];
  const cpmData = [];
  for (let m = 0; m < sessionMinutes && m < 60; m++) {{
    const mStart = s.start + m * 60000;
    const mEnd = mStart + 60000;
    cpmLabels.push('m' + (m + 1));
    cpmData.push(s.clickTimes.filter(t => t >= mStart && t < mEnd).length);
  }}
  rapClickChart = new Chart(document.getElementById('rapClickChart'), {{
    type: 'line',
    data: {{
      labels: cpmLabels,
      datasets: [{{ label: 'Clicks/min', data: cpmData, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.1)', fill: true, tension: 0.3 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        y: {{ beginAtZero: true, title: {{ display: true, text: 'Clicks' }} }},
        x: {{ title: {{ display: true, text: 'Minute' }} }}
      }}
    }}
  }});

  // Session history table
  let hhtml = '';
  const sorted = [...history].reverse();
  sorted.forEach((h, i) => {{
    const dur = Math.round(h.duration / 1000);
    const durStr = dur >= 3600 ? Math.floor(dur/3600) + 'h ' + Math.floor((dur%3600)/60) + 'm' : dur >= 60 ? Math.floor(dur/60) + 'm ' + (dur%60) + 's' : dur + 's';
    const d = new Date(h.date);
    const dateStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {{hour:'2-digit',minute:'2-digit'}});
    const isCurrent = h.id === s.start;
    const cls = isCurrent ? 'bg-indigo-50 font-semibold' : '';
    hhtml += `<tr class="border-b border-indigo-100 ${{cls}}">`;
    hhtml += `<td class="px-3 py-2">${{sorted.length - i}}${{isCurrent ? ' \u25c0' : ''}}</td>`;
    hhtml += `<td class="px-3 py-2">${{dateStr}}</td>`;
    hhtml += `<td class="px-3 py-2 text-right">${{durStr}}</td>`;
    hhtml += `<td class="px-3 py-2 text-right">${{h.clicks}}</td>`;
    hhtml += `<td class="px-3 py-2 text-right">${{h.renderCount}}</td>`;
    hhtml += `<td class="px-3 py-2 text-right">${{h.avgRender}} ms</td>`;
    hhtml += `<td class="px-3 py-2 text-right ${{h.maxRender > 200 ? 'text-red-600' : h.maxRender > 100 ? 'text-amber-600' : 'text-green-600'}}">${{h.maxRender}} ms</td>`;
    hhtml += `<td class="px-3 py-2 text-right">${{h.tabsVisited}}</td>`;
    hhtml += '</tr>';
  }});
  document.getElementById('rapSessionTable').innerHTML = hhtml;
}}

// Track all clicks on the page
document.addEventListener('click', () => {{ rapTrack('click'); }});

// Init
renderTable();
initCharts();
</script>
</body>
</html>"""
    return html


# ── Excel Report ─────────────────────────────────────────────────────────────

def _generate_excel(rows: List[dict], output_path: str):
    """Generate a multi-sheet Excel workbook."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    HDR_FILL = PatternFill(start_color="1B2A4A", end_color="1B2A4A", fill_type="solid")
    HDR_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
    BODY = Font(name="Calibri", size=10)
    THIN = Border(*(Side(style="thin", color="CCCCCC"),) * 4)
    GREEN = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")

    def _write_header(ws, headers, row=1):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=row, column=c, value=h)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.border = THIN
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Sheet 1: Overview
    ws1 = wb.active
    ws1.title = "Overview"
    headers = [
        "Rank", "Instance", "Company", "Score", "Coverage", "Txn/Day (90d)",
        "DB Size (GB)", "Primary GB", "Tier", "DB Type",
        "Nodes", "Tables", "Total Rows",
        "Rows \u0394", "Tables \u0394", "DB GB \u0394",
        "Data GB", "Index GB", "Release", "Live Data",
    ]
    _write_header(ws1, headers)
    for i, r in enumerate(rows, 2):
        ws1.cell(row=i, column=1, value=r["rank"]).font = BODY
        ws1.cell(row=i, column=2, value=r["instance"]).font = BODY
        ws1.cell(row=i, column=3, value=r.get("company", "")).font = BODY
        ws1.cell(row=i, column=4, value=r["score"]).number_format = "0.000000"
        ws1.cell(row=i, column=5, value=r["coverage"]).font = BODY
        ws1.cell(row=i, column=6, value=r["txn_90d"]).number_format = "#,##0.0"
        ws1.cell(row=i, column=7, value=r["db_gb_csv"]).number_format = "#,##0.0"
        ws1.cell(row=i, column=8, value=r["primary_table_gb"]).number_format = "#,##0.0"
        ws1.cell(row=i, column=9, value=r["capacity_tier"]).font = BODY
        ws1.cell(row=i, column=10, value=r["db_type"]).font = BODY
        ws1.cell(row=i, column=11, value=r["node_count"]).font = BODY
        ws1.cell(row=i, column=12, value=r["table_count"]).number_format = "#,##0"
        ws1.cell(row=i, column=13, value=r["total_table_rows"]).number_format = "#,##0"
        d_rows = r.get("d_total_table_rows")
        ws1.cell(row=i, column=14, value=d_rows if d_rows is not None else "").number_format = "#,##0"
        d_tables = r.get("d_table_count")
        ws1.cell(row=i, column=15, value=d_tables if d_tables is not None else "").number_format = "#,##0"
        d_db = r.get("d_db_gb_csv")
        ws1.cell(row=i, column=16, value=round(d_db, 1) if d_db is not None else "").number_format = "#,##0.0"
        ws1.cell(row=i, column=17, value=r["total_data_size_gb"]).number_format = "#,##0.00"
        ws1.cell(row=i, column=18, value=r["total_index_size_gb"]).number_format = "#,##0.00"
        ws1.cell(row=i, column=19, value=r["release_family"]).font = BODY
        ws1.cell(row=i, column=20, value="Yes" if r["has_ruckus_data"] else "No").font = BODY
        for c in range(1, len(headers) + 1):
            ws1.cell(row=i, column=c).border = THIN

    widths = [6, 30, 35, 12, 10, 16, 14, 12, 24, 12, 8, 10, 16, 14, 12, 12, 12, 12, 16, 8]
    for c, w in enumerate(widths, 1):
        ws1.column_dimensions[get_column_letter(c)].width = w
    ws1.freeze_panes = "D2"

    # Sheet 2: Top Tables per Instance
    ws2 = wb.create_sheet("Top Tables")
    _write_header(ws2, ["Instance", "Table", "Rows", "Data GB", "Index GB", "Total GB", "Server"])
    r = 2
    for row in rows:
        for t in row.get("top_tables", [])[:20]:
            data_gb = (t.get("data_length", 0) or 0) / (1024**3)
            idx_gb = (t.get("index_length", 0) or 0) / (1024**3)
            ws2.cell(row=r, column=1, value=row["instance"]).font = BODY
            ws2.cell(row=r, column=2, value=t.get("name", "")).font = BODY
            ws2.cell(row=r, column=3, value=t.get("rows", 0)).number_format = "#,##0"
            ws2.cell(row=r, column=4, value=round(data_gb, 2)).number_format = "#,##0.00"
            ws2.cell(row=r, column=5, value=round(idx_gb, 2)).number_format = "#,##0.00"
            ws2.cell(row=r, column=6, value=round(data_gb + idx_gb, 2)).number_format = "#,##0.00"
            ws2.cell(row=r, column=7, value=t.get("server", "")).font = BODY
            for c in range(1, 8):
                ws2.cell(row=r, column=c).border = THIN
            r += 1
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 40
    ws2.column_dimensions["G"].width = 50
    ws2.freeze_panes = "C2"

    # Sheet 3: Aggregates
    ws3 = wb.create_sheet("Aggregates")
    _write_header(ws3, ["Metric", "Value"])
    agg_data = [
        ("Total Instances", len(rows)),
        ("Total DB Size (GB)", sum(r["db_gb_csv"] for r in rows)),
        ("Total Rows (all tables)", sum(r["total_table_rows"] for r in rows)),
        ("Total Tables", sum(r["table_count"] for r in rows)),
        ("Avg Txn/Day (90d)", sum(r["txn_90d"] for r in rows) / max(1, len(rows))),
        ("Avg DB Size (GB)", sum(r["db_gb_csv"] for r in rows) / max(1, len(rows))),
        ("Max DB Size (GB)", max((r["db_gb_csv"] for r in rows), default=0)),
        ("Avg Node Count", sum(r["node_count"] for r in rows) / max(1, len(rows))),
        ("Max Node Count", max((r["node_count"] for r in rows), default=0)),
        ("Instances w/ Live Data", sum(1 for r in rows if r["has_ruckus_data"])),
    ]
    for i, (metric, val) in enumerate(agg_data, 2):
        ws3.cell(row=i, column=1, value=metric).font = BODY
        cell = ws3.cell(row=i, column=2, value=val)
        cell.font = BODY
        if isinstance(val, float):
            cell.number_format = "#,##0.0"
        elif isinstance(val, int):
            cell.number_format = "#,##0"
    ws3.column_dimensions["A"].width = 30
    ws3.column_dimensions["B"].width = 20

    wb.save(output_path)


# ── Public API ───────────────────────────────────────────────────────────────

def generate_reports(
    ranked: List[Tuple[str, float, int, dict]],
    collected: Dict[str, dict],
    csv_data: Dict[str, dict],
    company_map: Dict[str, str] = None,
):
    """Main entry point: generate HTML dashboard + Excel workbook."""
    console.print(f"\n[bold cyan]Step 3: Generating reports[/bold cyan]")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    rows = _build_rows(ranked, collected, csv_data, company_map)

    # HTML
    console.print("  Writing HTML dashboard...")
    html = _generate_html(rows, generated_at)
    with open(config.HTML_REPORT, "w", encoding="utf-8") as f:
        f.write(html)
    console.print(f"  [green]Saved:[/green] {config.HTML_REPORT}")

    # Excel
    console.print("  Writing Excel workbook...")
    _generate_excel(rows, config.EXCEL_REPORT)
    console.print(f"  [green]Saved:[/green] {config.EXCEL_REPORT}")

    # JSON dump for programmatic use
    json_path = os.path.join(config.OUTPUT_DIR, "ruckus_aggregated_data.json")
    with open(json_path, "w") as f:
        json.dump(rows, f, default=str, indent=2)
    console.print(f"  [green]Saved:[/green] {json_path}")

    # Save this run for future delta comparisons
    run_id = save_aggregator_run(rows)
    has_deltas = sum(1 for r in rows if r.get("has_delta"))
    console.print(f"  [green]Snapshot saved:[/green] run #{run_id} ({has_deltas} instances with deltas)")

    return rows
