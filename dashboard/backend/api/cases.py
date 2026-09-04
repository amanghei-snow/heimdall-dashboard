"""API endpoints for case data analysis."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, case as sql_case, and_

from dashboard.backend.database import get_db, engine
from dashboard.backend.models.instance import CaseRecord

router = APIRouter(tags=["cases"])

_is_pg = engine.dialect.name == "postgresql"


def _month_expr(col):
    """Return a month-truncation expression compatible with MySQL and PostgreSQL."""
    if _is_pg:
        return func.date_trunc("month", col)
    return func.date_format(col, "%Y-%m")


@router.get("/cases/summary")
def case_summary(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Overall case summary: total, open, by priority, escalated."""
    query = db.query(CaseRecord)
    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    total = query.count()
    if total == 0:
        return {"total": 0, "open": 0, "closed": 0, "escalated": 0,
                "priority": {}, "by_state": {}}

    open_count = query.filter(CaseRecord.state.notin_(["Closed", "Resolved", "Cancelled"])).count()
    closed_count = query.filter(CaseRecord.state.in_(["Closed", "Resolved"])).count()
    escalated = query.filter(CaseRecord.escalated == True).count()

    # Priority breakdown
    priority_rows = (
        query.with_entities(CaseRecord.priority, func.count())
        .group_by(CaseRecord.priority)
        .all()
    )
    priority = {r[0] or "Unknown": r[1] for r in priority_rows}

    # State breakdown
    state_rows = (
        query.with_entities(CaseRecord.state, func.count())
        .group_by(CaseRecord.state)
        .all()
    )
    by_state = {r[0] or "Unknown": r[1] for r in state_rows}

    return {
        "total": total, "open": open_count, "closed": closed_count,
        "escalated": escalated, "priority": priority, "by_state": by_state,
    }


@router.get("/cases/trend")
def case_trend(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Monthly case creation trend."""
    month_col = _month_expr(CaseRecord.opened_at)
    query = db.query(
        month_col.label("month"),
        func.count().label("count"),
    ).filter(CaseRecord.opened_at.isnot(None))

    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = query.group_by(month_col).order_by(month_col).all()
    return {
        "trend": [
            {"month": r.month.strftime("%Y-%m") if hasattr(r.month, 'strftime') else str(r.month)[:7], "count": r.count}
            for r in rows
        ]
    }


@router.get("/cases/problem-areas")
def case_problem_areas(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    limit: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top problem areas by case_type and case_category."""
    query = db.query(CaseRecord)
    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    # By case type
    type_rows = (
        query.with_entities(CaseRecord.case_type, func.count().label("cnt"))
        .filter(CaseRecord.case_type != "")
        .group_by(CaseRecord.case_type)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    # By case category
    cat_rows = (
        query.with_entities(CaseRecord.case_category, func.count().label("cnt"))
        .filter(CaseRecord.case_category != "")
        .group_by(CaseRecord.case_category)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    # By contact type
    contact_rows = (
        query.with_entities(CaseRecord.contact_type, func.count().label("cnt"))
        .filter(CaseRecord.contact_type != "")
        .group_by(CaseRecord.contact_type)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    # By closure code
    closure_rows = (
        query.with_entities(CaseRecord.closure_code, func.count().label("cnt"))
        .filter(CaseRecord.closure_code != "")
        .group_by(CaseRecord.closure_code)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    return {
        "by_type": [{"label": r[0], "count": r[1]} for r in type_rows],
        "by_category": [{"label": r[0], "count": r[1]} for r in cat_rows],
        "by_contact_type": [{"label": r[0], "count": r[1]} for r in contact_rows],
        "by_closure_code": [{"label": r[0], "count": r[1]} for r in closure_rows],
    }


@router.get("/cases/breakdown")
def case_breakdown(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Full breakdown: category + subcategory matrix."""
    query = db.query(CaseRecord)
    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = (
        query.with_entities(
            CaseRecord.category,
            CaseRecord.subcategory,
            func.count().label("cnt"),
        )
        .group_by(CaseRecord.category, CaseRecord.subcategory)
        .order_by(func.count().desc())
        .all()
    )

    return {
        "breakdown": [
            {"category": r[0] or "Unknown", "subcategory": r[1] or "Unknown", "count": r[2]}
            for r in rows
        ]
    }


@router.get("/cases/top-accounts")
def top_accounts(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Top accounts by case volume."""
    rows = (
        db.query(
            CaseRecord.account_name,
            CaseRecord.instance,
            func.count().label("total"),
            func.count().filter(CaseRecord.priority.like("1%")).label("p1"),
            func.count().filter(CaseRecord.priority.like("2%")).label("p2"),
            func.count().filter(CaseRecord.escalated == True).label("escalated"),
        )
        .group_by(CaseRecord.account_name, CaseRecord.instance)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )

    return {
        "accounts": [
            {
                "account": r.account_name,
                "instance": r.instance or "",
                "total": r.total,
                "p1": r.p1,
                "p2": r.p2,
                "escalated": r.escalated,
            }
            for r in rows
        ]
    }


@router.get("/cases/rca")
def case_rca(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Root cause analysis from closure codes, grouped into buckets."""
    query = db.query(CaseRecord).filter(CaseRecord.closure_code != "")
    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = (
        query.with_entities(CaseRecord.closure_code, func.count().label("cnt"))
        .group_by(CaseRecord.closure_code)
        .order_by(func.count().desc())
        .all()
    )

    buckets = {
        "ServiceNow Caused": 0,
        "Customer Caused": 0,
        "3rd Party": 0,
        "Unknown / Other": 0,
    }
    detail = []
    for r in rows:
        code = r[0] or ""
        count = r[1]
        lower = code.lower()
        if "servicenow" in lower or "servicenow" in lower.replace("-", ""):
            bucket = "ServiceNow Caused"
        elif "customer" in lower:
            bucket = "Customer Caused"
        elif "3rd party" in lower or "third party" in lower:
            bucket = "3rd Party"
        else:
            bucket = "Unknown / Other"
        buckets[bucket] += count
        detail.append({"code": code, "count": count, "bucket": bucket})

    return {
        "buckets": [{"label": k, "count": v} for k, v in buckets.items() if v > 0],
        "detail": detail,
    }


@router.get("/cases/type-trend")
def case_type_trend(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Monthly trend by case type (bugs, performance, outage, etc.)."""
    month_col = _month_expr(CaseRecord.opened_at)
    query = db.query(
        month_col.label("month"),
        CaseRecord.case_type,
        func.count().label("count"),
    ).filter(CaseRecord.opened_at.isnot(None), CaseRecord.case_type != "")

    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = query.group_by(month_col, CaseRecord.case_type).order_by(month_col).all()

    data = {}
    for r in rows:
        m = r.month.strftime("%Y-%m") if hasattr(r.month, 'strftime') else str(r.month)[:7] if r.month else ""
        if m not in data:
            data[m] = {}
        data[m][r.case_type or "Other"] = r.count

    return {
        "trend": [{"month": m, **v} for m, v in sorted(data.items())]
    }


@router.get("/cases/hot-zones")
def case_hot_zones(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top categories with priority severity breakdown — hot zone heatmap data."""
    query = db.query(CaseRecord)
    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = (
        query.with_entities(
            CaseRecord.case_category,
            CaseRecord.priority,
            func.count().label("cnt"),
        )
        .filter(CaseRecord.case_category != "")
        .group_by(CaseRecord.case_category, CaseRecord.priority)
        .all()
    )

    # Reshape: {category: {P1: n, P2: n, total: n}}
    cats = {}
    for r in rows:
        cat = r[0] or "Unknown"
        pri = r[1] or "Unknown"
        if cat not in cats:
            cats[cat] = {"category": cat, "total": 0}
        cats[cat][pri] = cats[cat].get(pri, 0) + r[2]
        cats[cat]["total"] += r[2]

    sorted_cats = sorted(cats.values(), key=lambda x: x["total"], reverse=True)[:limit]
    return {"zones": sorted_cats}


@router.get("/cases/priority-trend")
def priority_trend(
    account: Optional[str] = None,
    instance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Monthly trend broken out by priority."""
    month_col = _month_expr(CaseRecord.opened_at)
    query = db.query(
        month_col.label("month"),
        CaseRecord.priority,
        func.count().label("count"),
    ).filter(CaseRecord.opened_at.isnot(None))

    if account:
        query = query.filter(CaseRecord.account_name == account)
    if instance:
        query = query.filter(CaseRecord.instance == instance)

    rows = query.group_by(month_col, CaseRecord.priority).order_by(month_col).all()

    # Reshape into {month: {P1: n, P2: n, ...}}
    data = {}
    for r in rows:
        m = r.month.strftime("%Y-%m") if hasattr(r.month, 'strftime') else str(r.month)[:7] if r.month else ""
        if m not in data:
            data[m] = {}
        data[m][r.priority or "Unknown"] = r.count

    return {
        "trend": [{"month": m, **v} for m, v in sorted(data.items())]
    }
