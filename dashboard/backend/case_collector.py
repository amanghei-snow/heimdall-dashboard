"""Collect case data from HI (ServiceNow) and store in Heimdall DB."""
import os
import sys
import time
import requests
from datetime import datetime
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


def collect_cases(lookback_days=365, company_filter=None):
    """Pull cases from HI and upsert into case_records table."""
    if not HI_PASS:
        print("ERROR: Set HI_PASS environment variable")
        sys.exit(1)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Build company→instance mapping from existing Heimdall data
    db = SessionLocal()
    company_map = {}
    for inst in db.query(Instance.company, Instance.instance).all():
        if inst.company:
            company_map[inst.company.strip().lower()] = inst.instance

    auth = (HI_USER, HI_PASS)
    headers = {"Accept": "application/json"}

    # Build date filter
    since = datetime.now().strftime("%Y-%m-%d")
    if lookback_days:
        from datetime import timedelta
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    query = f"opened_at>={since}"
    if company_filter:
        query += f"^account.name={company_filter}"

    offset = 0
    total_inserted = 0
    total_updated = 0

    print(f"Collecting cases from {HI_INSTANCE} (since {since})...")

    while True:
        params = {
            "sysparm_limit": BATCH_SIZE,
            "sysparm_offset": offset,
            "sysparm_fields": ",".join(FIELDS),
            "sysparm_query": query + "^ORDERBYopened_at",
            "sysparm_display_value": "true",
        }

        try:
            resp = requests.get(
                f"{HI_INSTANCE}/api/now/table/{TABLE}",
                auth=auth, headers=headers, params=params, timeout=60
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Request error at offset {offset}: {e}")
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
                total_updated += 1
            else:
                rec = CaseRecord(case_number=case_num, **data)
                db.add(rec)
                total_inserted += 1

        db.commit()
        offset += BATCH_SIZE
        print(f"  Progress: {offset} fetched, {total_inserted} inserted, {total_updated} updated")

    db.close()
    print(f"Done: {total_inserted} inserted, {total_updated} updated")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect case data from HI")
    parser.add_argument("--days", type=int, default=365, help="Lookback days (default 365)")
    parser.add_argument("--company", type=str, default=None, help="Filter to specific company")
    args = parser.parse_args()
    collect_cases(lookback_days=args.days, company_filter=args.company)
