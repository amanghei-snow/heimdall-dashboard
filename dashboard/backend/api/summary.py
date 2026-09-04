"""API routes for dashboard summary cards and chart data."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, Integer

from dashboard.backend.database import get_db
from dashboard.backend.models.instance import Instance

router = APIRouter(tags=["summary"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Aggregated metrics for summary cards — single lightweight query."""
    row = db.query(
        func.count(Instance.id).label("total_instances"),
        func.sum(Instance.total_table_rows).label("total_rows"),
        func.sum(Instance.db_gb_csv).label("total_db_size_gb"),
        func.avg(
            func.nullif(Instance.node_count, 0)
        ).label("avg_nodes"),
        func.sum(Instance.table_count).label("total_tables"),
        func.sum(func.cast(Instance.has_ruckus_data, Integer)).label("collected"),
    ).first()

    total_db_gb = float(row.total_db_size_gb or 0)
    return {
        "total_instances": row.total_instances or 0,
        "total_rows": int(row.total_rows or 0),
        "total_db_size_gb": round(total_db_gb, 1),
        "total_db_size_display": (
            f"{total_db_gb / 1024:.1f} TB" if total_db_gb >= 1024
            else f"{total_db_gb:.1f} GB"
        ),
        "avg_nodes": round(float(row.avg_nodes or 0), 1),
        "total_tables": int(row.total_tables or 0),
        "collected": int(row.collected or 0),
    }


@router.get("/summary/tier-distribution")
def tier_distribution(db: Session = Depends(get_db)):
    """Capacity tier distribution for pie chart."""
    rows = (
        db.query(Instance.capacity_tier, func.count(Instance.id))
        .group_by(Instance.capacity_tier)
        .order_by(func.count(Instance.id).desc())
        .all()
    )
    return {"data": [{"tier": r[0] or "Unknown", "count": r[1]} for r in rows]}


@router.get("/summary/dbtype-distribution")
def dbtype_distribution(db: Session = Depends(get_db)):
    """DB type distribution for pie chart."""
    rows = (
        db.query(Instance.db_type, func.count(Instance.id))
        .group_by(Instance.db_type)
        .order_by(func.count(Instance.id).desc())
        .all()
    )
    return {"data": [{"db_type": r[0] or "Unknown", "count": r[1]} for r in rows]}


@router.get("/summary/hyperscaler-distribution")
def hyperscaler_distribution(db: Session = Depends(get_db)):
    """Hyperscaler vs on-prem distribution for pie chart."""
    hs = db.query(func.count(Instance.id)).filter(Instance.is_hyperscaler == True).scalar() or 0
    total = db.query(func.count(Instance.id)).scalar() or 0
    on_prem = total - hs
    return {"data": [
        {"label": "Hyperscaler", "count": hs},
        {"label": "ServiceNow Hosted", "count": on_prem},
    ]}


@router.get("/summary/hyperscaler-by-dc")
def hyperscaler_by_dc(db: Session = Depends(get_db)):
    """Hyperscaler instance count grouped by datacenter pod."""
    rows = (
        db.query(Instance.datacenter, func.count(Instance.id))
        .filter(Instance.is_hyperscaler == True, Instance.datacenter != "")
        .group_by(Instance.datacenter)
        .order_by(func.count(Instance.id).desc())
        .all()
    )
    return {"data": [{"dc": r[0], "count": r[1]} for r in rows]}


@router.get("/summary/top-by-dbsize")
def top_by_dbsize(db: Session = Depends(get_db), limit: int = 20):
    """Top N instances by DB size for bar chart."""
    rows = (
        db.query(Instance.instance, Instance.db_gb_csv, Instance.txn_90d)
        .order_by(Instance.db_gb_csv.desc())
        .limit(limit)
        .all()
    )
    return {
        "labels": [r.instance for r in rows],
        "db_sizes": [round(r.db_gb_csv, 1) for r in rows],
        "txn": [round(r.txn_90d, 0) for r in rows],
    }


@router.get("/summary/top-by-txn")
def top_by_txn(db: Session = Depends(get_db), limit: int = 20):
    """Top N instances by transactions/day (90d) for bar chart."""
    rows = (
        db.query(Instance.instance, Instance.txn_90d)
        .order_by(Instance.txn_90d.desc())
        .limit(limit)
        .all()
    )
    return {
        "labels": [r.instance for r in rows],
        "txn": [round(r.txn_90d, 0) for r in rows],
    }
