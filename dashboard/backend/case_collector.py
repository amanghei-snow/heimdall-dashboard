"""Collect case data from HI (ServiceNow) and store in Heimdall DB."""
import os
import sys
import time
import requests
from datetime import datetime, timedelta
from calendar import monthrange
from sqlalchemy import text, inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dashboard.backend.database import SessionLocal, Base, engine
from dashboard.backend.models.instance import CaseRecord, Instance

# HI credentials
HI_INSTANCE = os.environ.get("HI_INSTANCE", "https://hiupgtest.service-now.com")
HI_USER = os.environ.get("HI_USER", "heimdall.int")
HI_PASS = os.environ.get("HI_PASS", "")

BATCH_SIZE = 1000
TABLE = "sn_customerservice_case"
FIELDS = [
    "number", "account.name", "priority", "state", "category", "subcategory",
    "u_case_type", "u_case_category", "contact_type", "u_closure_code",
    "u_escalated", "opened_at", "closed_at", "resolved_at",
]


def _parse_dt(val):
    """Parse SN datetime string to Python datetime."""
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _display_val(v):
    """Extract display value from SN field (may be dict or string)."""
    if isinstance(v, dict):
        return v.get("display_value", "") or ""
    return v or ""


def _month_bounds(year, month):
    """Return first and last day of a given month as YYYY-MM-DD strings."""
    first = datetime(year, month, 1)
    _, last_day = monthrange(year, month)
    last = datetime(year, month, last_day)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


UPSERT_COLS = [
    "case_number", "account_name", "instance", "priority", "state",
    "category", "subcategory", "case_type", "case_category",
    "contact_type", "closure_code", "escalated", "opened_at",
    "closed_at", "resolved_at",
]


def _build_upsert_sql(dialect):
    """Build a dialect-aware bulk upsert statement."""
    col_list = ", ".join(UPSERT_COLS)
    placeholders = ", ".join([f":{c}" for c in UPSERT_COLS])
    update_cols = [c for c in UPSERT_COLS if c != "case_number"]

    if dialect == "postgresql":
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
        return text(
            f"INSERT INTO case_records ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (case_number) DO UPDATE SET {updates}"
        )
    else:
        # MySQL / MariaDB
        updates = ", ".join([f"{c} = VALUES({c})" for c in update_cols])
        return text(
            f"INSERT INTO case_records ({col_list}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )


def _parse_record(r, company_map):
    """Parse a single ServiceNow result dict into an upsert-ready dict."""
    account = _display_val(r.get("account.name", ""))
    case_num = r.get("number", "")
    if not case_num or not account:
        return None
    return {
        "case_number": case_num,
        "account_name": account,
        "instance": company_map.get(account.strip().lower()),
        "priority": _display_val(r.get("priority", "")),
        "state": _display_val(r.get("state", "")),
        "category": _display_val(r.get("category", "")),
        "subcategory": _display_val(r.get("subcategory", "")),
        "case_type": _display_val(r.get("u_case_type", "")),
        "case_category": _display_val(r.get("u_case_category", "")),
        "contact_type": _display_val(r.get("contact_type", "")),
        "closure_code": _display_val(r.get("u_closure_code", "")),
        "escalated": _display_val(r.get("u_escalated", "")) == "true",
        "opened_at": _parse_dt(r.get("opened_at", "")),
        "closed_at": _parse_dt(r.get("closed_at", "")),
        "resolved_at": _parse_dt(r.get("resolved_at", "")),
    }


def _fetch_window(db, auth, headers, company_map, company_filter, start, end,
                  total_inserted, total_updated, upsert_stmt):
    """Fetch one calendar window from ServiceNow and bulk-upsert records."""
    query = f"opened_at>={start}^opened_at<={end}"
    if company_filter:
        query += f"^account.name={company_filter}"
    query += "^ORDERBYopened_at"

    offset = 0
    window_count = 0
    retries = 0

    while True:
        params = {
            "sysparm_limit": BATCH_SIZE,
            "sysparm_offset": offset,
            "sysparm_fields": ",".join(FIELDS),
            "sysparm_query": query,
            "sysparm_display_value": "true",
        }

        try:
            resp = requests.get(
                f"{HI_INSTANCE}/api/now/table/{TABLE}",
                auth=auth, headers=headers, params=params, timeout=120
            )
            resp.raise_for_status()
            retries = 0
        except requests.RequestException as e:
            retries += 1
            wait = min(retries * 5, 60)
            print(f"  Request error at {start}..{end} offset {offset}: {e}  (retry in {wait}s)")
            time.sleep(wait)
            if retries > 10:
                print(f"  GIVING UP on window {start}..{end} after {retries} retries")
                break
            continue

        results = resp.json().get("result", [])
        if not results:
            break

        # Parse all records in this page
        batch = []
        for r in results:
            rec = _parse_record(r, company_map)
            if rec:
                batch.append(rec)

        # Bulk upsert
        if batch:
            conn = db.get_bind().connect()
            conn.execute(upsert_stmt, batch)
            conn.commit()
            conn.close()
            window_count += len(batch)
            total_inserted[0] += len(batch)

        offset += BATCH_SIZE
        print(f"    {start}..{end}: offset {offset}, window total {window_count}", flush=True)

    print(f"  Window {start}..{end}: {window_count} upserted", flush=True)


def collect_cases(lookback_days=365, company_filter=None, all_time=False, start_date=None, end_date=None, month_window=True):
    """Pull cases from HI and upsert into case_records table.

    Supports calendar-month windowing to avoid large sysparm_offset values and to
    gracefully fetch multi-year data. Set all_time=True to fetch everything.
    """
    if not HI_PASS:
        print("ERROR: Set HI_PASS environment variable")
        sys.exit(1)

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    company_map = {}
    for inst in db.query(Instance.company, Instance.instance).all():
        if inst.company:
            company_map[inst.company.strip().lower()] = inst.instance

    auth = (HI_USER, HI_PASS)
    headers = {"Accept": "application/json"}

    # Build dialect-aware upsert statement
    dialect = engine.dialect.name
    upsert_stmt = _build_upsert_sql(dialect)
    print(f"Using {dialect} dialect for bulk upserts", flush=True)

    # Determine date range
    now = datetime.now()
    if all_time:
        start = datetime(2010, 1, 1)
        end = now
    elif start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    elif lookback_days is not None and lookback_days > 0:
        start = now - timedelta(days=lookback_days)
        end = now
    else:
        start = now - timedelta(days=365)
        end = now

    total_inserted = [0]
    total_updated = [0]

    if not month_window:
        _fetch_window(db, auth, headers, company_map, company_filter,
                      start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                      total_inserted, total_updated, upsert_stmt)
    else:
        print(f"Collecting cases from {HI_INSTANCE} ({start.date()} to {end.date()}) by month...", flush=True)
        cursor = start.replace(day=1)
        while cursor <= end:
            window_start, window_end = _month_bounds(cursor.year, cursor.month)
            if cursor.year == end.year and cursor.month == end.month:
                window_end = end.strftime("%Y-%m-%d")
            if datetime.strptime(window_start, "%Y-%m-%d") > end:
                break
            _fetch_window(db, auth, headers, company_map, company_filter,
                          window_start, window_end, total_inserted, total_updated, upsert_stmt)
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

    db.close()
    print(f"Done: {total_inserted[0]} records upserted", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect case data from HI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=365, help="Lookback days (default 365)")
    group.add_argument("--all", dest="all_time", action="store_true", help="Collect all available cases by month")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--company", type=str, default=None, help="Filter to specific company")
    parser.add_argument("--db", type=str, default=None,
                        help="Override DATABASE_URL (e.g. point directly at Render PostgreSQL)")
    args = parser.parse_args()

    # Allow overriding the database target at runtime
    if args.db:
        os.environ["DATABASE_URL"] = args.db
        # Re-initialize engine with new URL
        from importlib import reload
        import dashboard.backend.database as db_mod
        reload(db_mod)
        from dashboard.backend.database import SessionLocal as _SL, Base as _B, engine as _E
        globals().update({"SessionLocal": _SL, "Base": _B, "engine": _E})

    start_date = args.start
    end_date = args.end
    all_time = args.all_time
    lookback_days = None if all_time else args.days
    if start_date or end_date:
        all_time = False
        lookback_days = None

    collect_cases(
        lookback_days=lookback_days,
        company_filter=args.company,
        all_time=all_time,
        start_date=start_date,
        end_date=end_date,
    )
