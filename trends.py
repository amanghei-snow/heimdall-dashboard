"""
Trend analysis, anomaly detection, and capacity forecasting.

Compares metrics across snapshots to identify:
- Growth rates (row count, DB size, connections)
- Anomalous spikes or drops
- Projected capacity exhaustion dates
"""
import math
import time
from typing import Dict, List, Optional, Tuple

import config
import db


# ── Growth analysis ──────────────────────────────────────────────────────────

def compute_growth(snapshot_a_id: int, snapshot_b_id: int) -> List[dict]:
    """
    Compare two snapshots and compute growth per instance.
    snapshot_a = older, snapshot_b = newer.
    Returns list of instances with growth metrics.
    """
    with db.get_db() as conn:
        # Get timestamps for rate calculation
        sa = conn.execute(
            "SELECT taken_at FROM snapshots WHERE id = ?", (snapshot_a_id,)
        ).fetchone()
        sb = conn.execute(
            "SELECT taken_at FROM snapshots WHERE id = ?", (snapshot_b_id,)
        ).fetchone()
        if not sa or not sb:
            return []

        days_between = max((sb["taken_at"] - sa["taken_at"]) / 86400, 0.01)

        rows = conn.execute(
            "SELECT i.name, i.customer, "
            "a.total_rows as rows_before, b.total_rows as rows_after, "
            "a.total_data_bytes as data_before, b.total_data_bytes as data_after, "
            "a.total_index_bytes as idx_before, b.total_index_bytes as idx_after, "
            "a.db_total_conn as conn_before, b.db_total_conn as conn_after, "
            "a.repl_lag_sec as lag_before, b.repl_lag_sec as lag_after, "
            "b.health_score "
            "FROM instance_metrics a "
            "JOIN instance_metrics b ON a.instance_id = b.instance_id "
            "JOIN instances i ON i.id = a.instance_id "
            "WHERE a.snapshot_id = ? AND b.snapshot_id = ?",
            (snapshot_a_id, snapshot_b_id),
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            row_delta = d["rows_after"] - d["rows_before"]
            data_delta = (d["data_after"] + d["idx_after"]) - (d["data_before"] + d["idx_before"])

            d["row_growth"] = row_delta
            d["row_growth_pct"] = (
                round(row_delta / d["rows_before"] * 100, 2)
                if d["rows_before"] > 0 else 0
            )
            d["row_growth_per_day"] = round(row_delta / days_between)
            d["data_growth_bytes"] = data_delta
            d["data_growth_gb"] = round(data_delta / (1024**3), 2)
            d["data_growth_per_day_gb"] = round(data_delta / days_between / (1024**3), 3)
            d["days_between"] = round(days_between, 1)
            results.append(d)

        results.sort(key=lambda x: x["row_growth"], reverse=True)
        return results


def get_table_growth(table_name: str, snapshot_a_id: int,
                     snapshot_b_id: int, limit: int = 50) -> List[dict]:
    """Compare a specific table's metrics across two snapshots."""
    with db.get_db() as conn:
        sa = conn.execute(
            "SELECT taken_at FROM snapshots WHERE id = ?", (snapshot_a_id,)
        ).fetchone()
        sb = conn.execute(
            "SELECT taken_at FROM snapshots WHERE id = ?", (snapshot_b_id,)
        ).fetchone()
        if not sa or not sb:
            return []
        days = max((sb["taken_at"] - sa["taken_at"]) / 86400, 0.01)

        rows = conn.execute(
            "SELECT i.name, i.customer, "
            "a.rows as rows_before, b.rows as rows_after "
            "FROM table_metrics a "
            "JOIN table_metrics b ON a.instance_id = b.instance_id "
            "  AND a.table_name = b.table_name "
            "JOIN instances i ON i.id = a.instance_id "
            "WHERE a.snapshot_id = ? AND b.snapshot_id = ? "
            "AND a.table_name = ? "
            "ORDER BY (b.rows - a.rows) DESC LIMIT ?",
            (snapshot_a_id, snapshot_b_id, table_name, limit),
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            delta = d["rows_after"] - d["rows_before"]
            d["row_growth"] = delta
            d["row_growth_pct"] = (
                round(delta / d["rows_before"] * 100, 2)
                if d["rows_before"] > 0 else 0
            )
            d["row_growth_per_day"] = round(delta / days)
            results.append(d)
        return results


# ── Anomaly detection ────────────────────────────────────────────────────────

# Thresholds for anomaly flags
_ANOMALY_THRESHOLDS = {
    "row_growth_pct": {
        "warning": 50,     # 50% growth between snapshots
        "critical": 200,   # 200% growth
    },
    "repl_lag_spike": {
        "warning": 60,     # lag went from <60 to >60
        "critical": 300,   # lag > 300s
    },
    "connection_spike": {
        "warning": 500,    # connections increased by 500+
        "critical": 2000,  # connections increased by 2000+
    },
    "data_growth_gb": {
        "warning": 100,    # grew 100 GB between snapshots
        "critical": 500,   # grew 500 GB
    },
}


def detect_anomalies(snapshot_a_id: int, snapshot_b_id: int) -> List[dict]:
    """
    Detect anomalies between two snapshots.
    Returns list of anomaly records.
    """
    growth = compute_growth(snapshot_a_id, snapshot_b_id)
    anomalies = []
    now = time.time()

    for g in growth:
        instance = g["name"]

        # Row growth anomaly
        pct = abs(g.get("row_growth_pct", 0))
        if pct >= _ANOMALY_THRESHOLDS["row_growth_pct"]["critical"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "row_growth_pct", "value": pct,
                "baseline": 0, "severity": "critical",
                "description": f"Row count changed {pct:.0f}% ({_fmt_num(g['row_growth'])} rows)",
            })
        elif pct >= _ANOMALY_THRESHOLDS["row_growth_pct"]["warning"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "row_growth_pct", "value": pct,
                "baseline": 0, "severity": "warning",
                "description": f"Row count changed {pct:.0f}% ({_fmt_num(g['row_growth'])} rows)",
            })

        # Replication lag spike
        lag_after = g.get("lag_after", -1)
        lag_before = g.get("lag_before", -1)
        if lag_after >= _ANOMALY_THRESHOLDS["repl_lag_spike"]["critical"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "repl_lag", "value": lag_after,
                "baseline": lag_before, "severity": "critical",
                "description": f"Replication lag {lag_after:.0f}s (was {lag_before:.0f}s)",
            })
        elif (lag_after >= _ANOMALY_THRESHOLDS["repl_lag_spike"]["warning"]
              and lag_before < _ANOMALY_THRESHOLDS["repl_lag_spike"]["warning"]):
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "repl_lag", "value": lag_after,
                "baseline": lag_before, "severity": "warning",
                "description": f"Replication lag spiked to {lag_after:.0f}s (was {lag_before:.0f}s)",
            })

        # Connection spike
        conn_delta = g.get("conn_after", 0) - g.get("conn_before", 0)
        if conn_delta >= _ANOMALY_THRESHOLDS["connection_spike"]["critical"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "connections", "value": g["conn_after"],
                "baseline": g["conn_before"], "severity": "critical",
                "description": f"Connections jumped +{conn_delta} to {g['conn_after']}",
            })
        elif conn_delta >= _ANOMALY_THRESHOLDS["connection_spike"]["warning"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "connections", "value": g["conn_after"],
                "baseline": g["conn_before"], "severity": "warning",
                "description": f"Connections increased +{conn_delta} to {g['conn_after']}",
            })

        # Data growth
        data_gb = g.get("data_growth_gb", 0)
        if data_gb >= _ANOMALY_THRESHOLDS["data_growth_gb"]["critical"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "data_growth", "value": data_gb,
                "baseline": 0, "severity": "critical",
                "description": f"Data grew {data_gb:.1f} GB between snapshots",
            })
        elif data_gb >= _ANOMALY_THRESHOLDS["data_growth_gb"]["warning"]:
            anomalies.append({
                "instance": instance, "customer": g["customer"],
                "metric": "data_growth", "value": data_gb,
                "baseline": 0, "severity": "warning",
                "description": f"Data grew {data_gb:.1f} GB between snapshots",
            })

    return anomalies


def store_anomalies(snapshot_id: int, anomalies: List[dict]):
    """Persist detected anomalies to the database."""
    now = time.time()
    with db.get_db() as conn:
        for a in anomalies:
            inst = conn.execute(
                "SELECT id FROM instances WHERE name = ?", (a["instance"],)
            ).fetchone()
            if not inst:
                continue
            conn.execute(
                "INSERT INTO anomalies "
                "(snapshot_id, instance_id, metric, value, baseline, "
                "severity, description, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, inst["id"], a["metric"], a["value"],
                 a["baseline"], a["severity"], a["description"], now),
            )


def get_anomalies(snapshot_id: int = None, severity: str = None,
                  limit: int = 100) -> List[dict]:
    """Retrieve anomalies, optionally filtered."""
    with db.get_db() as conn:
        sql = (
            "SELECT a.*, i.name as instance, i.customer "
            "FROM anomalies a "
            "JOIN instances i ON i.id = a.instance_id "
        )
        params = []
        clauses = []

        if snapshot_id:
            clauses.append("a.snapshot_id = ?")
            params.append(snapshot_id)
        if severity:
            clauses.append("a.severity = ?")
            params.append(severity)

        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "ORDER BY a.created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── Capacity forecasting ────────────────────────────────────────────────────

# Capacity tier limits in GB (approximate)
_TIER_LIMITS_GB = {
    1:    50,    # nano
    2:   100,    # pico
    3:   200,    # micro
    4:   400,    # mini
    5:   600,    # exp-small
    6:  1000,    # small
    7:  2000,    # medium
    8:  4000,    # large
    9:  6000,    # xlarge
    10: 8000,    # xxlarge
    11: 10000,   # ultra
    12: 15000,   # mega
    13: 20000,   # giga
    14: 30000,   # tera
    15: 50000,   # peta
}


def forecast_capacity(snapshot_a_id: int, snapshot_b_id: int,
                      csv_data: dict = None) -> List[dict]:
    """
    Predict when instances will exhaust their capacity tier.
    Uses linear extrapolation from growth between two snapshots.
    Returns instances sorted by urgency (soonest exhaustion first).
    """
    growth = compute_growth(snapshot_a_id, snapshot_b_id)
    if not growth:
        return []

    # Load CSV data for tier info if available
    tier_map = {}
    if csv_data:
        for inst_name, info in csv_data.items():
            tier = info.get("capacity_tier", "")
            ordinal = config.CAPACITY_TIERS.get(tier, 0)
            tier_map[inst_name] = {
                "tier": tier,
                "ordinal": ordinal,
                "limit_gb": _TIER_LIMITS_GB.get(ordinal, 0),
            }

    forecasts = []
    for g in growth:
        name = g["name"]
        current_gb = (g["data_after"] + g["idx_after"]) / (1024**3)
        growth_per_day_gb = g.get("data_growth_per_day_gb", 0)

        tier_info = tier_map.get(name, {})
        limit_gb = tier_info.get("limit_gb", 0)

        if limit_gb <= 0 or growth_per_day_gb <= 0:
            continue

        remaining_gb = limit_gb - current_gb
        if remaining_gb <= 0:
            days_until_full = 0
        else:
            days_until_full = remaining_gb / growth_per_day_gb

        forecasts.append({
            "instance": name,
            "customer": g["customer"],
            "tier": tier_info.get("tier", "unknown"),
            "current_gb": round(current_gb, 1),
            "limit_gb": limit_gb,
            "usage_pct": round(current_gb / limit_gb * 100, 1),
            "growth_per_day_gb": round(growth_per_day_gb, 3),
            "days_until_full": round(days_until_full),
            "months_until_full": round(days_until_full / 30, 1),
            "health_score": g.get("health_score", -1),
        })

    forecasts.sort(key=lambda x: x["days_until_full"])
    return forecasts


# ── Top movers ───────────────────────────────────────────────────────────────

def top_growers(snapshot_a_id: int, snapshot_b_id: int,
                metric: str = "rows", limit: int = 50) -> List[dict]:
    """Get instances with the largest absolute growth in a metric."""
    growth = compute_growth(snapshot_a_id, snapshot_b_id)
    if metric == "rows":
        growth.sort(key=lambda x: x.get("row_growth", 0), reverse=True)
    elif metric == "data":
        growth.sort(key=lambda x: x.get("data_growth_bytes", 0), reverse=True)
    elif metric == "connections":
        growth.sort(key=lambda x: (x.get("conn_after", 0) - x.get("conn_before", 0)), reverse=True)
    return growth[:limit]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_num(n: int) -> str:
    if abs(n) >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)
