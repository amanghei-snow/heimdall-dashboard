"""Migrate history data from local SQLite (history.db) to PostgreSQL on Render."""
import os
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, ".")

# Override DATABASE_URL before importing engine
RENDER_URL = os.environ.get("DATABASE_URL")
if not RENDER_URL:
    print("Usage: DATABASE_URL='postgresql://...' python seed_history.py")
    sys.exit(1)

os.environ["DATABASE_URL"] = RENDER_URL

from dashboard.backend.database import engine, SessionLocal, Base
from dashboard.backend.models.instance import AggregatorRun, AggregatorSnapshot

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

# Read from SQLite
db_path = Path("data/history.db")
if not db_path.exists():
    print(f"SQLite file not found: {db_path}")
    sys.exit(1)

sqlite_conn = sqlite3.connect(str(db_path))
sqlite_conn.row_factory = sqlite3.Row

# Migrate runs
runs = sqlite_conn.execute("SELECT * FROM aggregator_runs ORDER BY id").fetchall()
print(f"Found {len(runs)} aggregator runs")

# Migrate snapshots
snapshots = sqlite_conn.execute("SELECT * FROM aggregator_snapshots ORDER BY id").fetchall()
print(f"Found {len(snapshots)} aggregator snapshots")

db = SessionLocal()
try:
    # Check if already seeded
    existing = db.query(AggregatorRun).count()
    if existing > 0:
        print(f"Already have {existing} runs in PostgreSQL. Skipping.")
        sys.exit(0)

    # Insert runs
    run_id_map = {}
    for r in runs:
        run = AggregatorRun(
            timestamp=r["timestamp"],
            top_n=r["top_n"] or 0,
            instance_count=r["instance_count"] or 0,
        )
        db.add(run)
        db.flush()
        run_id_map[r["id"]] = run.id

    db.commit()
    print(f"Inserted {len(run_id_map)} runs")

    # Insert snapshots in batches
    batch = []
    for i, s in enumerate(snapshots):
        new_run_id = run_id_map.get(s["run_id"])
        if not new_run_id:
            continue
        snap = AggregatorSnapshot(
            run_id=new_run_id,
            instance=s["instance"],
            total_table_rows=s["total_table_rows"] or 0,
            table_count=s["table_count"] or 0,
            db_total_size_gb=s["db_total_size_gb"] or 0.0,
            total_data_size_gb=s["total_data_size_gb"] or 0.0,
            total_index_size_gb=s["total_index_size_gb"] or 0.0,
            node_count=s["node_count"] or 0,
            txn_90d=s["txn_90d"] or 0.0,
            db_gb_csv=s["db_gb_csv"] or 0.0,
            score=s["score"] or 0.0,
        )
        db.add(snap)

        if (i + 1) % 5000 == 0:
            db.commit()
            print(f"  Progress: {i + 1}/{len(snapshots)} snapshots")

    db.commit()
    print(f"Inserted {len(snapshots)} snapshots")
    print("History migration complete!")

finally:
    db.close()
    sqlite_conn.close()
