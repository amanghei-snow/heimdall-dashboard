"""Recover top_tables from history.db view_snapshots for instances missing them."""
import sqlite3
import json
import os

from dashboard.backend.database import SessionLocal
from dashboard.backend.models.instance import Instance, TopTable
from sqlalchemy import func

HISTORY_DB = os.path.join("data", "history.db")

def main():
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    db = SessionLocal()

    # Find all instances in MariaDB that have 0 top_tables
    empty = (
        db.query(Instance.id, Instance.instance)
        .outerjoin(TopTable)
        .group_by(Instance.id)
        .having(func.count(TopTable.id) == 0)
        .all()
    )
    print(f"Instances with 0 top_tables: {len(empty)}")

    recovered = 0
    for inst_id, inst_name in empty:
        row = conn.execute(
            "SELECT vs.data_json, vs.item_count "
            "FROM view_snapshots vs "
            "JOIN analysis_runs ar ON ar.id = vs.run_id "
            "WHERE ar.instance = ? AND vs.view_key = 'PsqlDatabaseTableView' AND vs.item_count > 0 "
            "ORDER BY ar.timestamp DESC LIMIT 1",
            (inst_name,),
        ).fetchone()

        if not row or row["item_count"] == 0:
            continue

        try:
            tables = json.loads(row["data_json"])
            if not isinstance(tables, list):
                continue
        except Exception:
            continue

        # Deduplicate by table name, keep highest row count
        seen = {}
        for t in tables:
            name = t.get("name", "")
            rows = t.get("rows", 0) or 0
            if name not in seen or rows > (seen[name].get("rows", 0) or 0):
                seen[name] = t
        top = sorted(seen.values(), key=lambda t: t.get("rows", 0) or 0, reverse=True)[:20]
        for t in top:
            db.add(TopTable(
                instance_id=inst_id,
                name=t.get("name", ""),
                rows=t.get("rows", 0) or 0,
                data_length=t.get("dataLength", 0) or 0,
                index_length=t.get("indexLength", 0) or 0,
            ))
        recovered += 1

    db.commit()
    db.close()
    conn.close()
    print(f"Recovered top_tables for {recovered} instances from history DB")

if __name__ == "__main__":
    main()
