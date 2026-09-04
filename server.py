"""
Flask server for Ruckus Aggregator dashboard and instance analysis.

Usage:
    python server.py [--port PORT]
"""
import argparse
import asyncio
import json
import os
import sys
import threading
import time as _time
from queue import Queue, Empty

from flask import Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from collector import _run_ruckus, _extract_json, _collect_instance, _save_cache, _load_cached, find_retry_candidates
from text_parser import parse_ruckus_text, merge_text_into_views
from analyzers import run_all_analyzers
import history

app = Flask(__name__)

# Comprehensive bindings for PsqlInstance analysis
ANALYSIS_BINDINGS = "iacuDdRqTpAnvsSwWCbPM"

# Human-readable labels for view keys
VIEW_LABELS = {
    "InstanceView": "Instance",
    "AlertView": "Alerts",
    "ChangeRequestView": "Change Requests",
    "UpdateSetView": "Update Sets",
    "PsqlDbserverView": "DB Servers",
    "MysqlDbserverView": "DB Servers",
    "PsqlDatabaseView": "Databases",
    "MysqlDatabaseView": "Databases",
    "PsqlDatabaseReplicationView": "DB Replication",
    "MysqlDatabaseReplicationView": "DB Replication",
    "PsqlDatabaseQueryView": "DB Queries",
    "MysqlDatabaseQueryView": "DB Queries",
    "PsqlDatabaseTableView": "Tables (Top 100)",
    "MysqlDatabaseTableView": "Tables (Top 100)",
    "DatabasePoolView": "Database Pools",
    "AppServerView": "App Servers",
    "NodeView": "Nodes",
    "ServletView": "Servlet Stats",
    "SemaphoreSetView": "Semaphore Sets",
    "SemaphoreView": "Semaphores",
    "SchedulerView": "Scheduler",
    "JobView": "Jobs",
    "JvmThreadView": "JVM Threads",
    "BackupView": "Backups",
    "LoadBalancingPoolView": "LB Pools",
    "LoadBalancingPoolMemberView": "LB Pool Members",
}


# ── Health Scoring ──────────────────────────────────────────────────────────


def _get_node_dc(node):
    """Extract datacenter from a node's host name (e.g. app146033.ord101 -> ord101)."""
    host = node.get("host", {})
    hname = host.get("name", "") if isinstance(host, dict) else str(host)
    parts = hname.split(".")
    if len(parts) >= 2:
        return parts[1].lower()  # e.g. 'ord101'
    return ""


def _score_nodes(views):
    """Score node health based on operational status and activity.
    Detects AHA configs (nodes across 2+ DCs) and does not penalize
    far-side standby nodes that are inactive."""
    nodes = views.get("NodeView", [])
    if not nodes:
        return {"score": -1, "label": "Nodes", "issues": [], "detail": "No node data"}

    # Detect AHA: check if nodes span multiple DCs
    dc_active_counts = {}  # dc -> count of active nodes
    for n in nodes:
        if not isinstance(n, dict):
            continue
        dc = _get_node_dc(n)
        if dc:
            if dc not in dc_active_counts:
                dc_active_counts[dc] = 0
            if n.get("isActive", True):
                dc_active_counts[dc] += 1

    is_aha = len(dc_active_counts) >= 2
    # Primary DC = the one with most active nodes
    primary_dc = max(dc_active_counts, key=dc_active_counts.get) if dc_active_counts else ""

    issues = []
    total = len(nodes)
    problem_count = 0
    standby_count = 0

    for n in nodes:
        if not isinstance(n, dict):
            continue
        status = n.get("operationalStatus", "")
        is_active = n.get("isActive", True)
        name = n.get("name", "?")
        dc = _get_node_dc(n)
        cluster = n.get("clusterNode", {}) or {}
        cluster_status = cluster.get("status", "")

        if status.lower() not in ("operational", ""):
            issues.append({"severity": "critical", "msg": f"Node {name}: status={status}"})
            problem_count += 1
        elif not is_active:
            if is_aha and dc != primary_dc:
                # Far-side standby node in AHA — expected, not an issue
                standby_count += 1
            else:
                # Inactive on primary side — that IS a problem
                issues.append({"severity": "warning", "msg": f"Node {name}: inactive (primary DC)"})
                problem_count += 1
        elif cluster_status and cluster_status.lower() not in ("online", ""):
            issues.append({"severity": "warning", "msg": f"Node {name}: cluster={cluster_status}"})
            problem_count += 1

    aha_label = f" (AHA: {', '.join(sorted(dc_active_counts.keys()))})"
    detail = f"{total} nodes, {problem_count} issues"
    if is_aha:
        detail += f", {standby_count} standby{aha_label}"
    score = max(0, 100 - int(problem_count / max(total - standby_count, 1) * 100))
    return {"score": score, "label": "Nodes", "issues": issues, "detail": detail}


def _score_alerts(views):
    """Score alert health based on active alerting alerts.
    AlertView structure: each item = {name: 'ci', alerts: {single alert dict}}."""
    alert_items = views.get("AlertView", [])
    if not alert_items:
        return {"score": -1, "label": "Alerts", "issues": [], "detail": "No alert data"}

    all_alerts = []
    for item in alert_items:
        if not isinstance(item, dict):
            continue
        ci = item.get("name", "?")
        alert_data = item.get("alerts")
        if isinstance(alert_data, dict):
            alert_data["_ci"] = ci
            all_alerts.append(alert_data)
        elif isinstance(alert_data, list):
            for a in alert_data:
                if isinstance(a, dict):
                    a["_ci"] = ci
                    all_alerts.append(a)

    alerting = [a for a in all_alerts if (a.get("state") or "").lower() == "alerting"]
    msg_alerts = [a for a in all_alerts if (a.get("state") or "").lower() == "message alert"]
    issues = []
    for a in alerting[:15]:
        atype = a.get("type", "Unknown")
        ci = a.get("_ci", "")
        issues.append({"severity": "critical", "msg": f"{ci}: {atype}"})
    for a in msg_alerts[:10]:
        atype = a.get("type", "Unknown")
        ci = a.get("_ci", "")
        issues.append({"severity": "info", "msg": f"{ci}: {atype}"})

    score = max(0, 100 - len(alerting) * 8 - len(msg_alerts) * 2)
    return {"score": score, "label": "Alerts", "issues": issues,
            "detail": f"{len(all_alerts)} total, {len(alerting)} alerting, {len(msg_alerts)} message"}


def _score_databases(views):
    """Score database health based on replication, connections, status."""
    dbs = views.get("PsqlDatabaseView", views.get("MysqlDatabaseView", []))
    repl = views.get("PsqlDatabaseReplicationView",
                     views.get("MysqlDatabaseReplicationView", []))
    if not dbs:
        return {"score": -1, "label": "Databases", "issues": [], "detail": "No DB data"}

    issues = []
    problem_count = 0

    for db in dbs:
        if not isinstance(db, dict):
            continue
        host = db.get("host", {})
        hname = host.get("name", "?") if isinstance(host, dict) else str(host)
        port = db.get("port", "?")
        label = f"{hname}:{port}"
        status = (db.get("operationalStatus") or "").lower()
        if status != "operational":
            issues.append({"severity": "critical", "msg": f"DB {label}: status={status}"})
            problem_count += 1

    for r in repl:
        if not isinstance(r, dict):
            continue
        host = r.get("host", {})
        hname = host.get("name", "?") if isinstance(host, dict) else str(host)
        port = r.get("port", "?")
        label = f"{hname}:{port}"
        repl_status = (r.get("replicationStatus") or "").lower()
        if repl_status and repl_status != "replicating":
            issues.append({"severity": "critical",
                           "msg": f"DB {label}: replication {repl_status}"})
            problem_count += 1
        lag = r.get("replicationLag") or ""
        if lag and lag not in ("0:00:00", "0:00:01"):
            # Check if lag is significant (> 5 minutes)
            parts = str(lag).split(":")
            if len(parts) >= 2:
                try:
                    hours = int(parts[0])
                    mins = int(parts[1])
                    if hours > 0 or mins > 5:
                        issues.append({"severity": "warning",
                                       "msg": f"DB {label}: replication lag {lag}"})
                        problem_count += 1
                except ValueError:
                    pass

    total = len(dbs) + len(repl)
    score = max(0, 100 - int(problem_count / max(total, 1) * 100 * 2))
    return {"score": score, "label": "Databases", "issues": issues,
            "detail": f"{len(dbs)} databases, {len(repl)} replication streams"}


def _score_backups(views):
    """Score backup health based on recent backup results."""
    backups = views.get("BackupView", [])
    if not backups:
        return {"score": -1, "label": "Backups", "issues": [], "detail": "No backup data"}

    issues = []
    failed = [b for b in backups if isinstance(b, dict) and (b.get("result") or "").lower() not in ("success", "")]
    for b in failed[:5]:
        name = b.get("name", "?")
        result = b.get("result", "?")
        issues.append({"severity": "warning", "msg": f"Backup {name}: {result}"})

    score = max(0, 100 - int(len(failed) / max(len(backups), 1) * 100))
    return {"score": score, "label": "Backups", "issues": issues,
            "detail": f"{len(backups)} backups, {len(failed)} non-success"}


def _parse_pool_ratio(val):
    """Parse a pool string like '3 / 32' into (current, max)."""
    if not val or not isinstance(val, str):
        return None, None
    parts = val.split("/")
    if len(parts) == 2:
        try:
            return int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            pass
    return None, None


def _score_semaphores(views):
    """Score semaphore health from SemaphoreView or SemaphoreSetView."""
    # Try SemaphoreView first (has semaphoreSets)
    sem_groups = views.get("SemaphoreView", [])
    issues = []
    total_sets = 0
    exhausted = 0

    for group in sem_groups:
        if not isinstance(group, dict):
            continue
        sets = group.get("semaphoreSets", []) or []
        if not isinstance(sets, list):
            continue
        total_sets += len(sets)
        for s in sets:
            if not isinstance(s, dict):
                continue
            name = s.get("name", "?")
            current = s.get("current", 0) or 0
            max_val = s.get("max", 1) or 1
            if isinstance(current, (int, float)) and isinstance(max_val, (int, float)) and max_val > 0 and current / max_val > 0.9:
                issues.append({"severity": "warning",
                               "msg": f"Semaphore {name}: {current}/{max_val} (>90%)"})
                exhausted += 1

    # Also check DatabasePoolView for pool utilization (db pools have semaphore-like ratios)
    pools = views.get("DatabasePoolView", [])
    pool_high = 0
    for p in pools:
        if not isinstance(p, dict):
            continue
        node_name = p.get("name", "?")
        # Check databasePools (contains pool usage like "3 / 32")
        db_pools = p.get("databasePools", {})
        if isinstance(db_pools, dict):
            for pool_name, pool_val in db_pools.items():
                cur, mx = _parse_pool_ratio(pool_val)
                if cur is not None and mx and mx > 0 and cur / mx > 0.85:
                    issues.append({"severity": "warning",
                                   "msg": f"{node_name} pool {pool_name}: {cur}/{mx}"})
                    pool_high += 1

    if total_sets == 0 and not pools:
        return {"score": -1, "label": "Semaphores", "issues": [], "detail": "No data"}

    score = max(0, 100 - exhausted * 15 - pool_high * 5)
    return {"score": score, "label": "Semaphores", "issues": issues,
            "detail": f"{total_sets} semaphore sets, {len(pools)} pool nodes, {exhausted + pool_high} warnings"}


def _score_app_servers(views):
    """Score app server health based on load and CPU."""
    servers = views.get("AppServerView", [])
    if not servers:
        return {"score": -1, "label": "App Servers", "issues": [], "detail": "No data"}

    issues = []
    problem_count = 0

    for srv in servers:
        if not isinstance(srv, dict):
            continue
        name = srv.get("name", "?")
        load = srv.get("load")
        cpu = srv.get("cpu", {}) or {}
        cpu_idle = cpu.get("idle")
        lifecycle = (srv.get("lifeCycle") or "").lower()

        if lifecycle in ("decommissioned", "hold"):
            continue

        if load and isinstance(load, (int, float)) and load > 50:
            issues.append({"severity": "warning", "msg": f"Server {name}: high load {load}"})
            problem_count += 1
        if cpu_idle is not None and isinstance(cpu_idle, (int, float)) and cpu_idle < 20:
            issues.append({"severity": "warning",
                           "msg": f"Server {name}: low CPU idle {cpu_idle}%"})
            problem_count += 1

    active = [s for s in servers
              if (s.get("lifeCycle") or "").lower() not in ("decommissioned", "hold")]
    score = max(0, 100 - int(problem_count / max(len(active), 1) * 50))
    return {"score": score, "label": "App Servers", "issues": issues,
            "detail": f"{len(active)} active servers, {problem_count} issues"}


def _score_db_servers(views):
    """Score DB server health based on lifecycle and capacity."""
    servers = views.get("PsqlDbserverView", views.get("MysqlDbserverView", []))
    if not servers:
        return {"score": -1, "label": "DB Servers", "issues": [], "detail": "No data"}

    issues = []
    problem_count = 0
    decom = 0
    hold = 0

    for srv in servers:
        if not isinstance(srv, dict):
            continue
        name = srv.get("name", "?")
        lifecycle = (srv.get("lifeCycle") or "").lower()
        if lifecycle == "decommissioned":
            decom += 1
        elif lifecycle == "hold":
            hold += 1
            issues.append({"severity": "info", "msg": f"DB Server {name}: lifecycle=hold"})

    total_active = len(servers) - decom
    score = max(0, 100 - hold * 5)
    return {"score": score, "label": "DB Servers", "issues": issues,
            "detail": f"{len(servers)} servers, {decom} decommissioned, {hold} on hold"}


def _score_changes(views):
    """Score based on change requests — informational, scheduled vs closed."""
    cr_items = views.get("ChangeRequestView", [])
    if not cr_items:
        return {"score": -1, "label": "Changes", "issues": [], "detail": "No data"}

    issues = []
    scheduled = 0
    implement = 0
    canceled = 0

    for item in cr_items:
        if not isinstance(item, dict):
            continue
        cr = item.get("changeRequests", {})
        if isinstance(cr, dict):
            state = (cr.get("state") or "").lower()
        elif isinstance(cr, list):
            state = ""
        else:
            continue
        if state == "scheduled":
            scheduled += 1
        elif state == "implement":
            implement += 1
        elif state == "canceled":
            canceled += 1

    if scheduled > 0:
        issues.append({"severity": "info", "msg": f"{scheduled} scheduled change(s)"})
    if implement > 0:
        issues.append({"severity": "warning", "msg": f"{implement} change(s) in implementation"})

    score = max(0, 100 - implement * 10)
    return {"score": score, "label": "Changes", "issues": issues,
            "detail": f"{len(cr_items)} changes, {scheduled} scheduled, {implement} implementing"}


def compute_health(views):
    """Compute comprehensive health scores from ruckus view data."""
    categories = {
        "alerts": _score_alerts(views),
        "nodes": _score_nodes(views),
        "databases": _score_databases(views),
        "db_servers": _score_db_servers(views),
        "app_servers": _score_app_servers(views),
        "backups": _score_backups(views),
        "semaphores": _score_semaphores(views),
        "changes": _score_changes(views),
    }

    # Overall = weighted average of categories with data (score >= 0)
    weights = {"alerts": 20, "nodes": 20, "databases": 20, "db_servers": 10,
               "app_servers": 10, "backups": 10, "semaphores": 5, "changes": 5}
    total_weight = 0
    weighted_sum = 0
    all_issues = []
    for cat, data in categories.items():
        if data["score"] >= 0:
            w = weights.get(cat, 10)
            weighted_sum += data["score"] * w
            total_weight += w
        all_issues.extend(
            {"severity": i["severity"], "category": data["label"], "msg": i["msg"]}
            for i in data["issues"]
        )

    overall = int(weighted_sum / total_weight) if total_weight > 0 else -1

    # Sort issues: critical first, then warning, then info
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    all_issues.sort(key=lambda x: sev_order.get(x["severity"], 9))

    return {
        "overall": overall,
        "categories": categories,
        "issues": all_issues,
    }


# ── Flask Routes ────────────────────────────────────────────────────────────


@app.route("/")
def dashboard():
    """Serve the main aggregator dashboard."""
    return send_from_directory("output", "ruckus_dashboard.html")


@app.route("/analysis")
def analysis_page():
    """Serve the instance analysis page."""
    return send_from_directory("static", "analysis.html")


@app.route("/instance")
def instance_page():
    """Serve the instance history page."""
    return send_from_directory("static", "instance.html")


@app.errorhandler(Exception)
def handle_exception(e):
    """Return JSON for any unhandled exception."""
    import traceback
    traceback.print_exc()
    return jsonify({"error": str(e)}), 500


async def _collect_both(instance, bindings, timeout=180):
    """Run ruckus in JSON and text modes in parallel."""
    json_task = _run_ruckus(instance, bindings, timeout=timeout, json_full=True)
    text_task = _run_ruckus(instance, bindings, timeout=timeout, json_full=False)
    json_result, text_result = await asyncio.gather(json_task, text_task,
                                                     return_exceptions=True)
    # Unwrap exceptions
    if isinstance(json_result, Exception):
        json_result = (False, str(json_result))
    if isinstance(text_result, Exception):
        text_result = (False, str(text_result))
    return json_result, text_result


@app.route("/api/analyze/<instance>")
def analyze_instance(instance):
    """Run comprehensive ruckus analysis for a single instance."""
    try:
        instance = instance.strip().lower()
        if not instance or len(instance) > 100:
            return jsonify({"error": "Invalid instance name"}), 400

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            json_result, text_result = loop.run_until_complete(
                _collect_both(instance, ANALYSIS_BINDINGS, timeout=180)
            )
        finally:
            loop.close()

        json_ok, json_output = json_result
        text_ok, text_output = text_result

        if not json_ok:
            return jsonify({"error": json_output, "instance": instance}), 502

        raw = _extract_json(json_output)
        if not raw:
            return jsonify({"error": "Failed to parse ruckus JSON output",
                            "instance": instance}), 502

        data = raw.get("Data", {})
        errors = raw.get("errors", [])

        # Build labeled views from JSON
        views = {}
        for key, val in data.items():
            label = VIEW_LABELS.get(key, key)
            views[key] = {"label": label, "data": val}

        # Merge text data for views that JSON leaves empty
        if text_ok and text_output:
            try:
                text_sections = parse_ruckus_text(text_output)
                merge_text_into_views(views, text_sections, VIEW_LABELS)
            except Exception as e:
                errors.append(f"Text parse warning: {e}")

        # Health analysis (use merged data)
        merged_data = {k: v.get("data", []) for k, v in views.items()}
        health = compute_health(merged_data)

        # Instance metadata
        inst_info = {}
        inst_list = data.get("InstanceView", [])
        if inst_list:
            iv = inst_list[0]
            inst_info = {
                "name": iv.get("name", instance),
                "version": (iv["version"].get("name", "") if isinstance(iv.get("version"), dict) else iv.get("version", "")),
                "customer": iv.get("customer", ""),
                "status": iv.get("operationalStatus", ""),
                "usage": iv.get("usedFor", ""),
                "dedicated": iv.get("isDedicated", False),
                "sharded": iv.get("hasShardedDatabase", False),
                "isPsql": iv.get("isPsql", False),
                "datacenter": (iv["primaryDatacenter"].get("name", "") if isinstance(iv.get("primaryDatacenter"), dict) else iv.get("primaryDatacenter", "")),
            }

        # Run deep analysis (20+ analyzers + composite + anomalies)
        deep_analysis = {}
        try:
            deep_analysis = run_all_analyzers(views, errors=errors)
        except Exception as e:
            errors.append(f"Deep analysis warning: {e}")

        # Compute delta against previous run
        delta = None
        try:
            delta = history.compute_delta(instance, health, views)
        except Exception:
            pass

        # Save to history
        raw_path = None
        try:
            run_id, raw_path = history.save_run(
                instance, health, inst_info, views,
                errors, raw_text=text_output if text_ok else None
            )
        except Exception as e:
            run_id = None
            errors.append(f"History save warning: {e}")

        # Deduplicate errors (JSON + text runs produce duplicates)
        seen_errors = set()
        error_list = []
        for e in errors:
            msg = (e.get("message", str(e))[:200] if isinstance(e, dict) else str(e)[:200])
            if msg not in seen_errors:
                seen_errors.add(msg)
                error_list.append(msg)
        error_list = error_list[:30]

        result = {
            "instance": instance,
            "instance_info": inst_info,
            "health": health,
            "deep_analysis": deep_analysis,
            "views": views,
            "errors": error_list,
            "run_id": run_id,
            "saved_to": raw_path,
        }
        if delta:
            result["delta"] = delta

        return jsonify(result)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Server error: {exc}", "instance": instance}), 500


@app.route("/api/history/<instance>")
def instance_history(instance):
    """Get analysis history for an instance."""
    try:
        instance = instance.strip().lower()
        limit = request.args.get("limit", 20, type=int)
        runs = history.get_runs(instance, limit=limit)
        return jsonify({"instance": instance, "runs": runs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/run/<int:run_id>")
def history_run_detail(run_id):
    """Get full detail for a specific historical run, including re-computed deep analysis."""
    try:
        detail = history.get_run_detail(run_id)
        if not detail:
            return jsonify({"error": "Run not found"}), 404

        # Re-compute health scores and deep analysis from stored views
        views = detail.get("views", {})
        merged_data = {k: v.get("data", []) for k, v in views.items()}

        # Re-compute health if stored health is minimal
        try:
            health = compute_health(merged_data)
            detail["health"] = health
        except Exception:
            pass

        # Run deep analysis on stored view data
        try:
            deep_analysis = run_all_analyzers(views, errors=[])
            detail["deep_analysis"] = deep_analysis
        except Exception:
            detail["deep_analysis"] = {}

        # Compute delta against the previous run
        try:
            delta = history.compute_delta_between_runs(run_id)
            if delta:
                detail["delta"] = delta
        except Exception:
            pass

        return jsonify(detail)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/history/instances")
def history_instances():
    """Get all instances with historical data."""
    try:
        instances = history.get_all_instances()
        return jsonify({"instances": instances})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Bulk Collection Engine ──────────────────────────────────────────────────

_collect_state = {
    "running": False,
    "cancelled": False,
    "total": 0,
    "completed": 0,
    "cached": 0,
    "succeeded": 0,
    "failed": 0,
    "current_instance": "",
    "started_at": 0,
    "finished_at": 0,
    "events": Queue(maxsize=5000),
    "log": [],            # persisted log entries (last 2000)
    "error": None,
}
_LOG_MAX = 2000
_collect_lock = threading.Lock()


def _emit(event_type, data):
    """Push an SSE event onto the queue for all listeners and persist to log."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    _collect_state["events"].put(f"event: {event_type}\ndata: {payload}\n\n")
    # Persist log entry for refresh-resilience
    entry = {"type": event_type, "ts": _time.time()}
    if isinstance(data, dict):
        entry.update(data)
    else:
        entry["msg"] = data
    log = _collect_state["log"]
    log.append(entry)
    if len(log) > _LOG_MAX:
        del log[:-_LOG_MAX]


def _collection_thread(instances, use_cache, concurrency):
    """Background thread: run threaded collection."""
    state = _collect_state
    state["error"] = None

    try:
        os.makedirs(config.CACHE_DIR, exist_ok=True)

        # Phase 1: separate cached vs to-collect
        to_collect = []
        if use_cache:
            for inst in instances:
                if state["cancelled"]:
                    break
                cached = _load_cached(inst, bindings=config.RUCKUS_COMBINED_BINDINGS)
                if cached:
                    state["cached"] += 1
                    state["completed"] += 1
                    _emit("progress", {
                        "instance": inst, "status": "cached",
                        "completed": state["completed"], "total": state["total"],
                        "cached": state["cached"], "succeeded": state["succeeded"],
                        "failed": state["failed"],
                    })
                else:
                    to_collect.append(inst)
        else:
            to_collect = list(instances)

        if state["cached"] > 0:
            _emit("log", {"msg": f"{state['cached']} instances loaded from cache"})

        # Phase 2: collect remaining instances using thread pool
        from concurrent.futures import ThreadPoolExecutor, as_completed
        instance_timeout = getattr(config, "RUCKUS_INSTANCE_TIMEOUT", 300)
        state.setdefault("in_flight", set())

        def _sync_worker(inst):
            """Collect a single instance synchronously in its own thread."""
            if state["cancelled"]:
                return
            state["in_flight"].add(inst)
            state["current_instance"] = inst
            _emit("activity", {"instance": inst, "action": "collecting"})
            try:
                # Run async collection in a daemon sub-thread with hard timeout
                result_holder = [None, None]  # [data, error]

                def _run_async():
                    wloop = asyncio.new_event_loop()
                    try:
                        result_holder[0] = wloop.run_until_complete(
                            _collect_instance(inst, semaphore=None)
                        )
                    except Exception as ex:
                        result_holder[1] = ex
                    finally:
                        wloop.close()

                t = threading.Thread(target=_run_async, daemon=True)
                t.start()
                t.join(timeout=instance_timeout)
                if t.is_alive():
                    raise TimeoutError(f"Hard timeout after {instance_timeout}s")
                if result_holder[1]:
                    raise result_holder[1]
                data = result_holder[0]

                _save_cache(inst, data, bindings=config.RUCKUS_COMBINED_BINDINGS)

                has_views = len(data.get("views", {})) > 0
                chunk_stats = data.get("chunk_stats", {})
                chunk_failures = sum(
                    1 for cs in chunk_stats.values()
                    if cs.get("status", "").startswith("failed")
                )
                if has_views and chunk_failures == 0:
                    state["succeeded"] += 1
                    status = "success"
                elif has_views:
                    state["succeeded"] += 1
                    status = "partial"
                    _emit("log", {
                        "msg": f"{inst}: {chunk_failures}/{len(chunk_stats)} chunks failed, "
                               f"{len(data['views'])} views collected"
                    })
                else:
                    state["failed"] += 1
                    status = "failed"
                    _emit("log", {"msg": f"{inst}: all chunks failed"})
            except (TimeoutError, asyncio.TimeoutError):
                state["failed"] += 1
                status = "failed"
                _emit("log", {"msg": f"{inst}: timed out after {instance_timeout}s — skipping"})
            except Exception as e:
                state["failed"] += 1
                status = "failed"
                _emit("log", {"msg": f"{inst}: error — {e}"})

            state["completed"] += 1
            state["in_flight"].discard(inst)
            in_f = state["in_flight"]
            state["current_instance"] = ", ".join(sorted(in_f)[:5]) if in_f else ""
            _emit("progress", {
                "instance": inst, "status": status,
                "completed": state["completed"], "total": state["total"],
                "cached": state["cached"], "succeeded": state["succeeded"],
                "failed": state["failed"],
                "in_flight": len(in_f),
            })

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(_sync_worker, inst): inst for inst in to_collect}
            for future in as_completed(futures):
                if state["cancelled"]:
                    executor.shutdown(wait=False, cancel_futures=True)
                    _emit("log", {"msg": "Collection cancelled by user"})
                    break
                # Propagate any unhandled exceptions
                try:
                    future.result()
                except Exception as e:
                    inst = futures[future]
                    _emit("log", {"msg": f"{inst}: unhandled error — {e}"})

        # Phase 3: regenerate reports
        if not state["cancelled"]:
            _emit("log", {"msg": "Regenerating dashboard report..."})
            try:
                from scoring import get_ranked_instances, load_company_map, load_prod_transactions
                from reporter import generate_reports

                ranked = get_ranked_instances(top_n=config.DEFAULT_TOP_N, csv_only=True)
                inst_names = [inst for inst, _, _, _ in ranked]
                collected = {}
                for inst in inst_names:
                    cached = _load_cached(inst, bindings=config.RUCKUS_COMBINED_BINDINGS)
                    if cached:
                        collected[inst] = cached

                csv_data = load_prod_transactions()
                company_map = load_company_map()
                generate_reports(ranked, collected, csv_data, company_map)
                _emit("log", {"msg": "Dashboard report regenerated successfully"})
            except Exception as e:
                _emit("log", {"msg": f"Report generation warning: {e}"})

            # Phase 4: auto-ingest snapshot into insights DB
            try:
                _emit("log", {"msg": "Ingesting snapshot into insights DB..."})
                snap_id = _db.ingest_from_cache(note="auto-ingest after collection")
                score_result = _health.score_snapshot(snap_id)
                _emit("log", {
                    "msg": f"Insights snapshot #{snap_id} — "
                           f"{score_result['total']} instances scored, "
                           f"avg health {score_result['avg_score']}"
                })
            except Exception as e:
                _emit("log", {"msg": f"Insights ingestion warning: {e}"})

    except Exception as e:
        state["error"] = str(e)
        _emit("error", {"msg": str(e)})
    finally:
        state["running"] = False
        state["finished_at"] = _time.time()
        state["current_instance"] = ""
        _emit("done", {
            "completed": state["completed"], "total": state["total"],
            "cached": state["cached"], "succeeded": state["succeeded"],
            "failed": state["failed"],
            "elapsed": round(state["finished_at"] - state["started_at"], 1),
            "cancelled": state["cancelled"],
        })


@app.route("/collect")
def collect_page():
    """Serve the bulk collection page."""
    return send_from_directory("static", "collect.html")


@app.route("/api/collect/start", methods=["POST"])
def collect_start():
    """Start bulk data collection for all instances."""
    with _collect_lock:
        if _collect_state["running"]:
            return jsonify({"error": "Collection already in progress"}), 409

        # Load instance list
        agg_path = os.path.join(config.OUTPUT_DIR, "ruckus_aggregated_data.json")
        if not os.path.exists(agg_path):
            return jsonify({"error": "No aggregated data found — run main.py first"}), 404

        with open(agg_path) as f:
            agg_data = json.load(f)

        instances = [row["instance"] for row in agg_data if row.get("instance")]

        body = request.get_json(silent=True) or {}
        use_cache = body.get("use_cache", True)
        concurrency = body.get("concurrency", config.RUCKUS_CONCURRENCY)
        concurrency = max(1, min(concurrency, 25))

        # Reset state
        _collect_state.update({
            "running": True,
            "cancelled": False,
            "total": len(instances),
            "completed": 0,
            "cached": 0,
            "succeeded": 0,
            "failed": 0,
            "current_instance": "",
            "started_at": _time.time(),
            "finished_at": 0,
            "error": None,
            "log": [],
        })
        # Drain old events
        while not _collect_state["events"].empty():
            try:
                _collect_state["events"].get_nowait()
            except Empty:
                break

        thread = threading.Thread(
            target=_collection_thread,
            args=(instances, use_cache, concurrency),
            daemon=True,
        )
        thread.start()

        return jsonify({
            "status": "started",
            "total": len(instances),
            "use_cache": use_cache,
            "concurrency": concurrency,
        })


@app.route("/api/collect/cancel", methods=["POST"])
def collect_cancel():
    """Cancel an in-progress collection (idempotent)."""
    if not _collect_state["running"]:
        return jsonify({"status": "not_running", "message": "No collection running"})
    if _collect_state["cancelled"]:
        return jsonify({"status": "already_cancelling", "message": "Cancel already in progress"})
    _collect_state["cancelled"] = True
    _emit("log", {"msg": "Cancel requested — finishing in-progress tasks..."})
    return jsonify({"status": "cancelling"})


@app.route("/api/collect/status")
def collect_status():
    """Get current collection status (polling fallback)."""
    s = _collect_state
    if s["running"] and s["started_at"]:
        elapsed = _time.time() - s["started_at"]
    elif s["finished_at"] and s["started_at"]:
        elapsed = s["finished_at"] - s["started_at"]
    else:
        elapsed = 0
    rate = s["completed"] / elapsed if elapsed > 0 else 0
    remaining = s["total"] - s["completed"]
    eta = remaining / rate if rate > 0 else 0

    return jsonify({
        "running": s["running"],
        "cancelled": s["cancelled"],
        "total": s["total"],
        "completed": s["completed"],
        "cached": s["cached"],
        "succeeded": s["succeeded"],
        "failed": s["failed"],
        "current_instance": s["current_instance"],
        "in_flight": len(s.get("in_flight", set())),
        "elapsed": round(elapsed, 1),
        "rate": round(rate, 2),
        "eta_seconds": round(eta),
        "error": s["error"],
    })


@app.route("/api/collect/log")
def collect_log():
    """Return persisted log entries for refresh-resilience."""
    return jsonify({"log": _collect_state["log"]})


@app.route("/api/collect/stream")
def collect_stream():
    """SSE stream for real-time collection progress."""
    def generate():
        # Send current state first
        yield f"event: status\ndata: {json.dumps({'running': _collect_state['running'], 'total': _collect_state['total'], 'completed': _collect_state['completed']})}\n\n"
        while True:
            try:
                event = _collect_state["events"].get(timeout=1)
                yield event
            except Empty:
                # Send heartbeat to keep connection alive
                yield ": heartbeat\n\n"
                if not _collect_state["running"]:
                    # Send final done if not running
                    yield f"event: done\ndata: {json.dumps({'completed': _collect_state['completed'], 'total': _collect_state['total']})}\n\n"
                    break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/collect/results")
def collect_results():
    """Inspect cache: show success/partial/failed breakdown with per-chunk detail."""
    try:
        candidates = find_retry_candidates()
        return jsonify({
            "success": len(candidates["success"]),
            "partial": len(candidates["partial"]),
            "failed": len(candidates["failed"]),
            "partial_detail": sorted(candidates["partial"], key=lambda x: -x["chunk_failures"]),
            "failed_detail": candidates["failed"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/collect/retry", methods=["POST"])
def collect_retry():
    """Re-collect only failed and partial instances (those with chunk failures).

    For partial instances, only the failed chunks are re-run (merge into existing data).
    For fully failed instances, all chunks are re-run from scratch.
    """
    with _collect_lock:
        if _collect_state["running"]:
            return jsonify({"error": "Collection already in progress"}), 409

        candidates = find_retry_candidates()
        retry_instances = (
            [e["instance"] for e in candidates["failed"]]
            + [e["instance"] for e in candidates["partial"]]
        )

        if not retry_instances:
            return jsonify({"status": "nothing_to_retry", "message": "No failed or partial instances in cache"})

        body = request.get_json(silent=True) or {}
        concurrency = body.get("concurrency", config.RUCKUS_CONCURRENCY)
        concurrency = max(1, min(concurrency, 25))

        # Reset state
        _collect_state.update({
            "running": True,
            "cancelled": False,
            "total": len(retry_instances),
            "completed": 0,
            "cached": 0,
            "succeeded": 0,
            "failed": 0,
            "current_instance": "",
            "started_at": _time.time(),
            "finished_at": 0,
            "error": None,
            "log": [],
        })
        # Drain old events
        while not _collect_state["events"].empty():
            try:
                _collect_state["events"].get_nowait()
            except Empty:
                break

        _emit("log", {
            "msg": f"Retry: {len(candidates['failed'])} failed + "
                   f"{len(candidates['partial'])} partial = {len(retry_instances)} instances"
        })

        thread = threading.Thread(
            target=_collection_thread,
            args=(retry_instances, False, concurrency),  # use_cache=False to force re-collect
            daemon=True,
        )
        thread.start()

        return jsonify({
            "status": "started",
            "total": len(retry_instances),
            "failed_count": len(candidates["failed"]),
            "partial_count": len(candidates["partial"]),
            "concurrency": concurrency,
        })


# ── Table Delta Tracking ────────────────────────────────────────────────────

@app.route("/api/tables/snapshot", methods=["POST"])
def api_save_table_snapshot():
    """Save a table snapshot from ruckus T output.
    POST JSON: {"instance": "name", "tables": [{"name":..., "rows":..., ...}]}
    Or POST with just {"instance": "name"} to auto-collect via ruckus."""
    data = request.get_json(force=True)
    instance = data.get("instance")
    if not instance:
        return jsonify({"error": "instance required"}), 400

    tables = data.get("tables")
    if tables:
        ts = history.save_table_snapshot(instance, tables)
        return jsonify({"ok": True, "instance": instance, "timestamp": ts, "table_count": len(tables)})

    # Auto-collect from ruckus
    import subprocess
    try:
        result = subprocess.run(
            ["ruckus", instance, "T", "-b", "--json-full"],
            capture_output=True, text=True, timeout=600
        )
        raw = result.stdout
        # Strip header line (non-JSON)
        lines = raw.split("\n")
        json_start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), 0)
        json_text = "\n".join(lines[json_start:])
        parsed = json.loads(json_text)

        # Extract tables from the appropriate view
        view_data = parsed.get("Data", {})
        table_list = (
            view_data.get("PsqlDatabaseTableView", []) or
            view_data.get("MysqlDatabaseTableView", [])
        )

        if not table_list:
            return jsonify({"error": "No table data returned", "errors": parsed.get("errors", [])}), 500

        ts = history.save_table_snapshot(instance, table_list)
        return jsonify({"ok": True, "instance": instance, "timestamp": ts, "table_count": len(table_list)})

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Ruckus timed out after 600s"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tables/delta/<instance>")
def api_table_deltas(instance):
    """Get per-table row count deltas between the two most recent snapshots."""
    delta = history.get_table_deltas(instance)
    if delta is None:
        return jsonify({"error": "Need at least 2 snapshots", "snapshots": history.get_table_snapshot_timestamps(instance)}), 404
    return jsonify(delta)


@app.route("/api/tables/history/<instance>/<table_name>")
def api_table_history(instance, table_name):
    """Get row count history for a specific table."""
    limit = request.args.get("limit", 20, type=int)
    rows = history.get_table_history(instance, table_name, limit)
    return jsonify({"instance": instance, "table": table_name, "history": rows})


@app.route("/api/tables/snapshots/<instance>")
def api_table_snapshots(instance):
    """List available snapshot timestamps for an instance."""
    timestamps = history.get_table_snapshot_timestamps(instance)
    return jsonify({"instance": instance, "timestamps": timestamps})


# ── Insights & Analytics API ─────────────────────────────────────────────────

import db as _db
import health as _health
import trends as _trends


@app.route("/insights")
def insights_page():
    return send_from_directory("static", "insights.html")


# -- Snapshot management --

@app.route("/api/insights/snapshot", methods=["POST"])
def api_ingest_snapshot():
    """Ingest current cache into a new DB snapshot."""
    body = request.get_json(silent=True) or {}
    note = body.get("note", "")
    try:
        snap_id = _db.ingest_from_cache(note=note)
        score_result = _health.score_snapshot(snap_id)
        return jsonify({
            "snapshot_id": snap_id,
            "health": score_result,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/insights/snapshots")
def api_list_snapshots():
    """List recent snapshots."""
    limit = request.args.get("limit", 50, type=int)
    return jsonify({"snapshots": _db.get_snapshots(limit=limit)})


# -- Health scores --

@app.route("/api/insights/health")
def api_health_summary():
    """Health score summary for latest (or specified) snapshot."""
    snap_id = request.args.get("snapshot_id", None, type=int)
    return jsonify(_health.get_health_summary(snap_id))


@app.route("/api/insights/health/<instance_name>")
def api_instance_health(instance_name):
    """Health details for a specific instance."""
    history = _db.get_instance_history(instance_name, limit=1)
    if not history:
        return jsonify({"error": "Instance not found"}), 404
    metrics = history[0]
    result = _health.compute_health_score(metrics)
    result["instance"] = instance_name
    result["metrics"] = metrics
    return jsonify(result)


# -- Trends & Growth --

@app.route("/api/insights/growth")
def api_growth():
    """Growth between two snapshots."""
    snaps = _db.get_snapshots(limit=2)
    if len(snaps) < 2:
        return jsonify({"error": "Need at least 2 snapshots for growth analysis"}), 400
    a_id = request.args.get("from", snaps[1]["id"], type=int)
    b_id = request.args.get("to", snaps[0]["id"], type=int)
    limit = request.args.get("limit", 50, type=int)
    metric = request.args.get("metric", "rows")
    results = _trends.top_growers(a_id, b_id, metric=metric, limit=limit)
    return jsonify({"from_snapshot": a_id, "to_snapshot": b_id, "results": results})


@app.route("/api/insights/table-growth/<table_name>")
def api_table_growth(table_name):
    """Growth for a specific table across instances."""
    snaps = _db.get_snapshots(limit=2)
    if len(snaps) < 2:
        return jsonify({"error": "Need at least 2 snapshots"}), 400
    a_id = request.args.get("from", snaps[1]["id"], type=int)
    b_id = request.args.get("to", snaps[0]["id"], type=int)
    results = _trends.get_table_growth(table_name, a_id, b_id)
    return jsonify({"table": table_name, "results": results})


# -- Anomalies --

@app.route("/api/insights/anomalies", methods=["GET"])
def api_anomalies_list():
    """List detected anomalies."""
    snap_id = request.args.get("snapshot_id", None, type=int)
    severity = request.args.get("severity", None)
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"anomalies": _trends.get_anomalies(snap_id, severity, limit)})


@app.route("/api/insights/anomalies/detect", methods=["POST"])
def api_detect_anomalies():
    """Detect anomalies between two snapshots."""
    snaps = _db.get_snapshots(limit=2)
    if len(snaps) < 2:
        return jsonify({"error": "Need at least 2 snapshots"}), 400
    a_id = snaps[1]["id"]
    b_id = snaps[0]["id"]
    anomalies = _trends.detect_anomalies(a_id, b_id)
    _trends.store_anomalies(b_id, anomalies)
    return jsonify({
        "from_snapshot": a_id, "to_snapshot": b_id,
        "total": len(anomalies),
        "critical": sum(1 for a in anomalies if a["severity"] == "critical"),
        "warning": sum(1 for a in anomalies if a["severity"] == "warning"),
        "anomalies": anomalies,
    })


# -- Capacity forecasting --

@app.route("/api/insights/forecast")
def api_forecast():
    """Capacity exhaustion forecast."""
    snaps = _db.get_snapshots(limit=2)
    if len(snaps) < 2:
        return jsonify({"error": "Need at least 2 snapshots"}), 400
    a_id = snaps[1]["id"]
    b_id = snaps[0]["id"]
    try:
        from scoring import load_prod_transactions
        csv_data = load_prod_transactions()
    except Exception:
        csv_data = {}
    results = _trends.forecast_capacity(a_id, b_id, csv_data)
    return jsonify({"forecasts": results})


# -- Search & Query --

@app.route("/api/insights/search/instances")
def api_search_instances():
    """Search instances with filters."""
    query = request.args.get("q", "")
    min_rows = request.args.get("min_rows", 0, type=int)
    min_lag = request.args.get("min_repl_lag", -1, type=float)
    customer = request.args.get("customer", "")
    limit = request.args.get("limit", 200, type=int)
    tags = request.args.getlist("tag")
    results = _db.search_instances(
        query=query, min_rows=min_rows, min_repl_lag=min_lag,
        tags=tags or None, customer=customer, limit=limit,
    )
    return jsonify({"results": results, "count": len(results)})


@app.route("/api/insights/search/tables")
def api_search_tables():
    """Search tables with filters."""
    query = request.args.get("q", "")
    min_rows = request.args.get("min_rows", 0, type=int)
    instance = request.args.get("instance", "")
    limit = request.args.get("limit", 200, type=int)
    exact = request.args.get("exact", "false").lower() == "true"
    results = _db.search_tables(
        query=query, min_rows=min_rows, instance=instance,
        exact_instance=exact, limit=limit,
    )
    return jsonify({"results": results, "count": len(results)})


# -- Instance comparison --

@app.route("/api/insights/compare")
def api_compare():
    """Side-by-side comparison of two instances."""
    a = request.args.get("a", "")
    b = request.args.get("b", "")
    if not a or not b:
        return jsonify({"error": "Both 'a' and 'b' instance names required"}), 400
    return jsonify(_db.compare_instances(a, b))


# -- Instance history --

@app.route("/api/insights/history/<instance_name>")
def api_instance_history(instance_name):
    """Metric history for an instance across snapshots."""
    limit = request.args.get("limit", 30, type=int)
    return jsonify({"history": _db.get_instance_history(instance_name, limit)})


# -- Customer portfolio --

@app.route("/api/insights/portfolio")
def api_portfolio():
    """Customer portfolio aggregation."""
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"portfolio": _db.get_customer_portfolio(limit)})


# -- Tags --

@app.route("/api/insights/tags", methods=["GET"])
def api_list_tags():
    """List all tags with counts."""
    return jsonify({"tags": _db.get_all_tags()})


@app.route("/api/insights/tags/<instance_name>", methods=["GET"])
def api_get_instance_tags(instance_name):
    return jsonify({"tags": _db.get_tags(instance_name)})


@app.route("/api/insights/tags/<instance_name>", methods=["POST"])
def api_add_tag(instance_name):
    body = request.get_json(force=True)
    tag = body.get("tag", "").strip()
    if not tag:
        return jsonify({"error": "tag required"}), 400
    ok = _db.add_tag(instance_name, tag)
    if not ok:
        return jsonify({"error": "Instance not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/insights/tags/<instance_name>", methods=["DELETE"])
def api_remove_tag(instance_name):
    tag = request.args.get("tag", "").strip()
    if not tag:
        return jsonify({"error": "tag required"}), 400
    _db.remove_tag(instance_name, tag)
    return jsonify({"ok": True})


# -- CSV export --

@app.route("/api/insights/export/csv")
def api_export_csv():
    """Export instance metrics as CSV download."""
    import tempfile
    query = request.args.get("q", "")
    min_rows = request.args.get("min_rows", 0, type=int)
    customer = request.args.get("customer", "")
    filepath = os.path.join(tempfile.gettempdir(), "ruckus_export.csv")
    count = _db.export_csv(filepath, query=query, min_rows=min_rows, customer=customer)
    if count == 0:
        return jsonify({"error": "No data to export"}), 404
    return send_from_directory(
        os.path.dirname(filepath), os.path.basename(filepath),
        as_attachment=True, download_name="ruckus_instances.csv",
    )


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ruckus Aggregator Server")
    parser.add_argument("--port", type=int, default=5111)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\n  Ruckus Aggregator Server")
    print(f"  Dashboard:  http://127.0.0.1:{args.port}/")
    print(f"  Analysis:   http://127.0.0.1:{args.port}/analysis")
    print(f"  Collection: http://127.0.0.1:{args.port}/collect")
    print(f"  Insights:   http://127.0.0.1:{args.port}/insights\n")
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
