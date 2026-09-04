"""
Historical storage for ruckus analysis data using SQLite.

Stores each analysis run with timestamp, allowing delta comparisons
between runs for the same instance.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "history.db")


def _ensure_db():
    """Create the database and tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            health_overall INTEGER,
            health_json TEXT,
            instance_info_json TEXT,
            errors_json TEXT,
            raw_text_path TEXT,
            UNIQUE(instance, timestamp)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS view_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            view_key TEXT NOT NULL,
            label TEXT,
            item_count INTEGER,
            data_json TEXT,
            FOREIGN KEY (run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_runs_instance
        ON analysis_runs(instance, timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_run
        ON view_snapshots(run_id, view_key)
    """)
    conn.commit()
    conn.close()


def save_run(instance: str, health: dict, instance_info: dict,
             views: dict, errors: list, raw_text: str = None) -> Tuple[int, Optional[str]]:
    """Save an analysis run to the database. Returns (run_id, raw_text_path)."""
    _ensure_db()
    now = datetime.now(timezone.utc).isoformat()

    # Optionally save raw text output
    raw_path = None
    if raw_text:
        raw_dir = os.path.join(os.path.dirname(DB_PATH), "raw", instance)
        os.makedirs(raw_dir, exist_ok=True)
        ts_safe = now.replace(":", "-").replace("+", "_")
        raw_path = os.path.join(raw_dir, f"{ts_safe}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """INSERT INTO analysis_runs
               (instance, timestamp, health_overall, health_json, instance_info_json, errors_json, raw_text_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (instance, now, health.get("overall", -1),
             json.dumps(health), json.dumps(instance_info),
             json.dumps(errors), raw_path)
        )
        run_id = cur.lastrowid

        # Save each view as a snapshot
        for view_key, view_data in views.items():
            label = view_data.get("label", view_key)
            data = view_data.get("data", [])
            item_count = len(data) if isinstance(data, list) else 0
            # Compress: only store data for views with <= 500 items
            data_json = json.dumps(data) if item_count <= 500 else json.dumps(f"[{item_count} items - too large]")
            conn.execute(
                """INSERT INTO view_snapshots (run_id, view_key, label, item_count, data_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, view_key, label, item_count, data_json)
            )

        conn.commit()
        return run_id, raw_path
    finally:
        conn.close()


def get_runs(instance: str, limit: int = 20) -> List[dict]:
    """Get recent analysis runs for an instance."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, instance, timestamp, health_overall
               FROM analysis_runs WHERE instance = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (instance, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run_detail(run_id: int) -> Optional[dict]:
    """Get full detail for a specific run."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not run:
            return None

        views = conn.execute(
            "SELECT view_key, label, item_count, data_json FROM view_snapshots WHERE run_id = ?",
            (run_id,)
        ).fetchall()

        result = dict(run)
        result["health"] = json.loads(result.pop("health_json", "{}"))
        result["instance_info"] = json.loads(result.pop("instance_info_json", "{}"))
        result["errors"] = json.loads(result.pop("errors_json", "[]"))
        result["views"] = {}
        for v in views:
            v = dict(v)
            result["views"][v["view_key"]] = {
                "label": v["label"],
                "item_count": v["item_count"],
                "data": json.loads(v["data_json"]),
            }
        return result
    finally:
        conn.close()


def compute_delta(instance: str, current_health: dict, current_views: dict) -> Optional[dict]:
    """Compare current run with the most recent previous run.
    Returns a delta dict highlighting changes, or None if no previous run.
    """
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Get the most recent previous run
        prev = conn.execute(
            """SELECT id, timestamp, health_overall, health_json
               FROM analysis_runs WHERE instance = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (instance,)
        ).fetchone()

        if not prev:
            return None

        prev = dict(prev)
        prev_health = json.loads(prev["health_json"])

        # Get previous view snapshots
        prev_views = {}
        for v in conn.execute(
            "SELECT view_key, item_count FROM view_snapshots WHERE run_id = ?",
            (prev["id"],)
        ).fetchall():
            v = dict(v)
            prev_views[v["view_key"]] = v["item_count"]

        delta = {
            "previous_run_id": prev["id"],
            "previous_timestamp": prev["timestamp"],
            "overall_change": current_health.get("overall", 0) - prev.get("health_overall", 0),
            "category_changes": {},
            "view_count_changes": {},
        }

        # Compare category scores
        prev_cats = prev_health.get("categories", {})
        curr_cats = current_health.get("categories", {})
        for cat_key in set(list(prev_cats.keys()) + list(curr_cats.keys())):
            prev_score = prev_cats.get(cat_key, {}).get("score", -1)
            curr_score = curr_cats.get(cat_key, {}).get("score", -1)
            if prev_score >= 0 and curr_score >= 0 and prev_score != curr_score:
                delta["category_changes"][cat_key] = {
                    "label": curr_cats.get(cat_key, {}).get("label", cat_key),
                    "previous": prev_score,
                    "current": curr_score,
                    "change": curr_score - prev_score,
                }

        # Compare view item counts
        for view_key, view_data in current_views.items():
            curr_count = len(view_data.get("data", [])) if isinstance(view_data.get("data"), list) else 0
            prev_count = prev_views.get(view_key, 0)
            if curr_count != prev_count:
                delta["view_count_changes"][view_key] = {
                    "label": view_data.get("label", view_key),
                    "previous": prev_count,
                    "current": curr_count,
                    "change": curr_count - prev_count,
                }

        return delta
    finally:
        conn.close()


def compute_delta_between_runs(run_id: int) -> Optional[dict]:
    """Compare a specific run with the run immediately before it.
    Returns a delta dict or None if no previous run exists."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Get this run
        current = conn.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not current:
            return None
        current = dict(current)

        # Get the previous run for same instance
        prev = conn.execute(
            """SELECT id, timestamp, health_overall, health_json
               FROM analysis_runs WHERE instance = ? AND id < ?
               ORDER BY id DESC LIMIT 1""",
            (current["instance"], run_id)
        ).fetchone()
        if not prev:
            return None
        prev = dict(prev)

        current_health = json.loads(current.get("health_json", "{}"))
        prev_health = json.loads(prev.get("health_json", "{}"))

        # Get view counts for both runs
        prev_views = {}
        for v in conn.execute(
            "SELECT view_key, item_count FROM view_snapshots WHERE run_id = ?",
            (prev["id"],)
        ).fetchall():
            v = dict(v)
            prev_views[v["view_key"]] = v["item_count"]

        curr_views = {}
        for v in conn.execute(
            "SELECT view_key, item_count FROM view_snapshots WHERE run_id = ?",
            (run_id,)
        ).fetchall():
            v = dict(v)
            curr_views[v["view_key"]] = v["item_count"]

        delta = {
            "previous_run_id": prev["id"],
            "previous_timestamp": prev["timestamp"],
            "overall_change": current.get("health_overall", 0) - prev.get("health_overall", 0),
            "category_changes": {},
            "view_count_changes": {},
        }

        # Compare category scores
        prev_cats = prev_health.get("categories", {})
        curr_cats = current_health.get("categories", {})
        for cat_key in set(list(prev_cats.keys()) + list(curr_cats.keys())):
            prev_score = prev_cats.get(cat_key, {}).get("score", -1)
            curr_score = curr_cats.get(cat_key, {}).get("score", -1)
            if prev_score >= 0 and curr_score >= 0 and prev_score != curr_score:
                delta["category_changes"][cat_key] = {
                    "label": curr_cats.get(cat_key, {}).get("label", cat_key),
                    "previous": prev_score,
                    "current": curr_score,
                    "change": curr_score - prev_score,
                }

        # Compare view item counts
        all_keys = set(list(curr_views.keys()) + list(prev_views.keys()))
        for vk in all_keys:
            cc = curr_views.get(vk, 0)
            pc = prev_views.get(vk, 0)
            if cc != pc:
                delta["view_count_changes"][vk] = {
                    "label": vk,
                    "previous": pc,
                    "current": cc,
                    "change": cc - pc,
                }

        return delta
    finally:
        conn.close()


def get_all_instances() -> List[dict]:
    """Get all instances that have historical data."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT instance, COUNT(*) as run_count,
                      MAX(timestamp) as last_run,
                      MAX(health_overall) as best_health,
                      MIN(health_overall) as worst_health
               FROM analysis_runs
               GROUP BY instance
               ORDER BY last_run DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Aggregator Run Delta Tracking ────────────────────────────────────────────

_DELTA_METRICS = [
    "total_table_rows", "table_count", "db_total_size_gb",
    "total_data_size_gb", "total_index_size_gb", "node_count",
    "txn_90d", "db_gb_csv", "score",
]


def _ensure_aggregator_tables():
    """Create aggregator-level tracking tables if they don't exist."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aggregator_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            top_n INTEGER,
            instance_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aggregator_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            instance TEXT NOT NULL,
            total_table_rows INTEGER DEFAULT 0,
            table_count INTEGER DEFAULT 0,
            db_total_size_gb REAL DEFAULT 0,
            total_data_size_gb REAL DEFAULT 0,
            total_index_size_gb REAL DEFAULT 0,
            node_count INTEGER DEFAULT 0,
            txn_90d REAL DEFAULT 0,
            db_gb_csv REAL DEFAULT 0,
            score REAL DEFAULT 0,
            FOREIGN KEY (run_id) REFERENCES aggregator_runs(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_agg_snap_run
        ON aggregator_snapshots(run_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_agg_snap_inst
        ON aggregator_snapshots(instance)
    """)
    conn.commit()
    conn.close()


def save_aggregator_run(rows: List[dict]) -> int:
    """Save a full aggregator run. *rows* is the list produced by _build_rows.
    Returns the new run_id."""
    _ensure_aggregator_tables()
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO aggregator_runs (timestamp, top_n, instance_count) VALUES (?, ?, ?)",
            (now, len(rows), len(rows)),
        )
        run_id = cur.lastrowid
        for r in rows:
            conn.execute(
                """INSERT INTO aggregator_snapshots
                   (run_id, instance, total_table_rows, table_count,
                    db_total_size_gb, total_data_size_gb, total_index_size_gb,
                    node_count, txn_90d, db_gb_csv, score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    r["instance"],
                    r.get("total_table_rows", 0),
                    r.get("table_count", 0),
                    r.get("db_total_size_gb", 0),
                    r.get("total_data_size_gb", 0),
                    r.get("total_index_size_gb", 0),
                    r.get("node_count", 0),
                    r.get("txn_90d", 0),
                    r.get("db_gb_csv", 0),
                    r.get("score", 0),
                ),
            )
        conn.commit()
        return run_id
    finally:
        conn.close()


def get_previous_aggregator_snapshot() -> Optional[Dict[str, dict]]:
    """Load the most recent aggregator run's per-instance data.
    Returns {instance_name: {metric: value, ...}} or None if no previous run."""
    _ensure_aggregator_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        prev_run = conn.execute(
            "SELECT id, timestamp FROM aggregator_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not prev_run:
            return None

        snaps = conn.execute(
            "SELECT * FROM aggregator_snapshots WHERE run_id = ?",
            (prev_run["id"],),
        ).fetchall()
        result = {}
        for s in snaps:
            s = dict(s)
            result[s["instance"]] = {m: s.get(m, 0) for m in _DELTA_METRICS}
        return result
    finally:
        conn.close()


def compute_aggregator_deltas(current_rows: List[dict]) -> Dict[str, dict]:
    """Compare current aggregator rows against the previous run.
    Returns {instance: {metric_delta: value, ...}} for every instance
    that has a previous snapshot. Delta keys are prefixed with 'd_'."""
    prev = get_previous_aggregator_snapshot()
    if not prev:
        return {}

    deltas = {}
    for r in current_rows:
        inst = r["instance"]
        if inst not in prev:
            continue
        # Skip delta for instances using fallback data — they have no
        # fresh collection, so comparing would be misleading.
        if r.get("used_fallback"):
            continue
        d = {}
        for m in _DELTA_METRICS:
            cur_val = r.get(m, 0) or 0
            prev_val = prev[inst].get(m, 0) or 0
            d[f"d_{m}"] = cur_val - prev_val
        deltas[inst] = d
    return deltas



# ── Per-Table Row Count Delta Tracking ──────────────────────────────────────

def _ensure_table_tracking():
    """Create table-level row count tracking tables."""
    _ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS table_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instance TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            table_name TEXT NOT NULL,
            catalog TEXT DEFAULT '',
            rows INTEGER DEFAULT 0,
            data_size TEXT DEFAULT '',
            index_size TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_table_snap_inst_ts
        ON table_snapshots(instance, timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_table_snap_inst_tbl
        ON table_snapshots(instance, table_name)
    """)
    conn.commit()
    conn.close()


def save_table_snapshot(instance: str, tables: List[dict]) -> str:
    """Save a snapshot of table row counts for an instance.
    tables: list of dicts with keys: name, catalog, rows, data, index
    Returns the timestamp used."""
    _ensure_table_tracking()
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        for t in tables:
            conn.execute(
                """INSERT INTO table_snapshots
                   (instance, timestamp, table_name, catalog, rows, data_size, index_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (instance, now, t.get("name", ""),
                 t.get("catalog", ""), t.get("rows", 0),
                 t.get("data", ""), t.get("index", ""))
            )
        conn.commit()
        return now
    finally:
        conn.close()


def get_table_deltas(instance: str) -> Optional[dict]:
    """Compare the two most recent table snapshots for an instance.
    Returns dict with per-table row count changes, or None if < 2 snapshots."""
    _ensure_table_tracking()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Get the two most recent distinct timestamps
        timestamps = conn.execute(
            """SELECT DISTINCT timestamp FROM table_snapshots
               WHERE instance = ? ORDER BY timestamp DESC LIMIT 2""",
            (instance,)
        ).fetchall()

        if len(timestamps) < 2:
            return None

        curr_ts = timestamps[0]["timestamp"]
        prev_ts = timestamps[1]["timestamp"]

        # Load both snapshots
        def load_snap(ts):
            rows = conn.execute(
                """SELECT table_name, catalog, rows, data_size, index_size
                   FROM table_snapshots WHERE instance = ? AND timestamp = ?""",
                (instance, ts)
            ).fetchall()
            # Dedup by table_name (keep first occurrence)
            seen = {}
            for r in rows:
                r = dict(r)
                key = r["table_name"]
                if key not in seen:
                    seen[key] = r
            return seen

        curr = load_snap(curr_ts)
        prev = load_snap(prev_ts)

        changes = []
        all_tables = set(list(curr.keys()) + list(prev.keys()))
        for tbl in sorted(all_tables):
            curr_rows = curr.get(tbl, {}).get("rows", 0) or 0
            prev_rows = prev.get(tbl, {}).get("rows", 0) or 0
            delta = curr_rows - prev_rows
            if delta != 0:
                changes.append({
                    "table": tbl,
                    "catalog": curr.get(tbl, prev.get(tbl, {})).get("catalog", ""),
                    "prev_rows": prev_rows,
                    "curr_rows": curr_rows,
                    "delta": delta,
                    "pct_change": round(delta / prev_rows * 100, 2) if prev_rows else None,
                    "curr_data": curr.get(tbl, {}).get("data_size", ""),
                    "curr_index": curr.get(tbl, {}).get("index_size", ""),
                })

        # Sort by absolute delta descending
        changes.sort(key=lambda x: abs(x["delta"]), reverse=True)

        return {
            "instance": instance,
            "current_timestamp": curr_ts,
            "previous_timestamp": prev_ts,
            "total_tables_current": len(curr),
            "total_tables_previous": len(prev),
            "tables_added": len(set(curr.keys()) - set(prev.keys())),
            "tables_removed": len(set(prev.keys()) - set(curr.keys())),
            "tables_changed": len(changes),
            "changes": changes,
        }
    finally:
        conn.close()


def get_table_history(instance: str, table_name: str, limit: int = 20) -> List[dict]:
    """Get row count history for a specific table across snapshots."""
    _ensure_table_tracking()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT timestamp, rows, data_size, index_size
               FROM table_snapshots
               WHERE instance = ? AND table_name = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (instance, table_name, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_table_snapshot_timestamps(instance: str, limit: int = 10) -> List[str]:
    """Get available snapshot timestamps for an instance."""
    _ensure_table_tracking()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """SELECT DISTINCT timestamp FROM table_snapshots
               WHERE instance = ? ORDER BY timestamp DESC LIMIT ?""",
            (instance, limit)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_aggregator_run_history(limit: int = 10) -> List[dict]:
    """Return metadata for recent aggregator runs."""
    _ensure_aggregator_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM aggregator_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
