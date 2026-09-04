"""SQLAlchemy models for the Heimdall dashboard."""
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, BigInteger,
    ForeignKey, Index, func,
)
from sqlalchemy.orm import relationship
from dashboard.backend.database import Base


class Instance(Base):
    """Core instance table — one row per ServiceNow instance."""
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance = Column(String(256), unique=True, nullable=False, index=True)
    company = Column(String(512), default="", index=True)
    rank = Column(Integer, default=0, index=True)
    score = Column(Float, default=0.0, index=True)
    coverage = Column(Float, default=0.0)

    # CSV-sourced metrics
    txn_90d = Column(Float, default=0.0, index=True)
    db_gb_csv = Column(Float, default=0.0, index=True)
    primary_table_gb = Column(Float, default=0.0)
    capacity_tier = Column(String(32), default="", index=True)
    db_type = Column(String(32), default="", index=True)
    release_patch = Column(String(256), default="")
    release_family = Column(String(32), default="", index=True)

    # Ruckus-collected DB info
    db_total_size_gb = Column(Float, default=0.0)
    db_count = Column(Integer, default=0)
    db_size_best = Column(Float, default=0.0, index=True)

    # Ruckus-collected table info
    table_count = Column(Integer, default=0, index=True)
    total_table_rows = Column(BigInteger, default=0, index=True)
    total_data_size_gb = Column(Float, default=0.0)
    total_index_size_gb = Column(Float, default=0.0)

    # Ruckus-collected node info
    node_count = Column(Integer, default=0, index=True)
    app_server_count = Column(Integer, default=0)

    # Collection status
    has_ruckus_data = Column(Boolean, default=False)
    used_fallback = Column(Boolean, default=False)

    # Delta fields (change since previous run)
    d_total_table_rows = Column(BigInteger, nullable=True)
    d_table_count = Column(Integer, nullable=True)
    d_db_total_size_gb = Column(Float, nullable=True)
    d_node_count = Column(Integer, nullable=True)
    d_txn_90d = Column(Float, nullable=True)
    d_db_gb_csv = Column(Float, nullable=True)
    d_score = Column(Float, nullable=True)
    has_delta = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    top_tables = relationship("TopTable", back_populates="instance_rel", cascade="all, delete-orphan", lazy="dynamic")

    __table_args__ = (
        Index("idx_instance_score", "score", "rank"),
        Index("idx_instance_company", "company", "instance"),
        Index("idx_instance_rank_id", "rank", "id"),
    )


class TopTable(Base):
    """Top tables per instance — lazy-loaded on expand."""
    __tablename__ = "top_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(512), nullable=False)
    rows = Column(BigInteger, default=0)
    data_length = Column(BigInteger, default=0)
    index_length = Column(BigInteger, default=0)

    instance_rel = relationship("Instance", back_populates="top_tables")

    __table_args__ = (
        Index("idx_toptable_instance_rows", "instance_id", "rows"),
    )


class RapSession(Base):
    """RAP — Heimdall Regression & Performance session tracking."""
    __tablename__ = "rap_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_start = Column(DateTime, nullable=False)
    duration_ms = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    render_count = Column(Integer, default=0)
    avg_render_ms = Column(Integer, default=0)
    max_render_ms = Column(Integer, default=0)
    tabs_visited = Column(Text, default="")
    user_agent = Column(String(512), default="")
    created_at = Column(DateTime, server_default=func.now())

    events = relationship("RapEvent", back_populates="session", cascade="all, delete-orphan", lazy="dynamic")


class RapEvent(Base):
    """Individual RAP events (clicks, renders) for detailed analysis."""
    __tablename__ = "rap_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("rap_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)  # 'click', 'render', 'tab'
    timestamp_ms = Column(BigInteger, nullable=False)
    render_ms = Column(Integer, nullable=True)
    tab = Column(String(32), nullable=True)

    session = relationship("RapSession", back_populates="events")

    __table_args__ = (
        Index("idx_rap_event_session_type", "session_id", "event_type"),
    )


class AggregatorRun(Base):
    """History: one row per aggregator collection run."""
    __tablename__ = "aggregator_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String(64), nullable=False)
    top_n = Column(Integer, default=0)
    instance_count = Column(Integer, default=0)

    snapshots = relationship("AggregatorSnapshot", back_populates="run", cascade="all, delete-orphan")


class AggregatorSnapshot(Base):
    """History: per-instance metrics captured in each aggregator run."""
    __tablename__ = "aggregator_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("aggregator_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    instance = Column(String(256), nullable=False, index=True)
    total_table_rows = Column(BigInteger, default=0)
    table_count = Column(Integer, default=0)
    db_total_size_gb = Column(Float, default=0.0)
    total_data_size_gb = Column(Float, default=0.0)
    total_index_size_gb = Column(Float, default=0.0)
    node_count = Column(Integer, default=0)
    txn_90d = Column(Float, default=0.0)
    db_gb_csv = Column(Float, default=0.0)
    score = Column(Float, default=0.0)

    run = relationship("AggregatorRun", back_populates="snapshots")

    __table_args__ = (
        Index("idx_snapshot_instance_run", "instance", "run_id"),
    )
