"""
Comprehensive ruckus analysis modules (1–40).

Each analyzer function takes (views) and returns:
  { "score": 0-100, "findings": [...], "summary": str }

Finding format:
  { "severity": "critical"|"warning"|"info",
    "title": str, "detail": str, "ci": str|None }

Analyzers 1–18:  Original infrastructure, DB, app, change, alert, backup, LB
Analyzers 21–30: Table growth, transactions, security, OIDC, semaphores,
                  parallel queries, cache, maintenance windows, patching
Analyzers 31–39: Node restarts, replication consistency, orphaned resources,
                  connection pool sizing, node scaling, alert closure velocity,
                  recurring incidents, update set conflicts, post-deploy health
Analyzer 40:     Executive risk narrative generator
Special:         Anomaly detection (cross-signal), collection errors, composite score
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import re

# ── Helpers ────────────────────────────────────────────────────────────────

def _ts(s: Any) -> Optional[datetime]:
    """Parse a timestamp string to datetime (UTC)."""
    if not s or not isinstance(s, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

def _age_days(ts_str: Any) -> float:
    """Days since timestamp."""
    dt = _ts(ts_str)
    if not dt:
        return 0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400

def _parse_duration(s: str) -> float:
    """Parse HH:MM:SS or H:MM:SS to seconds."""
    if not s or not isinstance(s, str):
        return 0
    s = s.strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return 0

def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        if isinstance(v, str):
            v = v.strip().replace(",", "")
        return float(v)
    except (ValueError, TypeError):
        return default

def _safe_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        if isinstance(v, str):
            v = v.strip().replace(",", "")
        return int(float(v))
    except (ValueError, TypeError):
        return default

def _name(obj: Any) -> str:
    if isinstance(obj, dict):
        return obj.get("name", str(obj))
    return str(obj) if obj else ""

def _short(fqdn: str) -> str:
    return fqdn.split(".")[0] if fqdn else ""

def _dc_from_server(name: str) -> str:
    """Extract DC from FQDN like db175143.ord101.service-now.com → ORD101."""
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[1].upper()
    return ""

def _iv(views: dict) -> dict:
    """Get the first InstanceView item."""
    iv_data = views.get("InstanceView", [])
    if isinstance(iv_data, dict):
        iv_data = iv_data.get("data", iv_data)
    if isinstance(iv_data, list) and iv_data:
        return iv_data[0] if isinstance(iv_data[0], dict) else {}
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE HEALTH
# ═══════════════════════════════════════════════════════════════════════════

# ── 1. DB Capacity Scoring & Forecasting ──────────────────────────────────

def analyze_db_capacity(views: dict) -> dict:
    iv = _iv(views)
    db_servers = iv.get("dbServers", [])
    if not db_servers:
        db_servers = views.get("PsqlDbserverView", [])
    if not db_servers:
        return {"score": -1, "findings": [], "summary": "No DB server data"}

    findings = []
    scores = []

    for ds in db_servers:
        if not isinstance(ds, dict):
            continue
        name = _short(_name(ds))
        pts_used = _safe_float(ds.get("pointsUsed"))
        pts_max = _safe_float(ds.get("pointsMax"))
        mp = ds.get("metricPoints", {}) or {}
        life = (ds.get("lifeCycle") or "").lower()

        if life in ("decommissioned",):
            continue  # skip decom

        # Points scoring
        if pts_max > 0:
            ratio = pts_used / pts_max
            srv_score = max(0, int(100 * (1 - max(0, ratio - 0.5) * 2)))
            scores.append(srv_score)

            if ratio > 1.0:
                findings.append({
                    "severity": "critical",
                    "title": f"{name}: Points {pts_used:.0f}/{pts_max:.0f} ({ratio:.0%} — OVER CAPACITY)",
                    "detail": f"Glide: {_safe_float(mp.get('diskUsed')):.0f}/{_safe_float(mp.get('diskMax')):.0f}, "
                              f"Mem: {_safe_float(mp.get('memUsed')):.0f}/{_safe_float(mp.get('memMax')):.0f}, "
                              f"HPG: {_safe_float(mp.get('hpgUsed')):.0f}/{_safe_float(mp.get('hpgMax')):.0f}",
                    "ci": name,
                })
            elif ratio > 0.9:
                findings.append({
                    "severity": "warning",
                    "title": f"{name}: Points {pts_used:.0f}/{pts_max:.0f} ({ratio:.0%})",
                    "detail": "Approaching capacity threshold",
                    "ci": name,
                })

        # Disk analysis
        disks = ds.get("disks", [])
        if isinstance(disks, list):
            for disk in disks:
                if not isinstance(disk, dict):
                    continue
                mount = disk.get("mountPoint", "")
                total = _safe_float(disk.get("totalMb"))
                used = _safe_float(disk.get("usedMb"))
                if total > 0 and mount in ("/glide", "/backup", "/"):
                    pct = used / total
                    if pct > 0.9:
                        findings.append({
                            "severity": "critical" if pct > 0.95 else "warning",
                            "title": f"{name}: Disk {mount} at {pct:.0%} ({used/1024:.0f}/{total/1024:.0f} GB)",
                            "detail": f"Mount {mount} nearing full",
                            "ci": name,
                        })

    avg_score = int(sum(scores) / len(scores)) if scores else -1
    return {
        "score": avg_score,
        "findings": findings,
        "summary": f"{len(db_servers)} servers, {sum(1 for f in findings if f['severity']=='critical')} critical",
    }


# ── 2. Replication Topology Integrity ─────────────────────────────────────

def analyze_replication(views: dict) -> dict:
    iv = _iv(views)
    repl_dbs = iv.get("psqlReplicationDatabases", [])
    if not repl_dbs:
        repl_dbs = views.get("PsqlDatabaseReplicationView", [])
    if not repl_dbs:
        return {"score": -1, "findings": [], "summary": "No replication data"}

    findings = []
    scores = []
    dc_roles = {}  # dc -> list of roles

    for rd in repl_dbs:
        if not isinstance(rd, dict):
            continue
        host = _short(_name(rd.get("host", "")))
        port = rd.get("port", "?")
        role = rd.get("replicationRole", "")
        lag = _safe_int(rd.get("replicationLag"))
        partner = _short(_name(rd.get("replicationPartner", "")))
        status = rd.get("replicationStatus") or ""
        usage = rd.get("usage", "")
        cluster_lag = _safe_int(rd.get("sysClusterStateLag"))
        dc = _dc_from_server(_name(rd.get("host", "")))

        dc_roles.setdefault(dc, []).append(role)

        # Score this replication link
        link_score = 100

        # Broken replication
        if "broken" in str(status).lower() or "broken" in str(role).lower():
            findings.append({
                "severity": "critical",
                "title": f"{host}:{port} — Replication BROKEN",
                "detail": f"Role: {role}, Status: {status}, Partner: {partner}",
                "ci": host,
            })
            link_score = 0

        # Lag threshold
        elif lag > 30:
            findings.append({
                "severity": "critical" if lag > 120 else "warning",
                "title": f"{host}:{port} — Replication lag {lag}s",
                "detail": f"Role: {role}, Partner: {partner}, Usage: {usage}",
                "ci": host,
            })
            link_score = max(0, 100 - lag)

        # No partner for non-standalone
        if not partner and "standalone" not in role.lower() and "primary" in role.lower():
            if usage.lower() != "primary":
                findings.append({
                    "severity": "warning",
                    "title": f"{host}:{port} — No replication partner",
                    "detail": f"Role: {role}, Usage: {usage}",
                    "ci": host,
                })
                link_score = min(link_score, 60)

        # Cluster state lag
        if cluster_lag > 100000:
            findings.append({
                "severity": "info",
                "title": f"{host}:{port} — High cluster state lag: {cluster_lag:,}",
                "detail": f"May indicate WAL replay drift",
                "ci": host,
            })

        scores.append(link_score)

    # DC topology asymmetry
    if len(dc_roles) >= 2:
        dc_counts = {dc: len(roles) for dc, roles in dc_roles.items()}
        max_dc = max(dc_counts.values())
        min_dc = min(dc_counts.values())
        if max_dc > 0 and min_dc / max_dc < 0.5:
            findings.append({
                "severity": "warning",
                "title": f"DC topology asymmetry: {dc_counts}",
                "detail": "Significant imbalance in replication topology across datacenters",
                "ci": None,
            })

    avg = int(sum(scores) / len(scores)) if scores else -1
    return {
        "score": avg,
        "findings": findings,
        "summary": f"{len(repl_dbs)} repl links, {len(dc_roles)} DCs",
    }


# ── 3. Server Lifecycle & Hardware Risk Matrix ────────────────────────────

def analyze_server_lifecycle(views: dict) -> dict:
    iv = _iv(views)
    db_servers = iv.get("dbServers", [])
    if not db_servers:
        db_servers = views.get("PsqlDbserverView", [])
    if not db_servers:
        return {"score": -1, "findings": [], "summary": "No DB server data"}

    findings = []
    hold_count = 0
    decom_count = 0
    sku_counts = {}
    rack_dbs = {}  # rack -> [server names]

    for ds in db_servers:
        if not isinstance(ds, dict):
            continue
        name = _short(_name(ds))
        life = (ds.get("lifeCycle") or "").lower()
        sku_raw = ds.get("sku", "")
        sku = sku_raw.split("-")[0] if sku_raw else "Unknown"
        rack_obj = ds.get("rack", {})
        rack_name = _name(rack_obj) if isinstance(rack_obj, dict) else str(rack_obj)
        dc = ""
        if isinstance(rack_obj, dict):
            cage = rack_obj.get("cage", {})
            if isinstance(cage, dict):
                dc = _name(cage.get("datacenter", ""))

        sku_counts[sku] = sku_counts.get(sku, 0) + 1
        rack_dbs.setdefault(rack_name, []).append(name)

        if life == "hold":
            hold_count += 1
            # Check for change requests explaining hold
            crs = ds.get("changeRequests", [])
            cr_info = ""
            if isinstance(crs, list) and crs:
                cr = crs[0] if isinstance(crs[0], dict) else {}
                cr_info = f" ({cr.get('number', '')}: {cr.get('shortDescription', '')[:40]})"
            findings.append({
                "severity": "warning",
                "title": f"{name} — On HOLD{cr_info}",
                "detail": f"SKU: {sku_raw[:40]}, DC: {dc}",
                "ci": name,
            })
        elif life == "decommissioned":
            decom_count += 1
            findings.append({
                "severity": "info",
                "title": f"{name} — Decommissioned but still in topology",
                "detail": f"SKU: {sku}, DC: {dc}",
                "ci": name,
            })

    # Rack concentration risk
    for rack, servers in rack_dbs.items():
        if len(servers) >= 3 and rack:
            findings.append({
                "severity": "warning",
                "title": f"Rack concentration: {rack} has {len(servers)} DB servers",
                "detail": ", ".join(servers[:6]),
                "ci": None,
            })

    # Aging SKU detection
    aging_skus = {"Freedom", "Grissom"}
    for sku, count in sku_counts.items():
        if sku in aging_skus and count > 0:
            findings.append({
                "severity": "info",
                "title": f"Aging SKU: {count}x {sku} servers",
                "detail": "Consider hardware refresh",
                "ci": None,
            })

    active = len(db_servers) - decom_count
    score = max(0, 100 - hold_count * 15 - decom_count * 5)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{active} active servers, {hold_count} on hold, {decom_count} decommissioned",
    }


# ── 4. Multi-DC Failover Readiness ───────────────────────────────────────

def analyze_failover_readiness(views: dict) -> dict:
    iv = _iv(views)
    repl_dbs = iv.get("psqlReplicationDatabases", [])
    databases = iv.get("psqlDatabases", [])
    backups = views.get("BackupView", [])
    if isinstance(backups, dict):
        backups = backups.get("data", [])
    if not repl_dbs:
        return {"score": -1, "findings": [], "summary": "No replication data"}

    findings = []

    # Map primary -> standby pairs
    primaries = {}
    standbys = {}
    for rd in repl_dbs:
        if not isinstance(rd, dict):
            continue
        host = _name(rd.get("host", ""))
        port = rd.get("port", "?")
        role = (rd.get("replicationRole") or "").lower()
        usage = (rd.get("usage") or "").lower()
        lag = _safe_int(rd.get("replicationLag"))
        partner = _name(rd.get("replicationPartner", ""))
        dc = _dc_from_server(host)
        key = f"{_short(host)}:{port}"

        if "primary" in role or "primary" in usage:
            primaries[key] = {"host": host, "port": port, "dc": dc, "lag": lag, "partner": partner}
        elif "standby" in role or "standby" in usage:
            standbys[key] = {"host": host, "port": port, "dc": dc, "lag": lag, "partner": partner}

    # Check each primary has a standby in different DC
    shard_scores = []
    for pkey, pdata in primaries.items():
        has_cross_dc_standby = False
        standby_lag = 0
        for skey, sdata in standbys.items():
            # Match via partner
            if _short(pdata["host"]) in _short(sdata.get("partner", "")):
                if sdata["dc"] != pdata["dc"]:
                    has_cross_dc_standby = True
                standby_lag = max(standby_lag, sdata["lag"])

        shard_score = 100
        if not has_cross_dc_standby:
            findings.append({
                "severity": "warning",
                "title": f"{pkey} — No cross-DC standby",
                "detail": f"Primary in {pdata['dc']}, no standby in different DC",
                "ci": _short(pdata["host"]),
            })
            shard_score -= 40

        if standby_lag > 60:
            findings.append({
                "severity": "critical" if standby_lag > 300 else "warning",
                "title": f"{pkey} — Standby lag {standby_lag}s",
                "detail": "Exceeds SLA threshold for failover readiness",
                "ci": _short(pdata["host"]),
            })
            shard_score -= min(40, standby_lag // 5)

        shard_scores.append(max(0, shard_score))

    avg = int(sum(shard_scores) / len(shard_scores)) if shard_scores else -1
    return {
        "score": avg,
        "findings": findings,
        "summary": f"{len(primaries)} primaries, {len(standbys)} standbys",
    }


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════

# ── 5. Long-Running Query Impact Analysis ─────────────────────────────────

def analyze_queries(views: dict) -> dict:
    iv = _iv(views)
    query_dbs = iv.get("queryDatabases", [])
    # Also check PsqlDatabaseQueryView
    qdv = views.get("PsqlDatabaseQueryView", [])
    if isinstance(qdv, dict):
        qdv = qdv.get("data", [])

    all_queries = []
    for db in query_dbs:
        if not isinstance(db, dict):
            continue
        db_name = _short(db.get("name", ""))
        q = db.get("queries", {})
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": db_name})
        elif isinstance(q, list):
            for qi in q:
                if isinstance(qi, dict) and qi.get("query"):
                    all_queries.append({**qi, "_db": db_name})

    for item in qdv:
        if not isinstance(item, dict):
            continue
        q = item.get("queries", item)
        db_name = _short(item.get("name", ""))
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": db_name})

    if not all_queries:
        return {"score": 100, "findings": [], "summary": "No active queries"}

    findings = []
    # Group by ppid for parallel worker detection
    ppid_groups = {}
    for q in all_queries:
        ppid = q.get("ppid")
        if ppid and _safe_int(ppid) > 0:
            ppid_groups.setdefault(ppid, []).append(q)

    scored = []
    for q in all_queries:
        time_val = q.get("time", "")
        secs = _safe_float(time_val)
        if isinstance(time_val, str) and ":" in time_val:
            secs = _parse_duration(time_val)

        query_text = str(q.get("query", ""))[:80]
        pid = q.get("pid", "?")
        ppid = q.get("ppid")
        db = q.get("_db", "?")
        is_parallel = q.get("isParallelWorker", False)

        # Skip WAL streaming replication connections — they run indefinitely by design
        if "START_REPLICATION" in query_text or "walsender" in query_text.lower():
            continue

        # Count parallel workers for this parent
        parallel_count = len(ppid_groups.get(ppid, [])) if ppid else 1

        # Risk score: duration × parallelism
        risk = secs * max(1, parallel_count)

        if secs > 300:  # 5+ minutes
            findings.append({
                "severity": "critical",
                "title": f"{db} PID {pid} — Query running {secs:.0f}s ({secs/60:.1f}m)",
                "detail": f"Query: {query_text}" + (f" | {parallel_count} parallel workers" if parallel_count > 1 else ""),
                "ci": db,
            })
            scored.append(max(0, 100 - int(secs / 10)))
        elif secs > 60:
            findings.append({
                "severity": "warning",
                "title": f"{db} PID {pid} — Query running {secs:.0f}s",
                "detail": query_text,
                "ci": db,
            })
            scored.append(max(40, 100 - int(secs / 5)))

    score = int(sum(scored) / len(scored)) if scored else 100
    return {
        "score": score,
        "findings": findings,
        "summary": f"{len(all_queries)} active queries, {len(findings)} long-running",
    }


# ── 6. Connection Saturation Heatmap ─────────────────────────────────────

def analyze_connections(views: dict) -> dict:
    iv = _iv(views)
    databases = iv.get("psqlDatabases", [])
    if not databases:
        databases = views.get("PsqlDatabaseView", [])
        if isinstance(databases, dict):
            databases = databases.get("data", [])
    if not databases:
        return {"score": -1, "findings": [], "summary": "No database data"}

    findings = []
    scores = []

    for db in databases:
        if not isinstance(db, dict):
            continue
        host = _short(_name(db.get("host", "")))
        port = db.get("port", "?")
        all_conn = _safe_int(db.get("allConnections"))
        app_conn = _safe_int(db.get("appConnections"))
        usage = db.get("usage", "")
        cap = db.get("capacitySize", "")
        key = f"{host}:{port}"

        # Estimate max connections based on capacity
        max_conn_estimate = 2000  # default
        if "pico" in cap:
            max_conn_estimate = 200
        elif "nano" in cap:
            max_conn_estimate = 500
        elif "micro" in cap:
            max_conn_estimate = 1000

        if all_conn > 0:
            ratio = all_conn / max_conn_estimate
            conn_score = max(0, int(100 * (1 - max(0, ratio - 0.5) * 2)))
            scores.append(conn_score)

            if ratio > 0.9:
                findings.append({
                    "severity": "critical",
                    "title": f"{key} — {all_conn} connections ({ratio:.0%} of ~{max_conn_estimate})",
                    "detail": f"App: {app_conn}, Usage: {usage}, Capacity: {cap}",
                    "ci": host,
                })
            elif ratio > 0.7:
                findings.append({
                    "severity": "warning",
                    "title": f"{key} — {all_conn} connections ({ratio:.0%})",
                    "detail": f"Usage: {usage}",
                    "ci": host,
                })

    avg = int(sum(scores) / len(scores)) if scores else 100
    return {
        "score": avg,
        "findings": findings,
        "summary": f"{len(databases)} databases monitored",
    }


# ── 7. Shard Balance Analyzer ────────────────────────────────────────────

def analyze_shard_balance(views: dict) -> dict:
    iv = _iv(views)
    databases = iv.get("psqlDatabases", [])
    tables = views.get("PsqlDatabaseTableView", [])
    if isinstance(tables, dict):
        tables = tables.get("data", [])
    if not databases:
        return {"score": -1, "findings": [], "summary": "No database data"}

    findings = []

    # Get catalog sizes from databases
    catalog_sizes = {}  # catalog_name -> size_mb
    for db in databases:
        if not isinstance(db, dict):
            continue
        pc = db.get("primaryCatalog", {})
        if isinstance(pc, dict):
            cat_name = pc.get("name", "")
            size = _safe_float(pc.get("sizeMb", 0))
            if cat_name and size > 0:
                catalog_sizes[cat_name] = size

    if len(catalog_sizes) < 2:
        return {"score": 100, "findings": [], "summary": "Single catalog (no shards to compare)"}

    sizes = list(catalog_sizes.values())
    avg_size = sum(sizes) / len(sizes)
    max_size = max(sizes)
    min_size = min(sizes)

    if avg_size > 0:
        imbalance = (max_size - min_size) / avg_size
        if imbalance > 0.5:
            # Find the biggest and smallest
            biggest = max(catalog_sizes, key=catalog_sizes.get)
            smallest = min(catalog_sizes, key=catalog_sizes.get)
            findings.append({
                "severity": "warning" if imbalance > 0.3 else "info",
                "title": f"Shard imbalance: {imbalance:.0%}",
                "detail": f"Largest: {biggest} ({max_size/1024:.0f} GB), "
                          f"Smallest: {smallest} ({min_size/1024:.0f} GB), "
                          f"Avg: {avg_size/1024:.0f} GB",
                "ci": None,
            })

    score = max(0, int(100 - (max_size - min_size) / max(avg_size, 1) * 50)) if avg_size > 0 else 100
    return {
        "score": min(100, score),
        "findings": findings,
        "summary": f"{len(catalog_sizes)} catalogs, sizes {min_size/1024:.0f}–{max_size/1024:.0f} GB",
    }


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION LAYER
# ═══════════════════════════════════════════════════════════════════════════

# ── 8. App Node Resource Equilibrium ──────────────────────────────────────

def analyze_app_nodes(views: dict) -> dict:
    iv = _iv(views)
    nodes = iv.get("nodes", [])
    if not nodes:
        nodes = views.get("NodeView", [])
        if isinstance(nodes, dict):
            nodes = nodes.get("data", [])
    if not nodes:
        return {"score": -1, "findings": [], "summary": "No node data"}

    findings = []
    cpus = []
    mem_values = []

    for nd in nodes:
        if not isinstance(nd, dict) or not nd.get("isActive"):
            continue
        name = nd.get("name", "?")
        stats = nd.get("nodeStats", {}) or {}
        cpu = _safe_float(stats.get("cpu"))
        rss = _safe_float(stats.get("residentMemoryMb"))
        node_type = nd.get("type", "?")

        if cpu > 0:
            cpus.append(cpu)
        if rss > 0:
            mem_values.append(rss)

    # Detect CPU outliers (>2 std dev from mean)
    if cpus:
        avg_cpu = sum(cpus) / len(cpus)
        if len(cpus) > 5:
            variance = sum((c - avg_cpu) ** 2 for c in cpus) / len(cpus)
            std_cpu = variance ** 0.5
            threshold = avg_cpu + 2 * std_cpu
            for nd in nodes:
                if not isinstance(nd, dict) or not nd.get("isActive"):
                    continue
                stats = nd.get("nodeStats", {}) or {}
                cpu = _safe_float(stats.get("cpu"))
                if cpu > threshold and cpu > 400:  # >400% = 4+ cores saturated
                    findings.append({
                        "severity": "warning",
                        "title": f"{nd['name']} — CPU outlier: {cpu:.0f}% (avg: {avg_cpu:.0f}%)",
                        "detail": f"RSS: {_safe_float(stats.get('residentMemoryMb')):.0f} MB, Type: {nd.get('type')}",
                        "ci": nd["name"],
                    })

    # Check for stuck scheduler workers from JvmThreadView
    jvm_threads = views.get("JvmThreadView", [])
    if isinstance(jvm_threads, dict):
        jvm_threads = jvm_threads.get("data", [])
    stuck_workers = []  # (node, thread, hours)
    for t in jvm_threads:
        if not isinstance(t, dict):
            continue
        tname = t.get("name", "")
        proc = t.get("process", {}) or {}
        cpu_time = _safe_float(proc.get("time"))
        node_name = _name(t.get("node", ""))
        if "scheduler.worker" in tname and cpu_time > 43200:  # 12+ hours
            stuck_workers.append((_short(node_name), tname, cpu_time / 3600))

    # Summarize stuck workers instead of individual findings
    if stuck_workers:
        # Group by node
        node_stuck = {}
        for node, thread, hours in stuck_workers:
            node_stuck.setdefault(node, []).append((thread, hours))
        # Report top 5 worst nodes + summary
        worst = sorted(node_stuck.items(), key=lambda x: max(h for _, h in x[1]), reverse=True)
        for node, threads in worst[:5]:
            max_h = max(h for _, h in threads)
            findings.append({
                "severity": "warning",
                "title": f"{node} — {len(threads)} stuck workers (longest: {max_h:.0f}h)",
                "detail": ", ".join(t for t, _ in threads[:4]) + ("..." if len(threads) > 4 else ""),
                "ci": node,
            })
        if len(worst) > 5:
            findings.append({
                "severity": "info",
                "title": f"{len(worst) - 5} more nodes with stuck workers ({len(stuck_workers)} total threads)",
                "detail": "See JVM Threads tab for full details",
                "ci": None,
            })

    # Node type distribution
    type_counts = {}
    for nd in nodes:
        if isinstance(nd, dict) and nd.get("isActive"):
            t = nd.get("type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1

    score = 100
    crit_count = len([f for f in findings if f["severity"] == "critical"])
    warn_count = len([f for f in findings if f["severity"] == "warning"])
    score -= crit_count * 15 + min(warn_count, 10) * 3  # cap warning impact
    score = max(0, min(100, score))

    return {
        "score": score,
        "findings": findings,
        "summary": f"{sum(type_counts.values())} active nodes ({type_counts}), avg CPU {sum(cpus)/len(cpus):.0f}%" if cpus else "No active nodes",
    }


# ── 9. Request Latency Fingerprinting ────────────────────────────────────

def analyze_request_latency(views: dict) -> dict:
    sem_view = views.get("SemaphoreView", [])
    if isinstance(sem_view, dict):
        sem_view = sem_view.get("data", [])
    if not sem_view:
        return {"score": 100, "findings": [], "summary": "No active requests"}

    findings = []
    endpoint_stats = {}  # page -> [durations]

    for s in sem_view:
        if not isinstance(s, dict):
            continue
        page = s.get("page", "")
        age_str = s.get("age", "")
        age_secs = _parse_duration(age_str)
        node = s.get("semaphore", s.get("node", "?"))

        if page:
            endpoint_stats.setdefault(page, []).append(age_secs)

        if age_secs > 120:  # 2+ minutes
            findings.append({
                "severity": "critical" if age_secs > 300 else "warning",
                "title": f"{_short(node)} — Request running {age_secs:.0f}s",
                "detail": f"Endpoint: {page}",
                "ci": _short(node),
            })

    # Identify slow endpoint patterns
    for page, durations in endpoint_stats.items():
        avg_dur = sum(durations) / len(durations)
        if avg_dur > 30 and len(durations) >= 2:
            findings.append({
                "severity": "info",
                "title": f"Slow endpoint pattern: {page}",
                "detail": f"{len(durations)} requests, avg {avg_dur:.0f}s",
                "ci": None,
            })

    score = 100 - len([f for f in findings if f["severity"] == "critical"]) * 15
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(sem_view)} active requests, {len(endpoint_stats)} endpoints",
    }


# ── 10. Worker vs UI Node Balance ─────────────────────────────────────────

def analyze_worker_balance(views: dict) -> dict:
    iv = _iv(views)
    nodes = iv.get("nodes", [])
    if not nodes:
        nodes = views.get("NodeView", [])
        if isinstance(nodes, dict):
            nodes = nodes.get("data", [])

    jobs = views.get("JobView", [])
    if isinstance(jobs, dict):
        jobs = jobs.get("data", [])

    findings = []
    ui_nodes = {"total": 0}
    worker_nodes = {"total": 0}
    dc_split = {}  # dc -> {ui: n, worker: n}

    for nd in nodes:
        if not isinstance(nd, dict) or not nd.get("isActive"):
            continue
        ntype = (nd.get("type") or "").lower()
        host = _name(nd.get("host", ""))
        dc = _dc_from_server(host) if isinstance(host, str) else ""

        dc_split.setdefault(dc, {"ui": 0, "worker": 0})
        if "worker" in ntype:
            worker_nodes["total"] += 1
            dc_split[dc]["worker"] += 1
        else:
            ui_nodes["total"] += 1
            dc_split[dc]["ui"] += 1

    # Long-running jobs — handle both JSON and text formats
    for j in jobs:
        if not isinstance(j, dict):
            continue
        # JSON format: {name: "node", jobs: {age: float_secs, name: "job name", ...}}
        # Text format: {job: "node", item: "job name", age: "HH:MM:SS", ...}
        job_data = j.get("jobs", j)
        if isinstance(job_data, dict):
            age_val = job_data.get("age", j.get("age", 0))
            if isinstance(age_val, (int, float)):
                age_secs = float(age_val)
            else:
                age_secs = _parse_duration(str(age_val))
            item = job_data.get("name", j.get("item", "?"))
            node = j.get("name", j.get("job", "?"))
            thread = job_data.get("thread", j.get("thread", "?"))
            started = job_data.get("started", j.get("started", "?"))
        else:
            continue

        if age_secs > 3600:  # 1+ hour
            findings.append({
                "severity": "critical" if age_secs > 7200 else "warning",
                "title": f"{_short(node)} — Job running {age_secs/3600:.1f}h: {item[:50]}",
                "detail": f"Thread: {thread}, Started: {started}",
                "ci": _short(node),
            })

    # DC balance
    for dc, counts in dc_split.items():
        if dc:
            total = counts["ui"] + counts["worker"]
            if total > 0:
                ratio = counts["worker"] / total if total > 0 else 0

    score = 100 - len([f for f in findings if f["severity"] == "critical"]) * 10
    return {
        "score": max(0, min(100, score)),
        "findings": findings,
        "summary": f"UI: {ui_nodes['total']}, Worker: {worker_nodes['total']}, {len(dc_split)} DCs, {len(jobs)} active jobs",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CHANGE & DEPLOYMENT RISK
# ═══════════════════════════════════════════════════════════════════════════

# ── 11. Change Velocity & Blast Radius ────────────────────────────────────

def analyze_change_velocity(views: dict) -> dict:
    iv = _iv(views)
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    if not cr_items:
        return {"score": 100, "findings": [], "summary": "No changes"}

    findings = []
    all_crs = []
    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if isinstance(cr, dict):
            all_crs.append(cr)

    # Track state distribution
    states = {}
    stale_implement = []
    recent_changes = 0
    now = datetime.now(timezone.utc)

    for cr in all_crs:
        state = (cr.get("state") or "").lower()
        states[state] = states.get(state, 0) + 1
        number = cr.get("number", "?")
        desc = cr.get("shortDescription", cr.get("description", ""))[:60]
        planned = cr.get("plannedStartDate") or cr.get("planned start", "")

        # Stale implement changes
        if state == "implement":
            age = _age_days(planned)
            if age > 30:
                findings.append({
                    "severity": "critical" if age > 365 else "warning",
                    "title": f"{number} — In 'implement' for {age:.0f} days",
                    "detail": f"Description: {desc}",
                    "ci": None,
                })

        # Count recent changes (last 7 days)
        if planned:
            age = _age_days(planned)
            if age <= 7:
                recent_changes += 1

    # High velocity warning
    if recent_changes > 20:
        findings.append({
            "severity": "warning",
            "title": f"High change velocity: {recent_changes} changes in last 7 days",
            "detail": f"States: {states}",
            "ci": None,
        })

    # Temporal clustering — check for same CI with multiple recent changes
    ci_counts = {}
    for cr in all_crs:
        ci = cr.get("ci", cr.get("cmdb_ci", ""))
        if ci:
            ci_counts[ci] = ci_counts.get(ci, 0) + 1
    for ci, count in ci_counts.items():
        if count >= 5:
            findings.append({
                "severity": "warning",
                "title": f"Repeated changes on {_short(ci)}: {count} changes",
                "detail": "May indicate recurring issue",
                "ci": _short(ci),
            })

    score = 100
    score -= len([f for f in findings if f["severity"] == "critical"]) * 20
    score -= len([f for f in findings if f["severity"] == "warning"]) * 5
    return {
        "score": max(0, min(100, score)),
        "findings": findings,
        "summary": f"{len(all_crs)} changes, {recent_changes} recent, states: {states}",
    }


# ── 12. Update Set Deployment Wave Analyzer ───────────────────────────────

def analyze_update_sets(views: dict) -> dict:
    iv = _iv(views)
    update_sets = iv.get("updateSets", [])
    if not update_sets:
        us_view = views.get("UpdateSetView", [])
        if isinstance(us_view, dict):
            us_view = us_view.get("data", [])
        update_sets = us_view
    if not update_sets:
        return {"score": 100, "findings": [], "summary": "No update sets"}

    findings = []
    # Group by install time window (5-minute buckets)
    time_buckets = {}
    for us in update_sets:
        if not isinstance(us, dict):
            continue
        install_date = us.get("installDate", "")
        dt = _ts(install_date)
        if dt:
            bucket = dt.strftime("%Y-%m-%d %H:%M")[:15] + "0"  # 10-min bucket
            time_buckets.setdefault(bucket, []).append(us.get("name", "?"))

    for bucket, items in time_buckets.items():
        if len(items) >= 5:
            findings.append({
                "severity": "warning",
                "title": f"Deployment wave at {bucket}: {len(items)} update sets",
                "detail": ", ".join(items[:5]) + ("..." if len(items) > 5 else ""),
                "ci": None,
            })

    return {
        "score": 100 - len(findings) * 10,
        "findings": findings,
        "summary": f"{len(update_sets)} update sets, {len(time_buckets)} deployment windows",
    }


# ── 13. Reconvergence Frequency Detector ─────────────────────────────────

def analyze_reconvergence(views: dict) -> dict:
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    if not cr_items:
        return {"score": 100, "findings": [], "summary": "No changes"}

    findings = []
    # Look for reconvergence patterns per CI
    ci_reconv = {}  # ci -> [change dates]

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        desc = (cr.get("shortDescription", cr.get("description", "")) or "").lower()
        ci = item.get("name", cr.get("ci", ""))
        planned = cr.get("plannedStartDate") or cr.get("planned start", "")

        if "reconverg" in desc or "reconv" in desc:
            ci_reconv.setdefault(_short(ci), []).append(planned)

    for ci, dates in ci_reconv.items():
        if len(dates) >= 3:
            findings.append({
                "severity": "critical" if len(dates) >= 5 else "warning",
                "title": f"{ci} — {len(dates)} reconvergence changes",
                "detail": "Recurring remediation pattern — possible underlying hardware/config issue",
                "ci": ci,
            })

    score = 100 - len([f for f in findings if f["severity"] == "critical"]) * 20
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{sum(len(v) for v in ci_reconv.values())} reconvergence changes across {len(ci_reconv)} CIs",
    }


# ═══════════════════════════════════════════════════════════════════════════
# ALERT INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════

# ── 14. Alert Correlation & Root Cause Graph ──────────────────────────────

def analyze_alert_correlation(views: dict) -> dict:
    alert_items = views.get("AlertView", [])
    if isinstance(alert_items, dict):
        alert_items = alert_items.get("data", [])
    if not alert_items:
        return {"score": 100, "findings": [], "summary": "No alerts"}

    findings = []
    alerts_by_ci = {}  # ci -> [alerts]
    alert_types = {}  # type -> count

    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ad = item.get("alerts", item)
        if isinstance(ad, dict):
            ci = _short(item.get("name", ad.get("ci", "")))
            atype = ad.get("description", ad.get("type", ""))
            state = ad.get("state", "")
            number = ad.get("number", "?")
            alerts_by_ci.setdefault(ci, []).append(ad)
            alert_types[atype] = alert_types.get(atype, 0) + 1

    # Multi-alert CIs
    for ci, alerts in alerts_by_ci.items():
        if len(alerts) >= 2:
            types = [a.get("description", a.get("type", "?"))[:40] for a in alerts]
            findings.append({
                "severity": "warning",
                "title": f"{ci} — {len(alerts)} correlated alerts",
                "detail": "; ".join(types[:4]),
                "ci": ci,
            })

    # Known correlation patterns
    db_perf_alerts = [ci for ci, alerts in alerts_by_ci.items()
                      if any("performance" in str(a.get("description", "")).lower() for a in alerts)]
    repl_alerts = [ci for ci, alerts in alerts_by_ci.items()
                   if any("replication" in str(a.get("description", "")).lower() for a in alerts)]

    if db_perf_alerts and repl_alerts:
        findings.append({
            "severity": "critical",
            "title": "DB Performance + Replication alerts — possible root cause",
            "detail": f"Performance: {db_perf_alerts}, Replication: {repl_alerts}",
            "ci": None,
        })

    score = 100 - len([f for f in findings if f["severity"] == "critical"]) * 15
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(alert_items)} alerts across {len(alerts_by_ci)} CIs",
    }


# ── 15. Alert Age & Staleness Scoring ─────────────────────────────────────

def analyze_alert_staleness(views: dict) -> dict:
    alert_items = views.get("AlertView", [])
    if isinstance(alert_items, dict):
        alert_items = alert_items.get("data", [])
    if not alert_items:
        return {"score": 100, "findings": [], "summary": "No alerts"}

    findings = []
    stale_count = 0

    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ad = item.get("alerts", item)
        if not isinstance(ad, dict):
            continue

        number = ad.get("number", "?")
        state = (ad.get("state") or "").lower()
        time_str = ad.get("initialRemoteTime", ad.get("event", ""))
        ci = _short(item.get("name", ad.get("ci", "")))
        desc = ad.get("description", ad.get("type", ""))[:50]
        assigned = ad.get("assignedTo", ad.get("assigned", ""))
        task = ad.get("hiTask", ad.get("hi task", ""))
        age = _age_days(time_str)

        if age > 30 and state in ("alerting", "message alert"):
            stale_count += 1
            sev = "critical" if age > 180 else "warning"
            findings.append({
                "severity": sev,
                "title": f"{number} — Alerting for {age:.0f} days: {desc}",
                "detail": f"CI: {ci}, State: {state}, Assigned: {assigned or 'NONE'}, Task: {task or 'NONE'}",
                "ci": ci,
            })
        elif age > 7 and not assigned and state in ("alerting",):
            findings.append({
                "severity": "warning",
                "title": f"{number} — Unassigned alert for {age:.0f} days",
                "detail": f"CI: {ci}, Type: {desc}",
                "ci": ci,
            })

    score = max(0, 100 - stale_count * 10)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{len(alert_items)} alerts, {stale_count} stale (>30 days)",
    }


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP & RECOVERY
# ═══════════════════════════════════════════════════════════════════════════

# ── 16. Backup Chain Integrity & RPO Calculator ──────────────────────────

def analyze_backup_integrity(views: dict) -> dict:
    backups = views.get("BackupView", [])
    if isinstance(backups, dict):
        backups = backups.get("data", [])
    if not backups:
        return {"score": -1, "findings": [], "summary": "No backup data"}

    findings = []
    # Group by source/database
    by_source = {}
    failed = 0
    deleted = 0

    for b in backups:
        if not isinstance(b, dict):
            continue
        name = b.get("name", b.get("backup", ""))
        result = (b.get("result") or "").lower()
        source = b.get("source", "")
        if isinstance(source, dict):
            source = _name(source)
        level = str(b.get("level", b.get("L", "")))
        size_val = b.get("sizeMb", b.get("size", 0))
        start = b.get("startDate", b.get("start", ""))

        by_source.setdefault(_short(source), {"L0": [], "L1": [], "failed": 0, "deleted": 0})

        if result == "deleted":
            deleted += 1
            by_source[_short(source)]["deleted"] += 1
        elif result not in ("success",):
            if result:
                failed += 1
                by_source[_short(source)]["failed"] += 1

        if level in ("0", "L0"):
            by_source[_short(source)]["L0"].append(start)
        elif level in ("1", "L1"):
            by_source[_short(source)]["L1"].append(start)

    # Check chain completeness per source
    for source, data in by_source.items():
        if not source:
            continue
        if data["failed"] > 0:
            findings.append({
                "severity": "warning",
                "title": f"{source} — {data['failed']} failed backup(s)",
                "detail": f"L0: {len(data['L0'])}, L1: {len(data['L1'])}",
                "ci": source,
            })
        if data["deleted"] > 0:
            findings.append({
                "severity": "info",
                "title": f"{source} — {data['deleted']} deleted backup(s)",
                "detail": "Verify retention policy compliance",
                "ci": source,
            })
        if not data["L0"] and not data["L1"]:
            findings.append({
                "severity": "critical",
                "title": f"{source} — No backups found",
                "detail": "Missing backup chain",
                "ci": source,
            })

    # RPO calculation
    for source, data in by_source.items():
        if not source or not data["L1"]:
            continue
        # Estimate RPO from L1 interval
        l1_times = sorted([_ts(t) for t in data["L1"] if _ts(t)])
        if len(l1_times) >= 2:
            intervals = [(l1_times[i+1] - l1_times[i]).total_seconds() / 3600
                         for i in range(len(l1_times)-1)]
            max_interval = max(intervals) if intervals else 0
            if max_interval > 24:
                findings.append({
                    "severity": "warning",
                    "title": f"{source} — RPO gap: {max_interval:.0f}h between L1 backups",
                    "detail": "Recovery point objective may exceed SLA",
                    "ci": source,
                })

    score = max(0, 100 - failed * 15 - deleted * 2)
    return {
        "score": min(100, score),
        "findings": findings,
        "summary": f"{len(backups)} backups, {failed} failed, {deleted} deleted",
    }


# ── 17. Backup Performance Trend ─────────────────────────────────────────

def analyze_backup_performance(views: dict) -> dict:
    backups = views.get("BackupView", [])
    if isinstance(backups, dict):
        backups = backups.get("data", [])
    if not backups:
        return {"score": 100, "findings": [], "summary": "No backup data"}

    findings = []
    long_backups = []

    for b in backups:
        if not isinstance(b, dict):
            continue
        dur_str = b.get("duration", "")
        dur_secs = _parse_duration(dur_str)
        level = str(b.get("level", b.get("L", "")))
        name = b.get("name", b.get("backup", ""))[:50]
        source_raw = b.get("source", "")
        source = _short(_name(source_raw) if isinstance(source_raw, dict) else source_raw)

        # Flag unusually long backups
        if level in ("0", "L0") and dur_secs > 14400:  # 4+ hours
            long_backups.append(name)
            findings.append({
                "severity": "warning",
                "title": f"{source} — L0 backup took {dur_secs/3600:.1f}h",
                "detail": name,
                "ci": source,
            })
        elif level in ("1", "L1") and dur_secs > 1800:  # 30+ min
            findings.append({
                "severity": "info",
                "title": f"{source} — L1 backup took {dur_secs/60:.0f}m",
                "detail": name,
                "ci": source,
            })

    score = max(0, 100 - len(long_backups) * 10)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{len(backups)} backups analyzed, {len(long_backups)} slow",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NETWORK & LOAD BALANCING
# ═══════════════════════════════════════════════════════════════════════════

# ── 18. Pool Member Symmetry & DC Affinity ────────────────────────────────

def analyze_lb_pools(views: dict) -> dict:
    pools = views.get("LoadBalancingPoolView", [])
    if isinstance(pools, dict):
        pools = pools.get("data", [])
    pool_members = views.get("LoadBalancingPoolMemberView", [])
    if isinstance(pool_members, dict):
        pool_members = pool_members.get("data", [])
    if not pools and not pool_members:
        return {"score": -1, "findings": [], "summary": "No LB pool data"}

    findings = []

    for pool in pools:
        if not isinstance(pool, dict):
            continue
        members = pool.get("members", [])
        pool_name = pool.get("name", "?")
        if not isinstance(members, list):
            continue

        # DC distribution
        dc_counts = {}
        disabled = 0
        host_nodes = {}  # host -> [nodes]

        for m in members:
            if not isinstance(m, dict):
                continue
            ip = m.get("ipAddress", "")
            node = _name(m.get("node", ""))
            enabled = m.get("isEnabled", True)
            # Extract DC from node host
            dc = _dc_from_server(node)
            dc_counts[dc] = dc_counts.get(dc, 0) + 1
            if not enabled:
                disabled += 1

            # Check for multi-node hosts
            host = ip
            host_nodes.setdefault(host, []).append(node)

        if disabled > 0:
            findings.append({
                "severity": "info",
                "title": f"{pool_name} — {disabled}/{len(members)} members disabled",
                "detail": f"DC distribution: {dc_counts}",
                "ci": None,
            })

        # DC imbalance
        if len(dc_counts) >= 2:
            vals = list(dc_counts.values())
            if max(vals) > 0 and min(vals) / max(vals) < 0.5:
                findings.append({
                    "severity": "warning",
                    "title": f"{pool_name} — DC member imbalance",
                    "detail": f"Distribution: {dc_counts}",
                    "ci": None,
                })

        # Single point of failure
        for host, nodes in host_nodes.items():
            if len(nodes) >= 3:
                findings.append({
                    "severity": "warning",
                    "title": f"Host {host} runs {len(nodes)} pool members",
                    "detail": f"Single server failure would affect {len(nodes)} members",
                    "ci": None,
                })

    score = 100 - len([f for f in findings if f["severity"] == "warning"]) * 10
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(pools)} pools, {sum(len(p.get('members',[])) for p in pools if isinstance(p, dict))} total members",
    }


# ═══════════════════════════════════════════════════════════════════════════
# COLLECTION ERRORS
# ═══════════════════════════════════════════════════════════════════════════

def analyze_collection_errors(errors: list) -> dict:
    """Categorize and score ruckus collection errors."""
    if not errors:
        return {"score": 100, "findings": [], "summary": "No collection errors"}

    findings = []
    orphaned_nodes = []
    missing_refs = []
    ssh_failures = {}
    other = []

    for e in errors:
        msg = str(e) if not isinstance(e, str) else e

        # Orphaned node records
        m = re.search(r'Cannot load .+\.service_now_node record with sys_id = \'([a-f0-9]+)\'', msg)
        if m:
            orphaned_nodes.append(m.group(1))
            continue

        # Missing replication references
        m = re.search(r'No reference (\S+) for \'(.+)\'', msg)
        if m:
            ref_type, target = m.group(1), m.group(2)
            missing_refs.append({"ref": ref_type, "target": target})
            continue

        # Squawk/SSH failures
        m = re.search(r'Squawk command failed: \[([^\]]+)\]', msg)
        if m:
            host = m.group(1)
            ssh_failures[host] = ssh_failures.get(host, 0) + 1
            continue

        other.append(msg)

    # Orphaned nodes summary
    if orphaned_nodes:
        unique = list(set(orphaned_nodes))
        findings.append({
            "severity": "warning",
            "title": f"{len(unique)} orphaned node record(s) in CMDB",
            "detail": f"sys_ids: {', '.join(unique[:5])}" + (f" +{len(unique)-5} more" if len(unique) > 5 else ""),
            "ci": None,
        })

    # Missing replication references
    if missing_refs:
        for ref in missing_refs:
            # Extract host from target like 'KagamiReplicator@db170034.phx201.service-now.com:3520'
            host_match = re.search(r'@([^:]+)', ref["target"])
            host = _short(host_match.group(1)) if host_match else "?"
            findings.append({
                "severity": "warning",
                "title": f"{host} — Missing {ref['ref']} reference",
                "detail": ref["target"],
                "ci": host,
            })

    # SSH/Squawk failures
    for host, count in ssh_failures.items():
        findings.append({
            "severity": "critical",
            "title": f"{_short(host)} — SSH unreachable ({count} failed commands)",
            "detail": f"Squawk commands to {host} failed — server may be down or network issue",
            "ci": _short(host),
        })

    # Other errors
    for msg in other[:5]:
        findings.append({
            "severity": "info",
            "title": f"Collection error: {msg[:80]}",
            "detail": msg[:200],
            "ci": None,
        })

    crit = len([f for f in findings if f["severity"] == "critical"])
    warn = len([f for f in findings if f["severity"] == "warning"])
    score = max(0, 100 - crit * 20 - warn * 5)
    total = len(orphaned_nodes) + len(missing_refs) + sum(ssh_failures.values()) + len(other)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{total} errors: {len(set(orphaned_nodes))} orphaned nodes, {len(missing_refs)} missing refs, {len(ssh_failures)} unreachable hosts",
    }


# ═══════════════════════════════════════════════════════════════════════════
# DATA GROWTH & TRENDING
# ═══════════════════════════════════════════════════════════════════════════

# ── 21. Table Growth Rate Analyzer ────────────────────────────────────────

def analyze_table_growth(views: dict) -> dict:
    """Identify largest tables and flag those likely growing without rotation."""
    tables = views.get("PsqlDatabaseTableView", views.get("MysqlDatabaseTableView", []))
    if isinstance(tables, dict):
        tables = tables.get("data", [])
    if not tables:
        return {"score": 100, "findings": [], "summary": "No table data"}

    findings = []
    # Flatten: tables may be grouped by database
    flat = []
    for item in tables:
        if not isinstance(item, dict):
            continue
        inner = item.get("tables", item)
        if isinstance(inner, list):
            for t in inner:
                if isinstance(t, dict):
                    flat.append(t)
        elif isinstance(inner, dict):
            flat.append(inner)

    if not flat:
        return {"score": 100, "findings": [], "summary": "No table rows"}

    # Sort by row count desc
    for t in flat:
        t["_rows"] = _safe_int(t.get("rows", t.get("rowCount", 0)))
        t["_size_mb"] = _safe_float(t.get("dataMb", t.get("dataLength", 0)))
        t["_name"] = t.get("name", t.get("tableName", "?"))

    flat.sort(key=lambda t: t["_rows"], reverse=True)

    # Known high-growth / rotation-candidate tables
    rotation_tables = {
        "syslog", "sys_audit", "sys_audit_delete", "ecc_queue",
        "sys_email", "syslog_transaction", "sys_history_line",
        "sys_journal_field", "sysevent", "wf_transition_history",
        "task_sla", "sys_variable_value",
    }

    total_rows = sum(t["_rows"] for t in flat)
    large_tables = []

    for t in flat[:30]:
        name = t["_name"]
        rows = t["_rows"]
        size_mb = t["_size_mb"]
        pct = (rows / total_rows * 100) if total_rows > 0 else 0

        # Flag massive tables
        if rows > 100_000_000:
            sev = "critical" if rows > 500_000_000 else "warning"
            base_name = name.split(".")[-1] if "." in name else name
            detail = f"{rows:,} rows, {size_mb/1024:.1f} GB, {pct:.1f}% of total"
            if base_name.lower() in rotation_tables:
                detail += " — ROTATION CANDIDATE"
            findings.append({
                "severity": sev,
                "title": f"Table {name}: {rows:,} rows ({pct:.1f}%)",
                "detail": detail,
                "ci": None,
            })
            large_tables.append(name)

    score = max(0, 100 - len([f for f in findings if f["severity"] == "critical"]) * 15
                - len([f for f in findings if f["severity"] == "warning"]) * 5)
    return {
        "score": min(100, score),
        "findings": findings,
        "summary": f"{len(flat)} tables, {total_rows:,} total rows, {len(large_tables)} very large",
    }


# ── 22. Transaction Volume Trend & Seasonality ───────────────────────────

def analyze_transaction_volume(views: dict) -> dict:
    """Analyze servlet/transaction stats for volume anomalies."""
    servlets = views.get("ServletView", [])
    if isinstance(servlets, dict):
        servlets = servlets.get("data", [])
    sem_view = views.get("SemaphoreView", [])
    if isinstance(sem_view, dict):
        sem_view = sem_view.get("data", [])

    if not servlets and not sem_view:
        return {"score": 100, "findings": [], "summary": "No transaction data"}

    findings = []
    total_requests = 0
    node_counts = {}

    for s in servlets:
        if not isinstance(s, dict):
            continue
        node = s.get("name", "?")
        stats = s.get("servletStats", s)
        if isinstance(stats, dict):
            count = _safe_int(stats.get("totalRequests", stats.get("count", 0)))
            total_requests += count
            node_counts[node] = count

    # Detect node-level imbalance
    if node_counts and len(node_counts) >= 3:
        vals = list(node_counts.values())
        avg = sum(vals) / len(vals)
        if avg > 0:
            for node, count in node_counts.items():
                ratio = count / avg
                if ratio > 3.0:
                    findings.append({
                        "severity": "warning",
                        "title": f"{_short(node)} — {ratio:.1f}x avg transaction volume",
                        "detail": f"{count:,} requests vs avg {avg:,.0f}",
                        "ci": _short(node),
                    })
                elif ratio < 0.1 and count > 0:
                    findings.append({
                        "severity": "info",
                        "title": f"{_short(node)} — Very low transaction volume ({ratio:.1%} of avg)",
                        "detail": f"{count:,} requests vs avg {avg:,.0f}",
                        "ci": _short(node),
                    })

    # Active semaphore load as proxy for current transaction pressure
    active_sems = len(sem_view) if isinstance(sem_view, list) else 0
    if active_sems > 100:
        findings.append({
            "severity": "warning",
            "title": f"High concurrent request load: {active_sems} active semaphores",
            "detail": "Elevated transaction pressure",
            "ci": None,
        })

    return {
        "score": max(0, 100 - len(findings) * 10),
        "findings": findings,
        "summary": f"{total_requests:,} total requests across {len(node_counts)} nodes, {active_sems} active",
    }


# ═══════════════════════════════════════════════════════════════════════════
# SECURITY & COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════

# ── 23. Service Account Hygiene Audit ─────────────────────────────────────

def analyze_service_accounts(views: dict) -> dict:
    """Detect service account password reset patterns from change requests."""
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    if not cr_items:
        return {"score": 100, "findings": [], "summary": "No change data"}

    findings = []
    svc_resets = {}  # account_name -> [dates]
    svc_pattern = re.compile(
        r'[Ss]ervice account password reset\s*[-–:]\s*(\S+)', re.IGNORECASE
    )

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        desc = cr.get("shortDescription", cr.get("description", "")) or ""
        planned = cr.get("plannedStartDate") or cr.get("planned start", "")

        m = svc_pattern.search(desc)
        if m:
            acct = m.group(1).rstrip(".")
            svc_resets.setdefault(acct, []).append(planned)

    # Flag accounts with frequent resets (possible automation issues)
    for acct, dates in svc_resets.items():
        if len(dates) >= 3:
            findings.append({
                "severity": "warning",
                "title": f"Service account '{acct}' — {len(dates)} password resets",
                "detail": "Frequent resets may indicate automation or credential management issues",
                "ci": None,
            })
        elif len(dates) >= 1:
            findings.append({
                "severity": "info",
                "title": f"Service account '{acct}' — {len(dates)} password reset(s)",
                "detail": f"Last reset: {dates[-1] if dates else 'unknown'}",
                "ci": None,
            })

    total_svc = len(svc_resets)
    total_resets = sum(len(v) for v in svc_resets.values())
    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{total_svc} service accounts, {total_resets} resets detected",
    }


# ── 24. Encryption & Compliance Posture ───────────────────────────────────

def analyze_encryption_compliance(views: dict) -> dict:
    """Check encryption settings across instance and DB servers."""
    iv = _iv(views)
    findings = []

    # Instance-level encryption flags
    dare = iv.get("isDare", iv.get("dare", False))
    disk_enc = iv.get("diskEncryption", iv.get("disk_encryption", False))
    tembo = iv.get("temboMigration", iv.get("tembo migration", ""))

    if not dare:
        findings.append({
            "severity": "warning",
            "title": "DARE (Data At Rest Encryption) is DISABLED",
            "detail": "Instance data is not encrypted at rest at the application layer",
            "ci": None,
        })
    if not disk_enc:
        findings.append({
            "severity": "info",
            "title": "Disk encryption is not enabled",
            "detail": "OS-level disk encryption is off",
            "ci": None,
        })

    # Check DB servers for SED (Self-Encrypting Drives)
    db_servers = iv.get("dbServers", [])
    if not db_servers:
        db_servers = views.get("PsqlDbserverView", [])
    sed_off = 0
    for ds in db_servers:
        if not isinstance(ds, dict):
            continue
        life = (ds.get("lifeCycle") or "").lower()
        if life == "decommissioned":
            continue
        sed = ds.get("isSed", ds.get("sed", False))
        if not sed:
            sed_off += 1

    if sed_off > 0:
        findings.append({
            "severity": "info",
            "title": f"{sed_off} DB server(s) without Self-Encrypting Drives (SED)",
            "detail": "Physical disk encryption not enabled on these servers",
            "ci": None,
        })

    score = 100
    score -= len([f for f in findings if f["severity"] == "warning"]) * 15
    score -= len([f for f in findings if f["severity"] == "info"]) * 3
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"DARE: {'ON' if dare else 'OFF'}, Disk enc: {'ON' if disk_enc else 'OFF'}, SED off: {sed_off}",
    }


# ── 25. OIDC/SSO Configuration Drift ─────────────────────────────────────

def analyze_oidc_sso(views: dict) -> dict:
    """Detect OIDC/SSO related changes that may indicate config drift."""
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    us_items = views.get("UpdateSetView", [])
    if isinstance(us_items, dict):
        us_items = us_items.get("data", [])

    findings = []
    oidc_changes = []
    sso_patterns = re.compile(r'(OIDC|IDP|SSO|SAML|oauth|single.sign.on)', re.IGNORECASE)

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        desc = (cr.get("shortDescription", cr.get("description", "")) or "")
        number = cr.get("number", "?")
        state = cr.get("state", "")
        if sso_patterns.search(desc):
            oidc_changes.append({"number": number, "desc": desc[:60], "state": state})

    oidc_update_sets = 0
    for us in us_items:
        if not isinstance(us, dict):
            continue
        name = us.get("name", "")
        if sso_patterns.search(name):
            oidc_update_sets += 1

    if oidc_changes:
        for ch in oidc_changes:
            sev = "warning" if ch["state"].lower() in ("scheduled", "implement", "assess") else "info"
            findings.append({
                "severity": sev,
                "title": f"{ch['number']} — OIDC/SSO change: {ch['desc']}",
                "detail": f"State: {ch['state']}",
                "ci": None,
            })

    if oidc_update_sets > 0:
        findings.append({
            "severity": "info",
            "title": f"{oidc_update_sets} update set(s) with OIDC/SSO modifications",
            "detail": "Review for config consistency across environments",
            "ci": None,
        })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 15)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{len(oidc_changes)} OIDC/SSO changes, {oidc_update_sets} related update sets",
    }


# ═══════════════════════════════════════════════════════════════════════════
# PERFORMANCE DEEP-DIVES
# ═══════════════════════════════════════════════════════════════════════════

# ── 26. Semaphore & Thread Pool Exhaustion Predictor ──────────────────────

def analyze_semaphore_exhaustion(views: dict) -> dict:
    """Model semaphore utilization and detect thread pool saturation."""
    sem_sets = views.get("SemaphoreSetView", [])
    if isinstance(sem_sets, dict):
        sem_sets = sem_sets.get("data", [])
    sem_groups = views.get("SemaphoreView", [])
    if isinstance(sem_groups, dict):
        sem_groups = sem_groups.get("data", [])
    db_pools = views.get("DatabasePoolView", [])
    if isinstance(db_pools, dict):
        db_pools = db_pools.get("data", [])

    findings = []
    exhausted_count = 0
    high_util = 0
    total_sets = 0

    # Process SemaphoreView groups (contains semaphoreSets per node)
    for group in sem_groups:
        if not isinstance(group, dict):
            continue
        node = group.get("name", "?")
        sets = group.get("semaphoreSets", [])
        if not isinstance(sets, list):
            continue
        for s in sets:
            if not isinstance(s, dict):
                continue
            total_sets += 1
            name = s.get("name", "?")
            current = _safe_int(s.get("current"))
            max_val = _safe_int(s.get("max"))
            if max_val <= 0:
                continue
            ratio = current / max_val
            if ratio >= 1.0:
                exhausted_count += 1
                findings.append({
                    "severity": "critical",
                    "title": f"{_short(node)} — Semaphore '{name}' EXHAUSTED ({current}/{max_val})",
                    "detail": "No available slots — requests will queue/fail",
                    "ci": _short(node),
                })
            elif ratio > 0.85:
                high_util += 1
                findings.append({
                    "severity": "warning",
                    "title": f"{_short(node)} — Semaphore '{name}' at {ratio:.0%} ({current}/{max_val})",
                    "detail": "Approaching exhaustion",
                    "ci": _short(node),
                })

    # DB connection pool utilization
    for pool in db_pools:
        if not isinstance(pool, dict):
            continue
        node = pool.get("name", "?")
        pools_data = pool.get("databasePools", {})
        if isinstance(pools_data, dict):
            for pname, pval in pools_data.items():
                if not isinstance(pval, str) or "/" not in pval:
                    continue
                parts = pval.split("/")
                try:
                    cur, mx = int(parts[0].strip()), int(parts[1].strip())
                except (ValueError, IndexError):
                    continue
                if mx > 0:
                    ratio = cur / mx
                    if ratio > 0.9:
                        findings.append({
                            "severity": "critical" if ratio >= 1.0 else "warning",
                            "title": f"{_short(node)} — DB pool '{pname}' at {ratio:.0%} ({cur}/{mx})",
                            "detail": "Connection pool nearing exhaustion",
                            "ci": _short(node),
                        })

    score = max(0, 100 - exhausted_count * 25 - high_util * 10)
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{total_sets} semaphore sets, {exhausted_count} exhausted, {high_util} high utilization",
    }


# ── 27. Parallel Query Escalation Detector ────────────────────────────────

def analyze_parallel_queries(views: dict) -> dict:
    """Detect queries spawning excessive parallel workers."""
    iv = _iv(views)
    query_dbs = iv.get("queryDatabases", [])
    qdv = views.get("PsqlDatabaseQueryView", [])
    if isinstance(qdv, dict):
        qdv = qdv.get("data", [])

    all_queries = []
    for db in query_dbs:
        if not isinstance(db, dict):
            continue
        db_name = _short(db.get("name", ""))
        q = db.get("queries", {})
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": db_name})
        elif isinstance(q, list):
            for qi in q:
                if isinstance(qi, dict) and qi.get("query"):
                    all_queries.append({**qi, "_db": db_name})

    for item in qdv:
        if not isinstance(item, dict):
            continue
        db_name = _short(item.get("name", ""))
        q = item.get("queries", item)
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": db_name})

    if not all_queries:
        return {"score": 100, "findings": [], "summary": "No active queries"}

    findings = []

    # Group by parent PID to find parallel worker clusters
    ppid_groups = {}
    for q in all_queries:
        ppid = q.get("ppid")
        pid = q.get("pid")
        is_parallel = q.get("isParallelWorker", False)
        query_text = str(q.get("query", ""))
        if "START_REPLICATION" in query_text:
            continue
        if ppid and str(ppid) != str(pid):
            ppid_groups.setdefault(str(ppid), []).append(q)

    # Flag parent queries with many parallel workers
    for ppid, workers in ppid_groups.items():
        if len(workers) >= 4:
            sample = workers[0]
            db = sample.get("_db", "?")
            query_text = str(sample.get("query", ""))[:80]
            # Find the parent query duration
            parent_dur = 0
            for q in all_queries:
                if str(q.get("pid")) == ppid:
                    time_val = q.get("time", "")
                    if isinstance(time_val, str) and ":" in time_val:
                        parent_dur = _parse_duration(time_val)
                    else:
                        parent_dur = _safe_float(time_val)
                    break

            sev = "critical" if len(workers) >= 6 or parent_dur > 60 else "warning"
            findings.append({
                "severity": sev,
                "title": f"{db} PID {ppid} — {len(workers)} parallel workers",
                "detail": f"Duration: {parent_dur:.0f}s | {query_text}",
                "ci": db,
            })

    # Detect table scan patterns (queries without index hints on large tables)
    scan_keywords = ["seq scan", "seqscan"]
    for q in all_queries:
        explain = str(q.get("explainPlan", "")).lower()
        if any(kw in explain for kw in scan_keywords):
            findings.append({
                "severity": "info",
                "title": f"{q.get('_db', '?')} — Sequential scan detected",
                "detail": str(q.get("query", ""))[:80],
                "ci": q.get("_db"),
            })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "critical"]) * 20
                - len([f for f in findings if f["severity"] == "warning"]) * 10)
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(ppid_groups)} parallel query groups, {len(findings)} issues",
    }


# ── 28. Cache Hit Ratio & Query Caching Effectiveness ─────────────────────

def analyze_cache_effectiveness(views: dict) -> dict:
    """Analyze query caching changes and database cache indicators."""
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])

    findings = []
    cache_changes = []

    # Detect query caching changes
    cache_pattern = re.compile(r'(query.cach|cache.enabl|cach.*instance)', re.IGNORECASE)
    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        desc = (cr.get("shortDescription", cr.get("description", "")) or "")
        number = cr.get("number", "?")
        state = (cr.get("state") or "").lower()
        if cache_pattern.search(desc):
            cache_changes.append({"number": number, "desc": desc[:60], "state": state})

    if cache_changes:
        for ch in cache_changes:
            sev = "info"
            if ch["state"] in ("canceled",):
                sev = "warning"
            findings.append({
                "severity": sev,
                "title": f"{ch['number']} — Cache config change: {ch['desc']}",
                "detail": f"State: {ch['state']}",
                "ci": None,
            })

    # Check for canceled cache changes (rollback indicator)
    canceled = [c for c in cache_changes if c["state"] == "canceled"]
    if canceled:
        findings.append({
            "severity": "warning",
            "title": f"{len(canceled)} cache-related change(s) were CANCELED",
            "detail": "May indicate caching caused issues and was rolled back",
            "ci": None,
        })

    return {
        "score": max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 15),
        "findings": findings,
        "summary": f"{len(cache_changes)} cache-related changes detected",
    }


# ═══════════════════════════════════════════════════════════════════════════
# OPERATIONAL PATTERNS
# ═══════════════════════════════════════════════════════════════════════════

# ── 29. Maintenance Window Compliance ─────────────────────────────────────

def analyze_maintenance_windows(views: dict) -> dict:
    """Validate changes execute within approved maintenance windows."""
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    if not cr_items:
        return {"score": 100, "findings": [], "summary": "No change data"}

    findings = []
    out_of_window = 0
    emergency_no_review = 0

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        number = cr.get("number", "?")
        state = (cr.get("state") or "").lower()
        desc = (cr.get("shortDescription", cr.get("description", "")) or "")[:60]
        planned = cr.get("plannedStartDate") or cr.get("planned start", "")
        closed = cr.get("closedAt") or cr.get("closed_at", "")

        # Check if closed before planned start (rushed) or long after
        if state == "closed" and planned and closed:
            planned_dt = _ts(planned)
            closed_dt = _ts(closed)
            if planned_dt and closed_dt:
                diff = (closed_dt - planned_dt).total_seconds()
                # Closed more than 24h after planned start
                if diff > 86400:
                    out_of_window += 1
                    findings.append({
                        "severity": "info",
                        "title": f"{number} — Closed {diff/3600:.0f}h after planned start",
                        "detail": desc,
                        "ci": None,
                    })
                # Closed before planned start
                elif diff < -3600:
                    out_of_window += 1
                    findings.append({
                        "severity": "warning",
                        "title": f"{number} — Closed {abs(diff)/3600:.0f}h BEFORE planned start",
                        "detail": f"Executed outside maintenance window. {desc}",
                        "ci": None,
                    })

        # Emergency/assess state changes without assignment
        if state == "assess":
            owner = cr.get("assignedTo", cr.get("owner", ""))
            age = _age_days(planned)
            if age > 14:
                findings.append({
                    "severity": "warning",
                    "title": f"{number} — In 'assess' for {age:.0f} days",
                    "detail": f"Owner: {owner or 'NONE'}. {desc}",
                    "ci": None,
                })
                emergency_no_review += 1

    score = 100 - out_of_window * 3 - emergency_no_review * 10
    return {
        "score": max(0, min(100, score)),
        "findings": findings,
        "summary": f"{out_of_window} out-of-window, {emergency_no_review} stale assess",
    }


# ── 30. Patching Coverage & Cadence ──────────────────────────────────────

def analyze_patching(views: dict) -> dict:
    """Track patching changes and identify servers overdue for patches."""
    cr_items = views.get("ChangeRequestView", [])
    if isinstance(cr_items, dict):
        cr_items = cr_items.get("data", [])
    if not cr_items:
        return {"score": 100, "findings": [], "summary": "No change data"}

    findings = []
    patched_servers = {}  # server -> latest patch date
    patch_pattern = re.compile(r'[Pp]atching:\s*(\S+)')

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", item)
        if not isinstance(cr, dict):
            continue
        desc = (cr.get("shortDescription", cr.get("description", "")) or "")
        state = (cr.get("state") or "").lower()
        planned = cr.get("plannedStartDate") or cr.get("planned start", "")

        m = patch_pattern.search(desc)
        if m:
            server = _short(m.group(1))
            if server not in patched_servers or planned > patched_servers[server]:
                patched_servers[server] = planned

    # Check for servers in LB pools that haven't been patched
    pool_members = views.get("LoadBalancingPoolMemberView", [])
    if isinstance(pool_members, dict):
        pool_members = pool_members.get("data", [])
    # Also check AppServerView
    app_servers = views.get("AppServerView", [])
    if isinstance(app_servers, dict):
        app_servers = app_servers.get("data", [])

    all_servers = set()
    for pm in pool_members:
        if isinstance(pm, dict):
            host = pm.get("appServer", pm.get("host", ""))
            if isinstance(host, dict):
                host = host.get("name", "")
            all_servers.add(_short(host))
    for srv in app_servers:
        if isinstance(srv, dict):
            name = _short(srv.get("name", ""))
            life = (srv.get("lifeCycle") or "").lower()
            if life not in ("decommissioned",) and name:
                all_servers.add(name)

    # Identify unpatched servers
    if patched_servers and all_servers:
        unpatched = all_servers - set(patched_servers.keys())
        # Filter out non-server names
        unpatched = {s for s in unpatched if s and len(s) > 3}
        if unpatched and len(unpatched) < len(all_servers):
            # Only flag if some servers WERE patched (indicating a patching campaign)
            if len(unpatched) > 10:
                findings.append({
                    "severity": "info",
                    "title": f"{len(unpatched)} servers not in recent patching window",
                    "detail": f"Sample: {', '.join(sorted(unpatched)[:5])}...",
                    "ci": None,
                })
            elif unpatched:
                findings.append({
                    "severity": "info",
                    "title": f"{len(unpatched)} server(s) not patched: {', '.join(sorted(unpatched)[:8])}",
                    "detail": "Not seen in recent Patching-CHG records",
                    "ci": None,
                })

    # Summary stats
    if patched_servers:
        dates = [v for v in patched_servers.values() if v]
        oldest = min(dates) if dates else "?"
        newest = max(dates) if dates else "?"
        findings.append({
            "severity": "info",
            "title": f"Patching window: {oldest[:10]} to {newest[:10]}",
            "detail": f"{len(patched_servers)} servers patched",
            "ci": None,
        })

    return {
        "score": 100,  # Informational
        "findings": findings,
        "summary": f"{len(patched_servers)} servers patched, {len(all_servers)} total servers tracked",
    }


# ── 31. Node Restart & Uptime Distribution ───────────────────────────────

def analyze_node_restarts(views: dict) -> dict:
    """Build restart heatmap from JVM thread uptimes and node start times."""
    jvm_threads = views.get("JvmThreadView", [])
    if isinstance(jvm_threads, dict):
        jvm_threads = jvm_threads.get("data", [])
    nodes = views.get("NodeView", [])
    if isinstance(nodes, dict):
        nodes = nodes.get("data", [])

    findings = []
    node_uptimes = {}  # node -> max_thread_uptime_hours

    for t in jvm_threads:
        if not isinstance(t, dict):
            continue
        node_name = _short(_name(t.get("node", "")))
        proc = t.get("process", {}) or {}
        cpu_time = _safe_float(proc.get("time"))
        if node_name and cpu_time > 0:
            existing = node_uptimes.get(node_name, 0)
            node_uptimes[node_name] = max(existing, cpu_time / 3600)

    if not node_uptimes:
        return {"score": 100, "findings": [], "summary": "No JVM thread data"}

    uptimes = list(node_uptimes.values())
    avg_uptime = sum(uptimes) / len(uptimes)

    # Detect frequently-restarted nodes (very low uptime vs avg)
    for node, hours in node_uptimes.items():
        if hours < 2 and avg_uptime > 24:
            findings.append({
                "severity": "warning",
                "title": f"{node} — Recently restarted (uptime {hours:.1f}h vs avg {avg_uptime:.0f}h)",
                "detail": "May indicate instability or recent deployment",
                "ci": node,
            })

    # Detect very long-running nodes (no restart in months)
    for node, hours in node_uptimes.items():
        if hours > 2160:  # 90+ days
            findings.append({
                "severity": "info",
                "title": f"{node} — Running for {hours/24:.0f} days without restart",
                "detail": "May be missing patches or memory leak accumulation",
                "ci": node,
            })

    # Detect restart clustering (multiple nodes with very similar low uptimes)
    low_uptime_nodes = [n for n, h in node_uptimes.items() if h < 6]
    if len(low_uptime_nodes) >= 3:
        findings.append({
            "severity": "warning",
            "title": f"Restart cascade: {len(low_uptime_nodes)} nodes restarted recently",
            "detail": ", ".join(low_uptime_nodes[:8]),
            "ci": None,
        })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10)
    return {
        "score": score,
        "findings": findings,
        "summary": f"{len(node_uptimes)} nodes tracked, avg uptime {avg_uptime:.0f}h",
    }


# ═══════════════════════════════════════════════════════════════════════════
# DATA INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

# ── 32. Replication Data Consistency Checker ──────────────────────────────

def analyze_replication_consistency(views: dict) -> dict:
    """Check for replication consistency issues from DB activity patterns."""
    iv = _iv(views)
    query_dbs = iv.get("queryDatabases", [])
    qdv = views.get("PsqlDatabaseQueryView", [])
    if isinstance(qdv, dict):
        qdv = qdv.get("data", [])
    repl_dbs = iv.get("psqlReplicationDatabases", [])
    if not repl_dbs:
        repl_dbs = views.get("PsqlDatabaseReplicationView", [])

    findings = []

    # Check for active maintenance queries (columnstore rebuilds, reindex, vacuum)
    maintenance_patterns = re.compile(
        r'(columnstore|reindex|vacuum|analyze|rebuild|cluster\s+)', re.IGNORECASE
    )
    all_queries = []
    for db in query_dbs:
        if not isinstance(db, dict):
            continue
        db_name = _short(db.get("name", ""))
        q = db.get("queries", {})
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": db_name})
        elif isinstance(q, list):
            for qi in q:
                if isinstance(qi, dict) and qi.get("query"):
                    all_queries.append({**qi, "_db": db_name})
    for item in qdv:
        if not isinstance(item, dict):
            continue
        q = item.get("queries", item)
        if isinstance(q, dict) and q.get("query"):
            all_queries.append({**q, "_db": _short(item.get("name", ""))})

    maintenance_ops = []
    for q in all_queries:
        query_text = str(q.get("query", ""))
        if maintenance_patterns.search(query_text):
            time_val = q.get("time", "")
            secs = _parse_duration(str(time_val)) if isinstance(time_val, str) and ":" in time_val else _safe_float(time_val)
            maintenance_ops.append({
                "db": q.get("_db", "?"),
                "query": query_text[:80],
                "duration": secs,
            })

    for op in maintenance_ops:
        sev = "warning" if op["duration"] > 300 else "info"
        findings.append({
            "severity": sev,
            "title": f"{op['db']} — Active maintenance: {op['query']}",
            "detail": f"Running for {op['duration']:.0f}s",
            "ci": op["db"],
        })

    # Check for high cluster state lag across replicas (indicates WAL replay drift)
    high_lag_replicas = []
    for rd in repl_dbs:
        if not isinstance(rd, dict):
            continue
        host = _short(_name(rd.get("host", "")))
        cluster_lag = _safe_int(rd.get("sysClusterStateLag"))
        if cluster_lag > 500000:
            high_lag_replicas.append((host, cluster_lag))

    if high_lag_replicas:
        for host, lag in high_lag_replicas:
            findings.append({
                "severity": "warning",
                "title": f"{host} — Cluster state lag: {lag:,}",
                "detail": "High WAL replay drift may cause data inconsistency on failover",
                "ci": host,
            })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10
                - len([f for f in findings if f["severity"] == "critical"]) * 20)
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(maintenance_ops)} maintenance ops, {len(high_lag_replicas)} high-lag replicas",
    }


# ── 33. Orphaned Resource Detector ───────────────────────────────────────

def analyze_orphaned_resources(views: dict) -> dict:
    """Detect orphaned CIs, dangling references, and stale configurations."""
    iv = _iv(views)
    findings = []

    # Check for decommissioned servers still referenced in replication topology
    db_servers = iv.get("dbServers", [])
    if not db_servers:
        db_servers = views.get("PsqlDbserverView", [])
    repl_dbs = iv.get("psqlReplicationDatabases", [])
    if not repl_dbs:
        repl_dbs = views.get("PsqlDatabaseReplicationView", [])

    decom_servers = set()
    for ds in db_servers:
        if not isinstance(ds, dict):
            continue
        life = (ds.get("lifeCycle") or "").lower()
        if life == "decommissioned":
            decom_servers.add(_short(_name(ds)))

    # Check if decommissioned servers appear in replication topology
    for rd in repl_dbs:
        if not isinstance(rd, dict):
            continue
        host = _short(_name(rd.get("host", "")))
        partner = _short(_name(rd.get("replicationPartner", "")))
        if host in decom_servers:
            findings.append({
                "severity": "warning",
                "title": f"{host} — Decommissioned but active in replication topology",
                "detail": f"Partner: {partner}",
                "ci": host,
            })
        if partner in decom_servers:
            findings.append({
                "severity": "warning",
                "title": f"{host} — Replication partner {partner} is decommissioned",
                "detail": "Dangling replication reference",
                "ci": host,
            })

    # Check for LB pool members pointing to non-active servers
    pools = views.get("LoadBalancingPoolView", [])
    if isinstance(pools, dict):
        pools = pools.get("data", [])
    active_nodes = set()
    nodes = views.get("NodeView", [])
    if isinstance(nodes, dict):
        nodes = nodes.get("data", [])
    for nd in nodes:
        if isinstance(nd, dict) and nd.get("isActive"):
            active_nodes.add(_name(nd).lower())

    for pool in pools:
        if not isinstance(pool, dict):
            continue
        pool_name = pool.get("name", "?")
        members = pool.get("members", [])
        if not isinstance(members, list):
            continue
        for m in members:
            if not isinstance(m, dict):
                continue
            node = _name(m.get("node", "")).lower()
            enabled = m.get("isEnabled", True)
            if enabled and node and active_nodes and node not in active_nodes:
                findings.append({
                    "severity": "info",
                    "title": f"{pool_name} — Pool member {_short(node)} not in active node list",
                    "detail": "May be orphaned or pre-provisioned",
                    "ci": _short(node),
                })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10
                - len([f for f in findings if f["severity"] == "critical"]) * 20)
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(decom_servers)} decommissioned servers, {len(findings)} orphaned references",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CAPACITY PLANNING
# ═══════════════════════════════════════════════════════════════════════════

# ── 34. Connection Pool Right-Sizing ─────────────────────────────────────

def analyze_connection_pool_sizing(views: dict) -> dict:
    """Analyze connection pool utilization and recommend right-sizing."""
    iv = _iv(views)
    databases = iv.get("psqlDatabases", [])
    if not databases:
        databases = views.get("PsqlDatabaseView", [])
        if isinstance(databases, dict):
            databases = databases.get("data", [])
    db_pools = views.get("DatabasePoolView", [])
    if isinstance(db_pools, dict):
        db_pools = db_pools.get("data", [])

    findings = []

    # Analyze per-database connection distribution
    db_connections = {}  # key -> {all, app, usage, capacity}
    for db in databases:
        if not isinstance(db, dict):
            continue
        host = _short(_name(db.get("host", "")))
        port = db.get("port", "?")
        all_conn = _safe_int(db.get("allConnections"))
        app_conn = _safe_int(db.get("appConnections"))
        usage = db.get("usage", "")
        cap = db.get("capacitySize", "")
        key = f"{host}:{port}"
        db_connections[key] = {
            "all": all_conn, "app": app_conn,
            "usage": usage, "capacity": cap,
        }

    if not db_connections:
        return {"score": 100, "findings": [], "summary": "No database connection data"}

    # Compare primary vs replica connection ratios
    primaries = {k: v for k, v in db_connections.items() if "primary" in v["usage"].lower()}
    replicas = {k: v for k, v in db_connections.items()
                if "read" in v["usage"].lower() or "standby" in v["usage"].lower()}

    for k, v in primaries.items():
        if v["all"] > 1000:
            findings.append({
                "severity": "warning" if v["all"] > 2000 else "info",
                "title": f"{k} — Primary with {v['all']} connections (app: {v['app']})",
                "detail": f"Capacity: {v['capacity']}, Usage: {v['usage']}",
                "ci": _short(k.split(":")[0]),
            })

    # Check for idle connection waste (all_conn >> app_conn)
    for k, v in db_connections.items():
        if v["all"] > 100 and v["app"] > 0:
            overhead = v["all"] - v["app"]
            if overhead > v["app"] * 2:
                findings.append({
                    "severity": "info",
                    "title": f"{k} — High connection overhead: {overhead} non-app connections",
                    "detail": f"Total: {v['all']}, App: {v['app']}. System/replication connections may be excessive",
                    "ci": _short(k.split(":")[0]),
                })

    # Analyze DB pool utilization from nodes
    pool_stats = {}  # pool_name -> [(cur, max)]
    for pool in db_pools:
        if not isinstance(pool, dict):
            continue
        pools_data = pool.get("databasePools", {})
        if isinstance(pools_data, dict):
            for pname, pval in pools_data.items():
                if not isinstance(pval, str) or "/" not in pval:
                    continue
                parts = pval.split("/")
                try:
                    cur, mx = int(parts[0].strip()), int(parts[1].strip())
                    pool_stats.setdefault(pname, []).append((cur, mx))
                except (ValueError, IndexError):
                    continue

    # Find pools that are consistently underutilized (over-provisioned)
    for pname, entries in pool_stats.items():
        if len(entries) >= 2:
            avg_ratio = sum(c / m for c, m in entries if m > 0) / len(entries)
            if avg_ratio < 0.1 and all(m >= 32 for _, m in entries):
                total_max = sum(m for _, m in entries)
                total_cur = sum(c for c, _ in entries)
                findings.append({
                    "severity": "info",
                    "title": f"Pool '{pname}' over-provisioned: avg {avg_ratio:.0%} utilization",
                    "detail": f"Total: {total_cur}/{total_max} across {len(entries)} nodes",
                    "ci": None,
                })

    return {
        "score": max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10),
        "findings": findings,
        "summary": f"{len(db_connections)} databases, {len(pool_stats)} connection pools analyzed",
    }


# ── 35. Node Scaling Recommendation Engine ───────────────────────────────

def analyze_node_scaling(views: dict) -> dict:
    """Recommend scaling based on CPU, memory, and request distribution."""
    iv = _iv(views)
    nodes = iv.get("nodes", [])
    if not nodes:
        nodes = views.get("NodeView", [])
        if isinstance(nodes, dict):
            nodes = nodes.get("data", [])
    if not nodes:
        return {"score": 100, "findings": [], "summary": "No node data"}

    findings = []
    ui_nodes = []
    worker_nodes = []
    dc_node_counts = {}  # dc -> {ui: n, worker: n}

    for nd in nodes:
        if not isinstance(nd, dict) or not nd.get("isActive"):
            continue
        ntype = (nd.get("type") or "").lower()
        stats = nd.get("nodeStats", {}) or {}
        cpu = _safe_float(stats.get("cpu"))
        rss = _safe_float(stats.get("residentMemoryMb"))
        host = _name(nd.get("host", ""))
        dc = _dc_from_server(host) if isinstance(host, str) else ""

        dc_node_counts.setdefault(dc, {"ui": 0, "worker": 0})
        entry = {"name": nd.get("name", "?"), "cpu": cpu, "rss": rss, "dc": dc}

        if "worker" in ntype:
            worker_nodes.append(entry)
            dc_node_counts[dc]["worker"] += 1
        else:
            ui_nodes.append(entry)
            dc_node_counts[dc]["ui"] += 1

    total_ui = len(ui_nodes)
    total_worker = len(worker_nodes)

    # Analyze CPU distribution across UI nodes
    if ui_nodes:
        ui_cpus = [n["cpu"] for n in ui_nodes if n["cpu"] > 0]
        if ui_cpus:
            avg_cpu = sum(ui_cpus) / len(ui_cpus)
            max_cpu = max(ui_cpus)
            if avg_cpu > 600:
                findings.append({
                    "severity": "warning",
                    "title": f"UI nodes avg CPU {avg_cpu:.0f}% — consider adding nodes",
                    "detail": f"{total_ui} UI nodes, max CPU: {max_cpu:.0f}%",
                    "ci": None,
                })
            elif avg_cpu < 100 and total_ui > 10:
                findings.append({
                    "severity": "info",
                    "title": f"UI nodes avg CPU {avg_cpu:.0f}% — potentially over-provisioned",
                    "detail": f"{total_ui} UI nodes may be more than needed",
                    "ci": None,
                })

    # Worker/UI ratio recommendation
    if total_ui > 0 and total_worker > 0:
        ratio = total_worker / total_ui
        if ratio < 0.3:
            findings.append({
                "severity": "info",
                "title": f"Low worker/UI ratio: {ratio:.2f} ({total_worker}W / {total_ui}UI)",
                "detail": "Background processing capacity may be insufficient for heavy workloads",
                "ci": None,
            })
        elif ratio > 1.5:
            findings.append({
                "severity": "info",
                "title": f"High worker/UI ratio: {ratio:.2f} ({total_worker}W / {total_ui}UI)",
                "detail": "Unusual — verify worker nodes are needed",
                "ci": None,
            })

    # DC balance
    for dc, counts in dc_node_counts.items():
        if not dc:
            continue
        total = counts["ui"] + counts["worker"]
        findings.append({
            "severity": "info",
            "title": f"DC {dc}: {counts['ui']} UI + {counts['worker']} worker = {total} nodes",
            "detail": "Node distribution per datacenter",
            "ci": None,
        })

    return {
        "score": max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 15),
        "findings": findings,
        "summary": f"{total_ui} UI + {total_worker} worker nodes across {len(dc_node_counts)} DCs",
    }


# ═══════════════════════════════════════════════════════════════════════════
# INCIDENT & ALERT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

# ── 36. Alert-to-Incident Closure Velocity ───────────────────────────────

def analyze_alert_closure_velocity(views: dict) -> dict:
    """Track how quickly alerts get tasks assigned and resolved."""
    alert_items = views.get("AlertView", [])
    if isinstance(alert_items, dict):
        alert_items = alert_items.get("data", [])
    if not alert_items:
        return {"score": 100, "findings": [], "summary": "No alerts"}

    findings = []
    unassigned_alerting = 0
    task_assigned_count = 0
    total_alerting = 0
    old_with_task = []

    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ad = item.get("alerts", item)
        if not isinstance(ad, dict):
            continue

        state = (ad.get("state") or "").lower()
        number = ad.get("number", "?")
        ci = _short(item.get("name", ad.get("ci", "")))
        desc = (ad.get("description", ad.get("type", "")) or "")[:50]
        assigned = ad.get("assignedTo", ad.get("assigned", ""))
        task = ad.get("hiTask", ad.get("hi task", ""))
        time_str = ad.get("initialRemoteTime", ad.get("event", ""))
        age = _age_days(time_str)

        if state in ("alerting", "message alert"):
            total_alerting += 1
            if not assigned:
                unassigned_alerting += 1
            if task:
                task_assigned_count += 1
                if age > 90:
                    old_with_task.append({
                        "number": number, "ci": ci, "desc": desc,
                        "task": task, "age": age,
                    })

    # Flag old alerts with tasks that are still open
    for alert in old_with_task[:5]:
        findings.append({
            "severity": "warning",
            "title": f"{alert['number']} — Open {alert['age']:.0f} days with task {alert['task']}",
            "detail": f"CI: {alert['ci']}, Type: {alert['desc']}",
            "ci": alert["ci"],
        })

    if total_alerting > 0:
        pct_unassigned = unassigned_alerting / total_alerting * 100
        pct_tasked = task_assigned_count / total_alerting * 100
        if pct_unassigned > 50:
            findings.append({
                "severity": "warning",
                "title": f"{pct_unassigned:.0f}% of alerting alerts are UNASSIGNED",
                "detail": f"{unassigned_alerting}/{total_alerting} alerts have no assignee",
                "ci": None,
            })
        findings.append({
            "severity": "info",
            "title": f"Alert triage: {pct_tasked:.0f}% have tasks, {pct_unassigned:.0f}% unassigned",
            "detail": f"{total_alerting} alerting, {task_assigned_count} with tasks, {unassigned_alerting} unassigned",
            "ci": None,
        })

    score = max(0, 100 - len(old_with_task) * 10 - (15 if unassigned_alerting > total_alerting / 2 else 0))
    return {
        "score": max(0, min(100, score)),
        "findings": findings,
        "summary": f"{total_alerting} alerting, {task_assigned_count} with tasks, {unassigned_alerting} unassigned",
    }


# ── 37. Recurring Incident Pattern Detector ──────────────────────────────

def analyze_recurring_incidents(views: dict) -> dict:
    """Detect repeating alert patterns — same type on different CIs or same CI different types."""
    alert_items = views.get("AlertView", [])
    if isinstance(alert_items, dict):
        alert_items = alert_items.get("data", [])
    if not alert_items:
        return {"score": 100, "findings": [], "summary": "No alerts"}

    findings = []
    type_cis = {}  # alert_type -> set(cis)
    ci_types = {}  # ci -> set(alert_types)

    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ad = item.get("alerts", item)
        if not isinstance(ad, dict):
            continue
        ci = _short(item.get("name", ad.get("ci", "")))
        atype = (ad.get("description", ad.get("type", "")) or "")[:60]
        state = (ad.get("state") or "").lower()
        if state not in ("alerting", "message alert"):
            continue
        if atype:
            type_cis.setdefault(atype, set()).add(ci)
        if ci:
            ci_types.setdefault(ci, set()).add(atype)

    # Same alert type across multiple CIs (spreading problem)
    for atype, cis in type_cis.items():
        if len(cis) >= 3:
            findings.append({
                "severity": "warning",
                "title": f"Spreading: '{atype}' on {len(cis)} CIs",
                "detail": ", ".join(sorted(cis)[:6]),
                "ci": None,
            })

    # Same CI with many alert types (compound failure)
    for ci, types in ci_types.items():
        if len(types) >= 3:
            findings.append({
                "severity": "warning",
                "title": f"{ci} — {len(types)} distinct alert types (compound failure)",
                "detail": "; ".join(sorted(types)[:4]),
                "ci": ci,
            })

    score = max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10
                - len([f for f in findings if f["severity"] == "critical"]) * 20)
    return {
        "score": max(0, score),
        "findings": findings,
        "summary": f"{len(type_cis)} alert types, {len(ci_types)} affected CIs",
    }


# ═══════════════════════════════════════════════════════════════════════════
# DEPLOYMENT QUALITY
# ═══════════════════════════════════════════════════════════════════════════

# ── 38. Update Set Conflict & Dependency Analyzer ────────────────────────

def analyze_update_set_conflicts(views: dict) -> dict:
    """Check update sets for naming conflicts and deployment wave risks."""
    us_items = views.get("UpdateSetView", [])
    if isinstance(us_items, dict):
        us_items = us_items.get("data", [])
    if not us_items:
        return {"score": 100, "findings": [], "summary": "No update sets"}

    findings = []

    # Analyze naming conventions
    name_prefixes = {}  # prefix -> [update set names]
    for us in us_items:
        if not isinstance(us, dict):
            continue
        name = us.get("name", "")
        # Extract prefix (first word or delimited segment)
        prefix = name.split(" ")[0].split("-")[0].split("_")[0] if name else "?"
        name_prefixes.setdefault(prefix, []).append(name)

    # Flag waves from different sources deployed simultaneously
    time_buckets = {}
    for us in us_items:
        if not isinstance(us, dict):
            continue
        install_date = us.get("installDate", "")
        name = us.get("name", "?")
        dt = _ts(install_date)
        if dt:
            bucket = dt.strftime("%Y-%m-%d %H:%M")[:15] + "0"
            time_buckets.setdefault(bucket, []).append(name)

    # Find mixed-origin deployment waves
    for bucket, items in time_buckets.items():
        if len(items) >= 3:
            prefixes = set()
            for item in items:
                p = item.split(" ")[0].split("-")[0].split("_")[0]
                prefixes.add(p)
            if len(prefixes) >= 2:
                findings.append({
                    "severity": "warning",
                    "title": f"Mixed deployment wave at {bucket}: {len(items)} sets from {len(prefixes)} sources",
                    "detail": f"Sources: {', '.join(sorted(prefixes)[:5])}. Sets: {', '.join(items[:3])}...",
                    "ci": None,
                })

    # Check for UAT/test sets deployed to production
    test_pattern = re.compile(r'(UAT|test|staging|dev|sandbox)', re.IGNORECASE)
    for us in us_items:
        if not isinstance(us, dict):
            continue
        name = us.get("name", "")
        if test_pattern.search(name):
            findings.append({
                "severity": "info",
                "title": f"Test/UAT update set deployed: {name[:60]}",
                "detail": f"Install date: {us.get('installDate', '?')}",
                "ci": None,
            })

    return {
        "score": max(0, 100 - len([f for f in findings if f["severity"] == "warning"]) * 10),
        "findings": findings,
        "summary": f"{len(us_items)} update sets, {len(time_buckets)} deployment windows, {len(name_prefixes)} sources",
    }


# ── 39. Post-Deployment Health Delta ─────────────────────────────────────

def analyze_post_deploy_health(views: dict) -> dict:
    """Correlate deployment waves with alert and query health changes."""
    us_items = views.get("UpdateSetView", [])
    if isinstance(us_items, dict):
        us_items = us_items.get("data", [])
    alert_items = views.get("AlertView", [])
    if isinstance(alert_items, dict):
        alert_items = alert_items.get("data", [])

    findings = []

    # Get deployment timestamps
    deploy_times = []
    for us in us_items:
        if not isinstance(us, dict):
            continue
        install_date = us.get("installDate", "")
        dt = _ts(install_date)
        if dt:
            deploy_times.append(dt)

    if not deploy_times:
        return {"score": 100, "findings": [], "summary": "No deployment data"}

    latest_deploy = max(deploy_times)
    deploy_window_start = latest_deploy - timedelta(hours=1)
    now = datetime.now(timezone.utc)
    hours_since_deploy = (now - latest_deploy).total_seconds() / 3600

    # Count alerts that started after the latest deployment wave
    post_deploy_alerts = 0
    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ad = item.get("alerts", item)
        if not isinstance(ad, dict):
            continue
        time_str = ad.get("initialRemoteTime", ad.get("event", ""))
        alert_dt = _ts(time_str)
        if alert_dt and alert_dt >= deploy_window_start:
            post_deploy_alerts += 1

    if post_deploy_alerts > 0 and hours_since_deploy < 48:
        sev = "warning" if post_deploy_alerts >= 3 else "info"
        findings.append({
            "severity": sev,
            "title": f"{post_deploy_alerts} alert(s) fired within {hours_since_deploy:.0f}h of latest deployment",
            "detail": f"Latest deploy: {latest_deploy.strftime('%Y-%m-%d %H:%M')} UTC",
            "ci": None,
        })

    # Deployment recency
    if hours_since_deploy < 2:
        findings.append({
            "severity": "info",
            "title": f"Active deployment: {len(deploy_times)} update sets deployed in last 2 hours",
            "detail": "Instance may be in transition state",
            "ci": None,
        })

    return {
        "score": max(0, 100 - post_deploy_alerts * 10),
        "findings": findings,
        "summary": f"Latest deploy {hours_since_deploy:.0f}h ago, {post_deploy_alerts} post-deploy alerts",
    }


# ═══════════════════════════════════════════════════════════════════════════
# REPORTING & EXECUTIVE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

# ── 40. Executive Risk Narrative Generator ───────────────────────────────

def generate_executive_narrative(views: dict, analyzer_results: dict) -> dict:
    """Auto-generate a plain-English executive risk summary."""
    iv = _iv(views)
    findings = []

    # Instance identity
    inst_name = iv.get("name", "Unknown")
    customer = iv.get("customer", "")
    if isinstance(customer, dict):
        customer = customer.get("name", "")
    version_raw = iv.get("version", "")
    version = version_raw.get("name", "") if isinstance(version_raw, dict) else str(version_raw)
    is_sharded = iv.get("hasShardedDatabase", False)
    is_dedicated = iv.get("isDedicated", False)
    is_psql = iv.get("isPsql", False)
    dc_raw = iv.get("primaryDatacenter", "")
    dc = dc_raw.get("name", "") if isinstance(dc_raw, dict) else str(dc_raw)

    # Topo summary
    topo_parts = []
    if is_sharded:
        topo_parts.append("sharded")
    if is_dedicated:
        topo_parts.append("dedicated")
    topo_parts.append("PSQL" if is_psql else "MySQL")
    topo = ", ".join(topo_parts)

    # Count critical findings across all analyzers
    all_critical = []
    all_warning = []
    for key, result in analyzer_results.items():
        for f in result.get("findings", []):
            if f.get("severity") == "critical":
                all_critical.append(f)
            elif f.get("severity") == "warning":
                all_warning.append(f)

    # Build narrative
    narrative_parts = []
    narrative_parts.append(
        f"{inst_name} ({customer}) is a {topo} instance in {dc} running {version}."
    )

    if all_critical:
        narrative_parts.append(
            f"**{len(all_critical)} critical risk(s)**: "
            + "; ".join(f["title"][:80] for f in all_critical[:5])
            + ("..." if len(all_critical) > 5 else "")
        )

    if all_warning:
        narrative_parts.append(f"{len(all_warning)} warning(s) identified.")

    # Key scores
    score_summary = []
    for key in ("db_capacity", "replication", "app_nodes", "backup_integrity"):
        result = analyzer_results.get(key, {})
        s = result.get("score", -1)
        if s >= 0:
            label = key.replace("_", " ").title()
            score_summary.append(f"{label}: {s}/100")
    if score_summary:
        narrative_parts.append("Key scores — " + ", ".join(score_summary))

    narrative = " ".join(narrative_parts)

    findings.append({
        "severity": "info",
        "title": "Executive Summary",
        "detail": narrative,
        "ci": None,
    })

    # Determine overall risk level
    if len(all_critical) >= 5:
        risk_level = "HIGH"
        sev = "critical"
    elif len(all_critical) >= 2:
        risk_level = "ELEVATED"
        sev = "warning"
    elif all_critical:
        risk_level = "MODERATE"
        sev = "info"
    else:
        risk_level = "LOW"
        sev = "info"

    findings.insert(0, {
        "severity": sev,
        "title": f"Overall Risk Level: {risk_level}",
        "detail": f"{len(all_critical)} critical, {len(all_warning)} warnings across all analyzers",
        "ci": None,
    })

    return {
        "score": max(0, 100 - len(all_critical) * 10 - len(all_warning) * 2),
        "findings": findings,
        "summary": narrative[:200],
        "narrative": narrative,
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE SCORING
# ═══════════════════════════════════════════════════════════════════════════

# ── 19. Instance Health Composite Score ───────────────────────────────────

_DIMENSION_WEIGHTS = {
    "db_health": 0.15,
    "app_health": 0.15,
    "alert_posture": 0.12,
    "change_risk": 0.12,
    "backup_integrity": 0.10,
    "infrastructure": 0.12,
    "data_growth": 0.08,
    "security_compliance": 0.08,
    "operational_patterns": 0.08,
}

_DIMENSION_COMPONENTS = {
    "db_health": ["db_capacity", "connections", "shard_balance", "queries", "connection_pool_sizing", "parallel_queries"],
    "app_health": ["app_nodes", "request_latency", "worker_balance", "semaphore_exhaustion", "transaction_volume", "node_scaling", "node_restarts"],
    "alert_posture": ["alert_correlation", "alert_staleness", "alert_closure_velocity", "recurring_incidents"],
    "change_risk": ["change_velocity", "update_sets", "reconvergence", "maintenance_windows", "update_set_conflicts", "post_deploy_health"],
    "backup_integrity": ["backup_integrity", "backup_performance"],
    "infrastructure": ["server_lifecycle", "replication", "failover_readiness", "lb_pools", "replication_consistency", "orphaned_resources"],
    "data_growth": ["table_growth"],
    "security_compliance": ["encryption_compliance", "service_accounts", "oidc_sso"],
    "operational_patterns": ["patching", "cache_effectiveness"],
}


def compute_composite_score(analyzer_results: dict) -> dict:
    """Compute weighted composite score from all analyzer results."""
    dimensions = {}

    for dim_key, components in _DIMENSION_COMPONENTS.items():
        comp_scores = []
        for comp in components:
            result = analyzer_results.get(comp, {})
            s = result.get("score", -1)
            if s >= 0:
                comp_scores.append(s)
        if comp_scores:
            dim_score = int(sum(comp_scores) / len(comp_scores))
        else:
            dim_score = -1
        dimensions[dim_key] = {
            "score": dim_score,
            "weight": _DIMENSION_WEIGHTS[dim_key],
            "components": components,
        }

    # Weighted overall
    weighted_sum = 0
    weight_sum = 0
    for dim_key, dim_data in dimensions.items():
        if dim_data["score"] >= 0:
            weighted_sum += dim_data["score"] * dim_data["weight"]
            weight_sum += dim_data["weight"]

    overall = int(weighted_sum / weight_sum) if weight_sum > 0 else -1

    # Collect all findings sorted by severity
    all_findings = []
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    for key, result in analyzer_results.items():
        for f in result.get("findings", []):
            f["analyzer"] = key
            all_findings.append(f)
    all_findings.sort(key=lambda f: sev_order.get(f.get("severity", "info"), 3))

    # Red flags = critical findings
    red_flags = [f for f in all_findings if f["severity"] == "critical"]

    return {
        "overall": overall,
        "dimensions": dimensions,
        "findings": all_findings,
        "red_flags": red_flags,
        "finding_counts": {
            "critical": len([f for f in all_findings if f["severity"] == "critical"]),
            "warning": len([f for f in all_findings if f["severity"] == "warning"]),
            "info": len([f for f in all_findings if f["severity"] == "info"]),
        },
    }


# ── 20. Anomaly Detection via Cross-Signal Correlation ────────────────────

def analyze_anomalies(views: dict, analyzer_results: dict) -> dict:
    """Detect multi-signal anomaly patterns."""
    findings = []

    # Pattern: Server on hold + reconvergence + replication issues
    hold_servers = set()
    lifecycle_result = analyzer_results.get("server_lifecycle", {})
    for f in lifecycle_result.get("findings", []):
        if "hold" in f.get("title", "").lower():
            hold_servers.add(f.get("ci", ""))

    reconv_cis = set()
    reconv_result = analyzer_results.get("reconvergence", {})
    for f in reconv_result.get("findings", []):
        if f.get("ci"):
            reconv_cis.add(f["ci"])

    repl_broken = set()
    repl_result = analyzer_results.get("replication", {})
    for f in repl_result.get("findings", []):
        if "broken" in f.get("title", "").lower():
            repl_broken.add(f.get("ci", ""))

    # Pattern 1: Hold + reconvergence = hardware EOL
    overlap = hold_servers & reconv_cis
    for ci in overlap:
        findings.append({
            "severity": "critical",
            "title": f"ANOMALY: {ci} — On hold + repeated reconvergence",
            "detail": "Pattern indicates potential hardware end-of-life",
            "ci": ci,
        })

    # Pattern 2: Long-running jobs + high CPU = runaway job
    long_jobs = set()
    worker_result = analyzer_results.get("worker_balance", {})
    for f in worker_result.get("findings", []):
        if f.get("ci") and "running" in f.get("title", "").lower():
            long_jobs.add(f["ci"])

    cpu_outliers = set()
    app_result = analyzer_results.get("app_nodes", {})
    for f in app_result.get("findings", []):
        if "cpu" in f.get("title", "").lower():
            cpu_outliers.add(f.get("ci", ""))

    runaway = long_jobs & cpu_outliers
    for ci in runaway:
        findings.append({
            "severity": "critical",
            "title": f"ANOMALY: {ci} — Long-running job + CPU spike",
            "detail": "Pattern indicates runaway job",
            "ci": ci,
        })

    # Pattern 3: Replication broken + DB performance alerts
    for ci in repl_broken:
        findings.append({
            "severity": "critical",
            "title": f"ANOMALY: {ci} — Replication broken",
            "detail": "Check for orphaned replication slots and data integrity",
            "ci": ci,
        })

    # Pattern 4: Capacity over + connection saturation
    cap_over = set()
    cap_result = analyzer_results.get("db_capacity", {})
    for f in cap_result.get("findings", []):
        if "over" in f.get("title", "").lower():
            cap_over.add(f.get("ci", ""))

    conn_high = set()
    conn_result = analyzer_results.get("connections", {})
    for f in conn_result.get("findings", []):
        if f.get("ci"):
            conn_high.add(f["ci"])

    for ci in cap_over & conn_high:
        findings.append({
            "severity": "critical",
            "title": f"ANOMALY: {ci} — Over capacity + high connections",
            "detail": "Compound resource exhaustion risk",
            "ci": ci,
        })

    return {
        "score": max(0, 100 - len(findings) * 20),
        "findings": findings,
        "summary": f"{len(findings)} cross-signal anomalies detected",
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

# Map of analyzer key -> function
_ANALYZERS = {
    # 1–18: Original analyzers
    "db_capacity": analyze_db_capacity,
    "replication": analyze_replication,
    "server_lifecycle": analyze_server_lifecycle,
    "failover_readiness": analyze_failover_readiness,
    "queries": analyze_queries,
    "connections": analyze_connections,
    "shard_balance": analyze_shard_balance,
    "app_nodes": analyze_app_nodes,
    "request_latency": analyze_request_latency,
    "worker_balance": analyze_worker_balance,
    "change_velocity": analyze_change_velocity,
    "update_sets": analyze_update_sets,
    "reconvergence": analyze_reconvergence,
    "alert_correlation": analyze_alert_correlation,
    "alert_staleness": analyze_alert_staleness,
    "backup_integrity": analyze_backup_integrity,
    "backup_performance": analyze_backup_performance,
    "lb_pools": analyze_lb_pools,
    # 21–30: Data Growth, Security, Performance, Operational
    "table_growth": analyze_table_growth,
    "transaction_volume": analyze_transaction_volume,
    "service_accounts": analyze_service_accounts,
    "encryption_compliance": analyze_encryption_compliance,
    "oidc_sso": analyze_oidc_sso,
    "semaphore_exhaustion": analyze_semaphore_exhaustion,
    "parallel_queries": analyze_parallel_queries,
    "cache_effectiveness": analyze_cache_effectiveness,
    "maintenance_windows": analyze_maintenance_windows,
    "patching": analyze_patching,
    # 31–39: Integrity, Capacity, Incidents, Deployment
    "node_restarts": analyze_node_restarts,
    "replication_consistency": analyze_replication_consistency,
    "orphaned_resources": analyze_orphaned_resources,
    "connection_pool_sizing": analyze_connection_pool_sizing,
    "node_scaling": analyze_node_scaling,
    "alert_closure_velocity": analyze_alert_closure_velocity,
    "recurring_incidents": analyze_recurring_incidents,
    "update_set_conflicts": analyze_update_set_conflicts,
    "post_deploy_health": analyze_post_deploy_health,
}

# Labels for UI
ANALYZER_LABELS = {
    # Original 1–18
    "db_capacity": "DB Capacity & Forecasting",
    "replication": "Replication Topology",
    "server_lifecycle": "Server Lifecycle & Hardware",
    "failover_readiness": "Multi-DC Failover Readiness",
    "queries": "Long-Running Query Impact",
    "connections": "Connection Saturation",
    "shard_balance": "Shard Balance",
    "app_nodes": "App Node Resources",
    "request_latency": "Request Latency",
    "worker_balance": "Worker/UI Balance & Jobs",
    "change_velocity": "Change Velocity & Blast Radius",
    "update_sets": "Update Set Deployment Waves",
    "reconvergence": "Reconvergence Detection",
    "alert_correlation": "Alert Correlation",
    "alert_staleness": "Alert Age & Staleness",
    "backup_integrity": "Backup Chain & RPO",
    "backup_performance": "Backup Performance Trends",
    "lb_pools": "LB Pool Symmetry",
    "collection_errors": "Collection Errors",
    # 21–30
    "table_growth": "Table Growth Rate",
    "transaction_volume": "Transaction Volume & Seasonality",
    "service_accounts": "Service Account Hygiene",
    "encryption_compliance": "Encryption & Compliance Posture",
    "oidc_sso": "OIDC/SSO Configuration Drift",
    "semaphore_exhaustion": "Semaphore & Thread Pool Exhaustion",
    "parallel_queries": "Parallel Query Escalation",
    "cache_effectiveness": "Cache Hit & Query Caching",
    "maintenance_windows": "Maintenance Window Compliance",
    "patching": "Patching Coverage & Cadence",
    # 31–40
    "node_restarts": "Node Restart & Uptime Distribution",
    "replication_consistency": "Replication Data Consistency",
    "orphaned_resources": "Orphaned Resource Detection",
    "connection_pool_sizing": "Connection Pool Right-Sizing",
    "node_scaling": "Node Scaling Recommendations",
    "alert_closure_velocity": "Alert-to-Incident Closure Velocity",
    "recurring_incidents": "Recurring Incident Patterns",
    "update_set_conflicts": "Update Set Conflict & Dependency",
    "post_deploy_health": "Post-Deployment Health Delta",
    "executive_narrative": "Executive Risk Narrative",
    "anomalies": "Cross-Signal Anomaly Detection",
}

ANALYZER_CATEGORIES = {
    "Infrastructure Health": ["db_capacity", "replication", "server_lifecycle", "failover_readiness", "replication_consistency", "orphaned_resources"],
    "Database Performance": ["queries", "connections", "shard_balance", "parallel_queries", "connection_pool_sizing", "cache_effectiveness"],
    "Application Layer": ["app_nodes", "request_latency", "worker_balance", "semaphore_exhaustion", "transaction_volume", "node_scaling", "node_restarts"],
    "Change & Deployment Risk": ["change_velocity", "update_sets", "reconvergence", "maintenance_windows", "update_set_conflicts", "post_deploy_health"],
    "Alert Intelligence": ["alert_correlation", "alert_staleness", "alert_closure_velocity", "recurring_incidents"],
    "Backup & Recovery": ["backup_integrity", "backup_performance"],
    "Network & Load Balancing": ["lb_pools"],
    "Data Growth & Trending": ["table_growth"],
    "Security & Compliance": ["service_accounts", "encryption_compliance", "oidc_sso"],
    "Operational Patterns": ["patching"],
    "Reporting": ["executive_narrative"],
    "Data Collection": ["collection_errors"],
}


def run_all_analyzers(views: dict, errors: list = None) -> dict:
    """Run all 40 analyzers and compute composite score.

    Args:
        views: dict of view_key -> data (either raw list or {data: list, ...})
        errors: list of ruckus collection error strings

    Returns:
        { "composite": {...}, "analyzers": {key: result}, "anomalies": {...} }
    """
    # Normalize views: extract "data" if wrapped
    normalized = {}
    for k, v in views.items():
        if isinstance(v, dict) and "data" in v:
            normalized[k] = v["data"]
        else:
            normalized[k] = v

    # Run each analyzer
    results = {}
    for key, func in _ANALYZERS.items():
        try:
            results[key] = func(normalized)
        except Exception as e:
            results[key] = {"score": -1, "findings": [], "summary": f"Error: {e}"}

    # Run collection error analyzer
    try:
        results["collection_errors"] = analyze_collection_errors(errors or [])
    except Exception as e:
        results["collection_errors"] = {"score": -1, "findings": [], "summary": f"Error: {e}"}

    # Run anomaly detection (needs other results)
    try:
        anomalies = analyze_anomalies(normalized, results)
        results["anomalies"] = anomalies
    except Exception as e:
        results["anomalies"] = {"score": -1, "findings": [], "summary": f"Error: {e}"}

    # Run executive narrative (needs other results)
    try:
        results["executive_narrative"] = generate_executive_narrative(normalized, results)
    except Exception as e:
        results["executive_narrative"] = {"score": -1, "findings": [], "summary": f"Error: {e}"}

    # Compute composite score
    composite = compute_composite_score(results)

    return {
        "composite": composite,
        "analyzers": results,
        "labels": ANALYZER_LABELS,
        "categories": ANALYZER_CATEGORIES,
    }
