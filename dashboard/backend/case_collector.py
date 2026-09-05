"""Collect case data from HI (ServiceNow) and store in Heimdall DB."""
import os
import sys
import time
import requests
from datetime import datetime, timedelta
from calendar import monthrange
from sqlalchemy import text

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


def _fetch_window(db, auth, headers, company_map, company_filter, start, end, total_inserted, total_updated):
    """Fetch one calendar window from ServiceNow and upsert records."""
    query = f"opened_at>={start}^opened_at<={end}"
    if company_filter:
        query += f"^account.name={company_filter}"
    query += "^ORDERBYopened_at"

    offset = 0
    window_inserted = 0
    window_updated = 0

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
        except requests.RequestException as e:
            print(f"  Request error at window {start}..{end} offset {offset}: {e}")
            time.sleep(5)
            continue

        results = resp.json().get("result", [])
        if not results:
            break

        for r in results:
            account = _display_val(r.get("account.name", ""))
            case_num = r.get("number", "")
            if not case_num or not account:
                continue

            # Map account to instance
            inst_name = company_map.get(account.strip().lower())

            existing = db.query(CaseRecord).filter_by(case_number=case_num).first()
            data = dict(
                account_name=account,
                instance=inst_name,
                priority=_display_val(r.get("priority", "")),
                state=_display_val(r.get("state", "")),
                category=_display_val(r.get("category", "")),
                subcategory=_display_val(r.get("subcategory", "")),
                case_type=_display_val(r.get("u_case_type", "")),
                case_category=_display_val(r.get("u_case_category", "")),
                contact_type=_display_val(r.get("contact_type", "")),
                closure_code=_display_val(r.get("u_closure_code", "")),
                escalated=_display_val(r.get("u_escalated", "")) == "true",
                opened_at=_parse_dt(r.get("opened_at", "")),
                closed_at=_parse_dt(r.get("closed_at", "")),
                resolved_at=_parse_dt(r.get("resolved_at", "")),
            )

            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
                total_updated[0] += 1
                window_updated += 1
            else:
                rec = CaseRecord(case_number=case_num, **data)
                db.add(rec)
                total_inserted[0] += 1
                window_inserted += 1

        db.commit()
        offset += BATCH_SIZE
        print(f"    {start}..{end}: offset {offset}, +{window_inserted}/~{window_inserted + window_updated}")

    print(f"  Window {start}..{end}: {window_inserted} inserted, {window_updated} updated")


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
        # default 365 days
        start = now - timedelta(days=365)
        end = now

    total_inserted = [0]
    total_updated = [0]

    if not month_window:
        # Single query approach (legacy)
        _fetch_window(db, auth, headers, company_map, company_filter,
                      start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                      total_inserted, total_updated)
    else:
        print(f"Collecting cases from {HI_INSTANCE} ({start.date()} to {end.date()}) by month...")
        cursor = start.replace(day=1)
        while cursor <= end:
            window_start, window_end = _month_bounds(cursor.year, cursor.month)
            # Clamp to requested end
            if cursor.year == end.year and cursor.month == end.month:
                window_end = end.strftime("%Y-%m-%d")
            if datetime.strptime(window_start, "%Y-%m-%d") > end:
                break
            _fetch_window(db, auth, headers, company_map, company_filter,
                          window_start, window_end, total_inserted, total_updated)
            # next month
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)

    db.close()
    print(f"Done: {total_inserted[0]} inserted, {total_updated[0]} updated")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect case data from HI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=365, help="Lookback days (default 365)")
    group.add_argument("--all", dest="all_time", action="store_true", help="Collect all available cases by month")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--company", type=str, default=None, help="Filter to specific company")
    args = parser.parse_args()

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
