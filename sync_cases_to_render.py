"""Sync case_records from local MariaDB to Render PostgreSQL."""
import os
import sys
from sqlalchemy import create_engine, text
import psycopg2
import psycopg2.extras

LOCAL_URL = os.getenv("DATABASE_URL", "mysql+pymysql://ruckus:ruckus@localhost:3306/ruckus_aggregator")
RENDER_URL = os.getenv(
    "RENDER_DATABASE_URL",
    "postgresql://heimdall:F8PKD2ASPB2PDsgvgfa9oplTXUGgLmW4@dpg-dad12p710e5c73cs6qgg-a.oregon-postgres.render.com/heimdall_p8ps",
)

COLUMNS = [
    "case_number", "account_name", "instance", "priority", "state",
    "category", "subcategory", "case_type", "case_category",
    "contact_type", "closure_code", "escalated", "opened_at",
    "closed_at", "resolved_at",
]


def serialize_row(row):
    d = {col: row[col] for col in COLUMNS}
    # MySQL returns escalated as int 0/1; Postgres expects boolean
    if d["escalated"] is not None:
        d["escalated"] = bool(int(d["escalated"]))
    return d


def sync(batch_size=5000):
    local = create_engine(LOCAL_URL)

    # Parse Render URL for psycopg2 direct connection
    pg_conn = psycopg2.connect(RENDER_URL)
    pg_cur = pg_conn.cursor()

    total = 0

    col_list = ", ".join(COLUMNS)
    updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "case_number"])
    upsert_sql = (
        f"INSERT INTO case_records ({col_list}) VALUES %s "
        f"ON CONFLICT (case_number) DO UPDATE SET {updates}"
    )

    with local.connect().execution_options(stream_results=True) as src:
        count = src.execute(text("SELECT count(*) FROM case_records")).scalar()
        print(f"Local cases: {count}", flush=True)

        result = src.execute(text(f"SELECT {', '.join(COLUMNS)} FROM case_records"))
        batch = []

        def flush():
            if not batch:
                return
            values = [tuple(row[c] for c in COLUMNS) for row in batch]
            psycopg2.extras.execute_values(pg_cur, upsert_sql, values, page_size=len(values))
            pg_conn.commit()
            batch.clear()

        for row in result.mappings():
            batch.append(serialize_row(row))
            total += 1
            if len(batch) >= batch_size:
                flush()
                print(f"  synced {total} / {count}", flush=True)
        flush()

    pg_cur.close()
    pg_conn.close()
    print(f"Done: {total} rows processed")


if __name__ == "__main__":
    sync()
