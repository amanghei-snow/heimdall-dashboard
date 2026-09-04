"""Migrate existing ruckus_aggregated_data.json into MariaDB.

Usage:
    python -m dashboard.backend.migrate_data [--json-path output/ruckus_aggregated_data.json]
"""
import json
import argparse
from pathlib import Path

from dashboard.backend.database import engine, SessionLocal, Base
from dashboard.backend.models.instance import Instance, TopTable


def migrate(json_path: str):
    """Load aggregated JSON and upsert into the instances + top_tables tables."""
    Base.metadata.create_all(bind=engine)

    with open(json_path) as f:
        data = json.load(f)

    instances = data if isinstance(data, list) else data.get("instances", data.get("data", []))
    print(f"Loaded {len(instances)} instances from {json_path}")

    db = SessionLocal()
    try:
        inserted = 0
        updated = 0
        for row in instances:
            inst_name = row.get("instance")
            if not inst_name:
                continue

            existing = db.query(Instance).filter(Instance.instance == inst_name).first()
            if existing:
                # Update all fields
                for key in [
                    "rank", "company", "score", "coverage", "txn_90d", "db_gb_csv",
                    "primary_table_gb", "capacity_tier", "db_type", "release_patch",
                    "release_family", "db_total_size_gb", "db_count", "table_count",
                    "total_table_rows", "total_data_size_gb", "total_index_size_gb",
                    "node_count", "app_server_count", "has_ruckus_data", "used_fallback",
                    "d_total_table_rows", "d_table_count", "d_db_total_size_gb",
                    "d_node_count", "d_txn_90d", "d_db_gb_csv", "d_score", "has_delta",
                ]:
                    if key in row:
                        setattr(existing, key, row[key])

                # Materialized best DB size for indexed sorting
                csv_gb = existing.db_gb_csv or 0
                existing.db_size_best = csv_gb if csv_gb else round((existing.total_data_size_gb or 0) + (existing.total_index_size_gb or 0), 2)

                # Replace top tables only if new data is available — preserve
                # existing tables when collection failed and JSON has none.
                new_tables = row.get("top_tables", [])
                if new_tables:
                    db.query(TopTable).filter(TopTable.instance_id == existing.id).delete()
                    _add_top_tables(db, existing.id, new_tables)
                updated += 1
            else:
                inst = Instance(
                    instance=inst_name,
                    rank=row.get("rank", 0),
                    company=row.get("company", ""),
                    score=row.get("score", 0.0),
                    coverage=row.get("coverage", 0.0),
                    txn_90d=row.get("txn_90d", 0.0),
                    db_gb_csv=row.get("db_gb_csv", 0.0),
                    primary_table_gb=row.get("primary_table_gb", 0.0),
                    capacity_tier=row.get("capacity_tier", ""),
                    db_type=row.get("db_type", ""),
                    release_patch=row.get("release_patch", ""),
                    release_family=row.get("release_family", ""),
                    db_total_size_gb=row.get("db_total_size_gb", 0.0),
                    db_count=row.get("db_count", 0),
                    table_count=row.get("table_count", 0),
                    total_table_rows=row.get("total_table_rows", 0),
                    total_data_size_gb=row.get("total_data_size_gb", 0.0),
                    total_index_size_gb=row.get("total_index_size_gb", 0.0),
                    node_count=row.get("node_count", 0),
                    app_server_count=row.get("app_server_count", 0),
                    has_ruckus_data=row.get("has_ruckus_data", False),
                    used_fallback=row.get("used_fallback", False),
                    d_total_table_rows=row.get("d_total_table_rows"),
                    d_table_count=row.get("d_table_count"),
                    d_db_total_size_gb=row.get("d_db_total_size_gb"),
                    d_node_count=row.get("d_node_count"),
                    d_txn_90d=row.get("d_txn_90d"),
                    d_db_gb_csv=row.get("d_db_gb_csv"),
                    d_score=row.get("d_score"),
                    has_delta=row.get("has_delta", False),
                )
                # Materialized best DB size for indexed sorting
                csv_gb = inst.db_gb_csv or 0
                inst.db_size_best = csv_gb if csv_gb else round((inst.total_data_size_gb or 0) + (inst.total_index_size_gb or 0), 2)
                db.add(inst)
                db.flush()
                _add_top_tables(db, inst.id, row.get("top_tables", []))
                inserted += 1

            # Commit in batches of 500
            if (inserted + updated) % 500 == 0:
                db.commit()
                print(f"  Progress: {inserted} inserted, {updated} updated")

        db.commit()
        print(f"Migration complete: {inserted} inserted, {updated} updated")

    finally:
        db.close()


def _add_top_tables(db, instance_id: int, tables: list):
    """Insert top_tables rows for an instance, deduped by table name."""
    seen = {}
    for t in tables:
        name = t.get("name", "")
        rows = t.get("rows", 0) or 0
        if name not in seen or rows > (seen[name].get("rows", 0) or 0):
            seen[name] = t
    top = sorted(seen.values(), key=lambda t: t.get("rows", 0) or 0, reverse=True)[:20]
    for t in top:
        db.add(TopTable(
            instance_id=instance_id,
            name=t.get("name", ""),
            rows=t.get("rows", 0) or 0,
            data_length=t.get("data_length", 0) or 0,
            index_length=t.get("index_length", 0) or 0,
        ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate JSON data to MariaDB")
    parser.add_argument(
        "--json-path",
        default="output/ruckus_aggregated_data.json",
        help="Path to the aggregated JSON file",
    )
    args = parser.parse_args()
    migrate(args.json_path)
