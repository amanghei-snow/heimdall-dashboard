"""
Health score engine.

Computes a 0-100 health score per instance based on multiple signals:
- Replication lag
- Connection saturation
- Table bloat (large tables)
- Semaphore contention
- Job failures
- Backup freshness

Higher score = healthier instance. Score bands:
  90-100  Healthy
  70-89   Watch
  50-69   Warning
  0-49    Critical
"""
import math
from typing import Dict, List, Optional

import db


# ── Scoring functions (each returns 0-100) ───────────────────────────────────

def _score_repl_lag(lag_sec: float) -> float:
    """Score replication lag. 0s=100, >300s=0."""
    if lag_sec < 0:
        return 100  # not applicable (no replication)
    if lag_sec <= 1:
        return 100
    if lag_sec <= 10:
        return 90
    if lag_sec <= 30:
        return 75
    if lag_sec <= 60:
        return 60
    if lag_sec <= 120:
        return 40
    if lag_sec <= 300:
        return 20
    return 0


def _score_connections(app_conn: int, total_conn: int) -> float:
    """Score connection health. High ratio of app:total is good."""
    if total_conn == 0:
        return 100
    ratio = app_conn / total_conn if total_conn > 0 else 0
    # Very high total connections is concerning
    if total_conn > 5000:
        return 20
    if total_conn > 2000:
        return 40
    if total_conn > 1000:
        return 60
    if total_conn > 500:
        return 75
    return 90


def _score_table_bloat(total_rows: int, table_count: int) -> float:
    """Score based on total data volume. Massive tables = lower score."""
    if total_rows == 0:
        return 100
    avg_rows = total_rows / max(table_count, 1)
    # Score based on total rows across all tables
    billions = total_rows / 1_000_000_000
    if billions > 50:
        return 10
    if billions > 20:
        return 30
    if billions > 10:
        return 50
    if billions > 5:
        return 65
    if billions > 1:
        return 80
    return 95


def _score_db_size(data_bytes: int, index_bytes: int) -> float:
    """Score based on total DB size in GB."""
    total_gb = (data_bytes + index_bytes) / (1024 ** 3)
    if total_gb > 10000:
        return 10
    if total_gb > 5000:
        return 25
    if total_gb > 2000:
        return 40
    if total_gb > 1000:
        return 55
    if total_gb > 500:
        return 70
    if total_gb > 100:
        return 85
    return 95


def _score_rss(rss_mb: float) -> float:
    """Score memory usage."""
    if rss_mb <= 0:
        return 100
    gb = rss_mb / 1024
    if gb > 100:
        return 15
    if gb > 50:
        return 35
    if gb > 20:
        return 55
    if gb > 10:
        return 70
    if gb > 5:
        return 85
    return 95


# ── Composite health score ───────────────────────────────────────────────────

# Weights for each component (must sum to 1.0)
# NOTE: repl_lag excluded — discovery data can be up to 24h stale.
# NOTE: connections excluded — scales with instance size, not health.
_WEIGHTS = {
    "table_bloat":  0.35,
    "db_size":      0.35,
    "rss":          0.30,
}


def compute_health_score(metrics: dict) -> dict:
    """
    Compute composite health score from instance metrics.
    Returns dict with component scores and overall score.
    """
    components = {
        "table_bloat": _score_table_bloat(
            metrics.get("total_rows", 0),
            metrics.get("table_count", 0),
        ),
        "db_size": _score_db_size(
            metrics.get("total_data_bytes", 0),
            metrics.get("total_index_bytes", 0),
        ),
        "rss": _score_rss(metrics.get("db_rss_mb", 0)),
    }

    overall = sum(
        components[k] * _WEIGHTS[k] for k in _WEIGHTS
    )

    return {
        "overall": round(overall, 1),
        "components": {k: round(v, 1) for k, v in components.items()},
        "band": _band(overall),
    }


def _band(score: float) -> str:
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "watch"
    if score >= 50:
        return "warning"
    return "critical"


# ── Batch scoring ────────────────────────────────────────────────────────────

def score_snapshot(snapshot_id: int) -> dict:
    """
    Compute and store health scores for all instances in a snapshot.
    Returns summary stats.
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT im.*, i.name, i.customer FROM instance_metrics im "
            "JOIN instances i ON i.id = im.instance_id "
            "WHERE im.snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()

        bands = {"healthy": 0, "watch": 0, "warning": 0, "critical": 0}
        scores = []

        for row in rows:
            m = dict(row)
            result = compute_health_score(m)
            score = result["overall"]
            band = result["band"]
            bands[band] += 1
            scores.append(score)

            conn.execute(
                "UPDATE instance_metrics SET health_score = ? "
                "WHERE snapshot_id = ? AND instance_id = ?",
                (score, snapshot_id, m["instance_id"]),
            )

    avg_score = sum(scores) / len(scores) if scores else 0
    return {
        "total": len(scores),
        "avg_score": round(avg_score, 1),
        "bands": bands,
    }


def get_health_summary(snapshot_id: int = None) -> dict:
    """Get health score distribution for a snapshot (default: latest)."""
    with db.get_db() as conn:
        if snapshot_id is None:
            snap = conn.execute(
                "SELECT id FROM snapshots ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
            if not snap:
                return {"error": "No snapshots"}
            snapshot_id = snap["id"]

        # All scored instances (for band counts)
        all_rows = conn.execute(
            "SELECT im.health_score FROM instance_metrics im "
            "WHERE im.snapshot_id = ? AND im.health_score >= 0",
            (snapshot_id,),
        ).fetchall()
        scores = [r["health_score"] for r in all_rows]

        bands = {"healthy": 0, "watch": 0, "warning": 0, "critical": 0}
        for s in scores:
            bands[_band(s)] += 1

        # Largest instances by data volume (only those with actual data)
        largest = conn.execute(
            "SELECT i.name, i.customer, im.health_score, im.total_rows, "
            "(im.total_data_bytes + im.total_index_bytes) as total_bytes, "
            "im.total_data_bytes, im.total_index_bytes, "
            "im.table_count, im.db_rss_mb "
            "FROM instance_metrics im "
            "JOIN instances i ON i.id = im.instance_id "
            "WHERE im.snapshot_id = ? AND im.total_rows > 0 "
            "ORDER BY im.total_rows DESC LIMIT 500",
            (snapshot_id,),
        ).fetchall()

        return {
            "snapshot_id": snapshot_id,
            "total": len(scores),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "min_score": round(min(scores), 1) if scores else 0,
            "max_score": round(max(scores), 1) if scores else 0,
            "bands": bands,
            "largest": [dict(r) for r in largest],
        }
