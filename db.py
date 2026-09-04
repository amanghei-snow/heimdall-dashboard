"""
SQLite database for historical snapshots, trend tracking, and analytics.

Stores per-instance and per-table metrics from each collection run,
enabling growth analysis, anomaly detection, and capacity forecasting.
"""
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import config

DB_PATH = os.path.join(config.OUTPUT_DIR, "ruckus_history.db")

# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at        REAL    NOT NULL,           -- unix timestamp
    total_instances INTEGER NOT NULL DEFAULT 0,
    collected       INTEGER NOT NULL DEFAULT 0,
    cached          INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    duration_sec    REAL    NOT NULL DEFAULT 0,
    bindings        TEXT    NOT NULL DEFAULT '',
    note            TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS instances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    customer    TEXT    NOT NULL DEFAULT '',
    used_for    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS instance_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    instance_id     INTEGER NOT NULL REFERENCES instances(id),
    -- DB server metrics
    db_count        INTEGER NOT NULL DEFAULT 0,
    db_total_conn   INTEGER NOT NULL DEFAULT 0,
    db_app_conn     INTEGER NOT NULL DEFAULT 0,
    db_rss_mb       REAL    NOT NULL DEFAULT 0,
    -- Replication
    repl_lag_sec    REAL    NOT NULL DEFAULT -1,  -- -1 = not available
    -- Tables
    table_count     INTEGER NOT NULL DEFAULT 0,
    total_rows      INTEGER NOT NULL DEFAULT 0,
    total_data_bytes INTEGER NOT NULL DEFAULT 0,
    total_index_bytes INTEGER NOT NULL DEFAULT 0,
    -- App servers / nodes
    app_server_count INTEGER NOT NULL DEFAULT 0,
    node_count       INTEGER NOT NULL DEFAULT 0,
    -- Semaphores
    semaphore_count  INTEGER NOT NULL DEFAULT 0,
    -- Jobs
    job_count        INTEGER NOT NULL DEFAULT 0,
    -- Health score (computed)
    health_score     REAL    NOT NULL DEFAULT -1,  -- 0-100, -1 = not computed
    -- Raw JSON for anything else
    raw_summary      TEXT    NOT NULL DEFAULT '{}',

    UNIQUE(snapshot_id, instance_id)
);

CREATE TABLE IF NOT EXISTS table_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    instance_id     INTEGER NOT NULL REFERENCES instances(id),
    table_name      TEXT    NOT NULL,
    rows            INTEGER NOT NULL DEFAULT 0,
    data_bytes      INTEGER NOT NULL DEFAULT 0,
    index_bytes     INTEGER NOT NULL DEFAULT 0,

    UNIQUE(snapshot_id, instance_id, table_name)
);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id),
    tag         TEXT    NOT NULL,
    created_at  REAL    NOT NULL,

    UNIQUE(instance_id, tag)
);

CREATE TABLE IF NOT EXISTS anomalies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots(id),
    instance_id     INTEGER NOT NULL REFERENCES instances(id),
    metric          TEXT    NOT NULL,
    value           REAL    NOT NULL,
    baseline        REAL    NOT NULL DEFAULT 0,
    severity        TEXT    NOT NULL DEFAULT 'warning',  -- info/warning/critical
    description     TEXT    NOT NULL DEFAULT '',
    created_at      REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_im_snapshot ON instance_metrics(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_im_instance ON instance_metrics(instance_id);
CREATE INDEX IF NOT EXISTS idx_tm_snapshot ON table_metrics(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_tm_instance ON table_metrics(instance_id);
CREATE INDEX IF NOT EXISTS idx_tm_table    ON table_metrics(table_name);
CREATE INDEX IF NOT EXISTS idx_anomalies_snapshot ON anomalies(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_instance ON anomalies(instance_id);
CREATE INDEX IF NOT EXISTS idx_tags_instance ON tags(instance_id);
"""


# ── Connection management ────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript(_SCHEMA)
    conn.close()


@contextmanager
def get_db():
    """Context manager yielding a DB connection with auto-commit."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Partition normalization (shared with top_tables.py) ──────────────────────

def normalize_table_name(name: str) -> str:
    """Collapse partition suffixes into base table name."""
    if name.startswith("ts_c_"):
        return "ts_c"
    if name.startswith("ts_index_"):
        return "ts_index"
    m = re.match(r'^(.+?)\d{3,}$', name)
    if m:
        return m.group(1)
    return name


# ── Instance upsert ──────────────────────────────────────────────────────────

def _upsert_instance(conn: sqlite3.Connection, name: str,
                     customer: str, used_for: str) -> int:
    """Insert or update instance, return its id."""
    conn.execute(
        "INSERT INTO instances (name, customer, used_for) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET customer=excluded.customer, "
        "used_for=excluded.used_for",
        (name, customer, used_for),
    )
    row = conn.execute(
        "SELECT id FROM instances WHERE name = ?", (name,)
    ).fetchone()
    return row["id"]


# ── Snapshot ingestion ───────────────────────────────────────────────────────

def ingest_from_cache(note: str = "", bindings: str = "") -> int:
    """
    Read all cache files and create a new snapshot in the database.
    Returns the snapshot id.
    """
    import glob

    cache_dir = config.CACHE_DIR
    cache_files = glob.glob(os.path.join(cache_dir, "*.json"))

    if not bindings:
        bindings = config.RUCKUS_COMBINED_BINDINGS

    start = time.time()
    total = len(cache_files)
    collected = 0
    failed = 0

    with get_db() as conn:
        # Create snapshot record
        cur = conn.execute(
            "INSERT INTO snapshots (taken_at, total_instances, bindings, note) "
            "VALUES (?, ?, ?, ?)",
            (time.time(), total, bindings, note),
        )
        snap_id = cur.lastrowid

        for path in cache_files:
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                failed += 1
                continue

            views = data.get("views", {})
            if not views:
                failed += 1
                continue

            # Instance info
            inst = views.get("instance", {})
            if isinstance(inst, list) and inst:
                inst = inst[0]
            if not isinstance(inst, dict):
                inst = {}

            inst_name = inst.get("name", os.path.basename(path).replace(".json", ""))
            customer = inst.get("customer", "")
            used_for = inst.get("usedFor", "")

            instance_id = _upsert_instance(conn, inst_name, customer, used_for)

            # Aggregate DB metrics
            databases = views.get("databases", [])
            if not isinstance(databases, list):
                databases = []
            db_count = len(databases)
            db_total_conn = sum(d.get("allConnections", 0) or 0 for d in databases if isinstance(d, dict))
            db_app_conn = sum(d.get("appConnections", 0) or 0 for d in databases if isinstance(d, dict))
            db_rss_mb = sum(d.get("residentSetSizeMb", 0) or 0 for d in databases if isinstance(d, dict))

            # Replication lag
            repl = views.get("replication", [])
            if not isinstance(repl, list):
                repl = []
            repl_lag = -1
            for r in repl:
                if isinstance(r, dict):
                    lag = r.get("replicationLag", r.get("sysClusterStateLag", -1))
                    if lag is not None and lag >= 0:
                        repl_lag = max(repl_lag, lag)

            # Tables
            tables = views.get("tables", [])
            if not isinstance(tables, list):
                tables = []
            table_count = len(tables)
            total_rows = 0
            total_data = 0
            total_idx = 0

            # Aggregate and normalize tables
            table_agg = {}
            for t in tables:
                if not isinstance(t, dict):
                    continue
                raw_name = t.get("name", "")
                if not raw_name:
                    continue
                norm = normalize_table_name(raw_name)
                rows = t.get("rows", 0) or 0
                db = t.get("dataLength", 0) or 0
                ib = t.get("indexLength", 0) or 0
                total_rows += rows
                total_data += db
                total_idx += ib
                if norm in table_agg:
                    table_agg[norm] = (
                        table_agg[norm][0] + rows,
                        table_agg[norm][1] + db,
                        table_agg[norm][2] + ib,
                    )
                else:
                    table_agg[norm] = (rows, db, ib)

            # App servers, nodes, semaphores, jobs
            app_servers = views.get("appservers", [])
            app_count = len(app_servers) if isinstance(app_servers, list) else 0
            nodes = views.get("nodes", [])
            node_count = len(nodes) if isinstance(nodes, list) else 0
            sems = views.get("semaphores", [])
            sem_count = len(sems) if isinstance(sems, list) else 0
            jobs = views.get("jobs", [])
            job_count = len(jobs) if isinstance(jobs, list) else 0

            # Insert instance metrics
            conn.execute(
                "INSERT OR REPLACE INTO instance_metrics "
                "(snapshot_id, instance_id, db_count, db_total_conn, db_app_conn, "
                "db_rss_mb, repl_lag_sec, table_count, total_rows, total_data_bytes, "
                "total_index_bytes, app_server_count, node_count, semaphore_count, "
                "job_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snap_id, instance_id, db_count, db_total_conn, db_app_conn,
                 db_rss_mb, repl_lag, table_count, total_rows, total_data,
                 total_idx, app_count, node_count, sem_count, job_count),
            )

            # Insert table metrics (top 200 by rows to keep DB size reasonable)
            top_tables = sorted(table_agg.items(), key=lambda x: x[1][0], reverse=True)[:200]
            for tname, (rows, db, ib) in top_tables:
                if rows > 0:
                    conn.execute(
                        "INSERT OR REPLACE INTO table_metrics "
                        "(snapshot_id, instance_id, table_name, rows, data_bytes, index_bytes) "
                        "VALUES (?,?,?,?,?,?)",
                        (snap_id, instance_id, tname, rows, db, ib),
                    )

            collected += 1

        # Update snapshot totals
        duration = time.time() - start
        conn.execute(
            "UPDATE snapshots SET collected=?, failed=?, duration_sec=? WHERE id=?",
            (collected, failed, duration, snap_id),
        )

    return snap_id


# ── Query helpers ────────────────────────────────────────────────────────────

def get_snapshots(limit: int = 50) -> List[dict]:
    """Return recent snapshots."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_instance_by_name(name: str) -> Optional[dict]:
    """Lookup instance by name."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def get_instance_history(instance_name: str, limit: int = 30) -> List[dict]:
    """Get metric history for an instance across snapshots."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT s.taken_at, i.name, i.customer, im.* FROM instance_metrics im "
            "JOIN snapshots s ON s.id = im.snapshot_id "
            "JOIN instances i ON i.id = im.instance_id "
            "WHERE i.name = ? ORDER BY s.taken_at DESC LIMIT ?",
            (instance_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_table_history(table_name: str, limit: int = 30) -> List[dict]:
    """Get row count history for a table across all instances and snapshots."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT s.taken_at, i.name as instance, i.customer, "
            "tm.rows, tm.data_bytes, tm.index_bytes "
            "FROM table_metrics tm "
            "JOIN snapshots s ON s.id = tm.snapshot_id "
            "JOIN instances i ON i.id = tm.instance_id "
            "WHERE tm.table_name = ? "
            "ORDER BY tm.rows DESC LIMIT ?",
            (table_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def search_instances(query: str = "", min_rows: int = 0,
                     min_repl_lag: float = -1, tags: List[str] = None,
                     customer: str = "", limit: int = 200) -> List[dict]:
    """
    Flexible query across instances using the latest snapshot.
    """
    with get_db() as conn:
        # Get latest snapshot
        snap = conn.execute(
            "SELECT id FROM snapshots ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
        if not snap:
            return []
        snap_id = snap["id"]

        sql = (
            "SELECT i.name, i.customer, i.used_for, im.* "
            "FROM instance_metrics im "
            "JOIN instances i ON i.id = im.instance_id "
            "WHERE im.snapshot_id = ? "
        )
        params: list = [snap_id]

        if query:
            sql += "AND (i.name LIKE ? OR i.customer LIKE ?) "
            params.extend([f"%{query}%", f"%{query}%"])
        if min_rows > 0:
            sql += "AND im.total_rows >= ? "
            params.append(min_rows)
        if min_repl_lag >= 0:
            sql += "AND im.repl_lag_sec >= ? "
            params.append(min_repl_lag)
        if customer:
            sql += "AND i.customer LIKE ? "
            params.append(f"%{customer}%")
        if tags:
            placeholders = ",".join(["?"] * len(tags))
            sql += (
                f"AND i.id IN (SELECT instance_id FROM tags "
                f"WHERE tag IN ({placeholders})) "
            )
            params.extend(tags)

        sql += "ORDER BY im.total_rows DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def search_tables(query: str = "", min_rows: int = 0,
                  instance: str = "", exact_instance: bool = False,
                  limit: int = 200) -> List[dict]:
    """Search tables across all instances in the latest snapshot."""
    with get_db() as conn:
        snap = conn.execute(
            "SELECT id FROM snapshots ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
        if not snap:
            return []
        snap_id = snap["id"]

        sql = (
            "SELECT i.name as instance, i.customer, "
            "tm.table_name, tm.rows, tm.data_bytes, tm.index_bytes "
            "FROM table_metrics tm "
            "JOIN instances i ON i.id = tm.instance_id "
            "WHERE tm.snapshot_id = ? "
        )
        params: list = [snap_id]

        if query:
            sql += "AND tm.table_name LIKE ? "
            params.append(f"%{query}%")
        if min_rows > 0:
            sql += "AND tm.rows >= ? "
            params.append(min_rows)
        if instance:
            if exact_instance:
                sql += "AND i.name = ? "
                params.append(instance)
            else:
                sql += "AND i.name LIKE ? "
                params.append(f"%{instance}%")

        sql += "ORDER BY tm.rows DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def compare_instances(inst_a: str, inst_b: str) -> dict:
    """Side-by-side comparison of two instances from the latest snapshot."""
    with get_db() as conn:
        snap = conn.execute(
            "SELECT id FROM snapshots ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
        if not snap:
            return {"error": "No snapshots"}
        snap_id = snap["id"]

        def _get(name):
            row = conn.execute(
                "SELECT i.name, i.customer, i.used_for, im.* "
                "FROM instance_metrics im "
                "JOIN instances i ON i.id = im.instance_id "
                "WHERE im.snapshot_id = ? AND i.name = ?",
                (snap_id, name),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            # Get top tables
            tables = conn.execute(
                "SELECT table_name, rows, data_bytes, index_bytes "
                "FROM table_metrics tm "
                "JOIN instances i ON i.id = tm.instance_id "
                "WHERE tm.snapshot_id = ? AND i.name = ? "
                "ORDER BY rows DESC LIMIT 20",
                (snap_id, name),
            ).fetchall()
            d["top_tables"] = [dict(t) for t in tables]
            return d

        return {"a": _get(inst_a), "b": _get(inst_b)}


def get_customer_portfolio(limit: int = 100) -> List[dict]:
    """Aggregate stats grouped by customer from latest snapshot."""
    with get_db() as conn:
        snap = conn.execute(
            "SELECT id FROM snapshots ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
        if not snap:
            return []
        snap_id = snap["id"]

        rows = conn.execute(
            "SELECT i.customer, "
            "COUNT(DISTINCT i.id) as instance_count, "
            "SUM(im.total_rows) as total_rows, "
            "SUM(im.total_data_bytes + im.total_index_bytes) as total_bytes, "
            "SUM(im.db_count) as total_dbs, "
            "AVG(im.health_score) as avg_health, "
            "MAX(im.repl_lag_sec) as max_repl_lag, "
            "SUM(im.table_count) as total_tables "
            "FROM instance_metrics im "
            "JOIN instances i ON i.id = im.instance_id "
            "WHERE im.snapshot_id = ? AND i.customer != '' "
            "GROUP BY i.customer "
            "ORDER BY total_rows DESC LIMIT ?",
            (snap_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Tagging ──────────────────────────────────────────────────────────────────

def add_tag(instance_name: str, tag: str) -> bool:
    with get_db() as conn:
        inst = conn.execute(
            "SELECT id FROM instances WHERE name = ?", (instance_name,)
        ).fetchone()
        if not inst:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO tags (instance_id, tag, created_at) "
            "VALUES (?, ?, ?)",
            (inst["id"], tag, time.time()),
        )
        return True


def remove_tag(instance_name: str, tag: str) -> bool:
    with get_db() as conn:
        inst = conn.execute(
            "SELECT id FROM instances WHERE name = ?", (instance_name,)
        ).fetchone()
        if not inst:
            return False
        conn.execute(
            "DELETE FROM tags WHERE instance_id = ? AND tag = ?",
            (inst["id"], tag),
        )
        return True


def get_tags(instance_name: str) -> List[str]:
    with get_db() as conn:
        inst = conn.execute(
            "SELECT id FROM instances WHERE name = ?", (instance_name,)
        ).fetchone()
        if not inst:
            return []
        rows = conn.execute(
            "SELECT tag FROM tags WHERE instance_id = ? ORDER BY tag",
            (inst["id"],),
        ).fetchall()
        return [r["tag"] for r in rows]


def get_all_tags() -> List[dict]:
    """Return all tags with instance counts."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT tag, COUNT(*) as count FROM tags "
            "GROUP BY tag ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Export ───────────────────────────────────────────────────────────────────

def export_csv(filepath: str, query: str = "", min_rows: int = 0,
               customer: str = "") -> int:
    """Export instance metrics from latest snapshot to CSV. Returns row count."""
    import csv

    results = search_instances(query=query, min_rows=min_rows,
                               customer=customer, limit=50000)
    if not results:
        return 0

    fields = ["name", "customer", "used_for", "db_count", "db_total_conn",
              "db_app_conn", "db_rss_mb", "repl_lag_sec", "table_count",
              "total_rows", "total_data_bytes", "total_index_bytes",
              "app_server_count", "node_count", "semaphore_count",
              "job_count", "health_score"]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    return len(results)


# ── Init on import ───────────────────────────────────────────────────────────

init_db()
