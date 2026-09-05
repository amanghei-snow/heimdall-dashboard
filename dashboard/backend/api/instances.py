"""API routes for instances — paginated, sortable, filterable."""
import time
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc, or_, func, String

from dashboard.backend.database import get_db
from dashboard.backend.models.instance import Instance, TopTable

router = APIRouter(tags=["instances"])

# Simple count cache: {filter_fingerprint: (timestamp, count)}
_count_cache: dict = {}
_COUNT_TTL = 30  # seconds

# Columns allowed for sorting / filtering
SORTABLE_COLS = {
    "rank", "instance", "company", "score", "coverage", "txn_90d", "db_gb_csv",
    "primary_table_gb", "capacity_tier", "db_type", "release_family",
    "db_total_size_gb", "db_count", "table_count", "total_table_rows",
    "total_data_size_gb", "total_index_size_gb", "node_count",
    "app_server_count", "has_ruckus_data", "is_hyperscaler", "datacenter",
}

STR_COLS = {"instance", "company", "capacity_tier", "db_type", "release_family", "datacenter"}
BOOL_COLS = {"is_hyperscaler", "has_ruckus_data"}


@router.get("/instances")
def list_instances(
    tab: str = "overview",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    sort: str = "rank",
    dir: str = "asc",
    q: Optional[str] = None,
    filters: Optional[str] = None,  # JSON-encoded filter list
    db: Session = Depends(get_db),
):
    """Paginated instance list with server-side sort, search, and filters."""
    query = db.query(Instance)

    # Global search (instance or company)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Instance.instance.ilike(like),
            Instance.company.ilike(like),
        ))

    # Column-specific filters (parsed from JSON string)
    if filters:
        import json
        try:
            filter_list = json.loads(filters)
            for f in filter_list:
                col = f.get("col")
                op = f.get("op")
                val = f.get("val")
                if col not in SORTABLE_COLS or op is None:
                    continue
                col_attr = getattr(Instance, col, None)
                if col_attr is None:
                    continue
                if col in BOOL_COLS:
                    bool_val = val.lower() in ("true", "1", "yes", "y") if isinstance(val, str) else bool(val)
                    if op == "=":
                        query = query.filter(col_attr == bool_val)
                    elif op == "!=":
                        query = query.filter(col_attr != bool_val)
                    continue
                if col in STR_COLS:
                    if op == "=":
                        query = query.filter(col_attr == val)
                    elif op == "!=":
                        query = query.filter(col_attr != val)
                    elif op == "contains":
                        query = query.filter(col_attr.ilike(f"%{val}%"))
                else:
                    # For numeric cols with 'contains' (from column search bar),
                    # cast to string and do LIKE match so users can type partial numbers
                    if op == "contains":
                        query = query.filter(
                            func.cast(col_attr, String).ilike(f"%{val}%")
                        )
                    else:
                        num_val = float(val) if val else 0
                        if op == "=":
                            query = query.filter(col_attr == num_val)
                        elif op == "!=":
                            query = query.filter(col_attr != num_val)
                        elif op == ">=":
                            query = query.filter(col_attr >= num_val)
                        elif op == "<=":
                            query = query.filter(col_attr <= num_val)
                        elif op == ">":
                            query = query.filter(col_attr > num_val)
                        elif op == "<":
                            query = query.filter(col_attr < num_val)
        except (json.JSONDecodeError, TypeError):
            pass

    # Total count (cached to avoid full index scan every request)
    cache_key = f"{q}|{filters}"
    now = time.time()
    cached = _count_cache.get(cache_key)
    if cached and (now - cached[0]) < _COUNT_TTL:
        total = cached[1]
    else:
        total = query.count()
        _count_cache[cache_key] = (now, total)

    # Sort
    sort_col = sort if sort in SORTABLE_COLS else "rank"
    order_fn = asc if dir == "asc" else desc
    # For db_gb_csv, sort on the pre-computed db_size_best column
    if sort_col == "db_gb_csv":
        col_attr = Instance.db_size_best
    else:
        col_attr = getattr(Instance, sort_col)
    query = query.order_by(order_fn(col_attr))

    # Paginate
    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "data": [_serialize_instance(r) for r in rows],
    }


@router.get("/instances/{instance_name}/tables")
def get_top_tables(
    instance_name: str,
    sort: str = "rows",
    dir: str = "desc",
    db: Session = Depends(get_db),
):
    """Lazy-load top tables for a specific instance."""
    inst = db.query(Instance).filter(Instance.instance == instance_name).first()
    if not inst:
        return {"tables": []}

    query = db.query(TopTable).filter(TopTable.instance_id == inst.id)

    sort_map = {"name": TopTable.name, "rows": TopTable.rows,
                "data_length": TopTable.data_length, "index_length": TopTable.index_length}
    sort_attr = sort_map.get(sort, TopTable.rows)
    order_fn = asc if dir == "asc" else desc
    query = query.order_by(order_fn(sort_attr))

    tables = query.limit(20).all()
    return {
        "tables": [
            {"name": t.name, "rows": t.rows,
             "data_length": t.data_length, "index_length": t.index_length}
            for t in tables
        ]
    }


@router.get("/instances/{instance_name}/history")
def get_instance_history(instance_name: str, db: Session = Depends(get_db)):
    """Deep audit: return per-run metric timeline for an instance."""
    from dashboard.backend.models.instance import AggregatorSnapshot, AggregatorRun

    rows = (
        db.query(
            AggregatorRun.id.label("run_id"),
            AggregatorRun.timestamp,
            AggregatorSnapshot.node_count,
            AggregatorSnapshot.table_count,
            AggregatorSnapshot.db_gb_csv,
            AggregatorSnapshot.txn_90d,
            AggregatorSnapshot.total_table_rows,
            AggregatorSnapshot.db_total_size_gb,
            AggregatorSnapshot.total_data_size_gb,
            AggregatorSnapshot.total_index_size_gb,
            AggregatorSnapshot.score,
        )
        .join(AggregatorRun, AggregatorRun.id == AggregatorSnapshot.run_id)
        .filter(AggregatorSnapshot.instance == instance_name)
        .order_by(AggregatorRun.timestamp)
        .all()
    )

    if not rows:
        # Fallback: try local SQLite history.db for backwards compatibility
        return _get_history_from_sqlite(instance_name)

    # Best-available DB size: prefer CSV, fall back to data+index
    processed = []
    for r in rows:
        d = dict(r._mapping)
        db_gb = d.get("db_gb_csv") or 0
        if not db_gb:
            db_gb = round((d.get("total_data_size_gb") or 0) + (d.get("total_index_size_gb") or 0), 2)
        d["db_gb_csv"] = db_gb
        processed.append(d)

    # Deduplicate: keep the LAST snapshot per date (most recent run)
    date_map = {}
    for d in processed:
        date = d["timestamp"][:10]
        date_map[date] = d
    timeline = [date_map[k] for k in sorted(date_map)]

    # Collapse consecutive entries where all tracked metrics are identical
    TRACK = ["node_count", "table_count", "db_gb_csv", "txn_90d",
             "total_table_rows", "db_total_size_gb", "score"]
    collapsed = [timeline[0]] if timeline else []
    for entry in timeline[1:]:
        prev = collapsed[-1]
        if all((entry.get(k) or 0) == (prev.get(k) or 0) for k in TRACK):
            continue
        collapsed.append(entry)
    timeline = collapsed

    # Compute deltas between consecutive entries
    for i in range(1, len(timeline)):
        prev = timeline[i - 1]
        curr = timeline[i]
        for key in TRACK:
            curr[f"d_{key}"] = (curr.get(key) or 0) - (prev.get(key) or 0)

    return {"instance": instance_name, "runs": timeline}


def _get_history_from_sqlite(instance_name: str):
    """Fallback: read history from local SQLite file (for local dev)."""
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).resolve().parent.parent.parent.parent / "data" / "history.db"
    if not db_path.exists():
        return {"runs": [], "instance": instance_name}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT r.id as run_id, r.timestamp,
                   s.node_count, s.table_count, s.db_gb_csv, s.txn_90d,
                   s.total_table_rows, s.db_total_size_gb,
                   s.total_data_size_gb, s.total_index_size_gb, s.score
            FROM aggregator_snapshots s
            JOIN aggregator_runs r ON r.id = s.run_id
            WHERE s.instance = ?
            ORDER BY r.timestamp
        """, (instance_name,)).fetchall()

        processed = []
        for r in rows:
            d = dict(r)
            db_gb = d.get("db_gb_csv") or 0
            if not db_gb:
                db_gb = round((d.get("total_data_size_gb") or 0) + (d.get("total_index_size_gb") or 0), 2)
            d["db_gb_csv"] = db_gb
            processed.append(d)

        date_map = {}
        for d in processed:
            date = d["timestamp"][:10]
            date_map[date] = d
        timeline = [date_map[k] for k in sorted(date_map)]

        TRACK = ["node_count", "table_count", "db_gb_csv", "txn_90d",
                 "total_table_rows", "db_total_size_gb", "score"]
        collapsed = [timeline[0]] if timeline else []
        for entry in timeline[1:]:
            prev = collapsed[-1]
            if all((entry.get(k) or 0) == (prev.get(k) or 0) for k in TRACK):
                continue
            collapsed.append(entry)
        timeline = collapsed

        for i in range(1, len(timeline)):
            prev = timeline[i - 1]
            curr = timeline[i]
            for key in TRACK:
                curr[f"d_{key}"] = (curr.get(key) or 0) - (prev.get(key) or 0)

        return {"instance": instance_name, "runs": timeline}
    finally:
        conn.close()


def _serialize_instance(r: Instance) -> dict:
    """Convert Instance ORM object to JSON-serializable dict.

    BUG-015: When CSV-sourced fields (db_gb_csv, txn_90d) are zero but
    ruckus-collected data exists, the dashboard showed contradictory values
    across tabs (Overview showed 0, Tables showed real data). We now compute
    best-available values so all tabs are consistent.
    """
    # Best-available DB size: prefer CSV, fallback to actual table data+index
    db_gb = r.db_gb_csv
    if (not db_gb or db_gb == 0):
        db_gb = round((r.total_data_size_gb or 0) + (r.total_index_size_gb or 0), 2)

    return {
        "rank": r.rank,
        "instance": r.instance,
        "is_hyperscaler": r.is_hyperscaler or False,
        "datacenter": r.datacenter or "",
        "company": r.company,
        "score": r.score,
        "coverage": r.coverage,
        "txn_90d": r.txn_90d,
        "db_gb_csv": db_gb,
        "db_gb_csv_raw": r.db_gb_csv,
        "primary_table_gb": r.primary_table_gb,
        "capacity_tier": r.capacity_tier,
        "db_type": r.db_type,
        "release_family": r.release_family,
        "db_total_size_gb": r.db_total_size_gb,
        "db_count": r.db_count,
        "table_count": r.table_count,
        "total_table_rows": r.total_table_rows,
        "total_data_size_gb": r.total_data_size_gb,
        "total_index_size_gb": r.total_index_size_gb,
        "node_count": r.node_count,
        "app_server_count": r.app_server_count,
        "has_ruckus_data": r.has_ruckus_data,
        "d_total_table_rows": r.d_total_table_rows,
        "d_table_count": r.d_table_count,
        "d_db_total_size_gb": r.d_db_total_size_gb,
        "d_node_count": r.d_node_count,
        "d_txn_90d": r.d_txn_90d,
        "d_db_gb_csv": r.d_db_gb_csv,
        "d_score": r.d_score,
        "has_delta": r.has_delta,
    }
