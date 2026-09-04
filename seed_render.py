"""Export local MariaDB data and seed Render PostgreSQL via the migrate endpoint."""
import json
import sys
sys.path.insert(0, ".")

from dashboard.backend.database import engine
from sqlalchemy import text

print("Exporting instances from local MariaDB...")
rows = []
with engine.connect() as conn:
    result = conn.execute(text("SELECT * FROM instances"))
    columns = list(result.keys())
    for row in result:
        d = dict(zip(columns, row))
        # Convert non-serializable types
        for k, v in d.items():
            if hasattr(v, 'isoformat'):
                d[k] = v.isoformat()
        rows.append(d)

    # Get top tables
    for inst in rows:
        tables = conn.execute(
            text("SELECT name, `rows`, data_length, index_length FROM top_tables WHERE instance_id = :iid"),
            {"iid": inst["id"]}
        ).fetchall()
        inst["top_tables"] = [
            {"name": t[0], "rows": t[1], "data_length": t[2], "index_length": t[3]}
            for t in tables
        ]

out_path = "output/render_seed_data.json"
with open(out_path, "w") as f:
    json.dump(rows, f)

print(f"Exported {len(rows)} instances to {out_path}")
print(f"File size: {len(json.dumps(rows)) / 1024 / 1024:.1f} MB")
print()
print("Now run the migration against Render:")
print("  DATABASE_URL='<render_postgres_url>' .venv/bin/python3 -m dashboard.backend.migrate_data --json-path output/render_seed_data.json")
