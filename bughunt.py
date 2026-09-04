#!/usr/bin/env python3
"""
BugHunt — Automated comparison of ruckus CLI output vs UI (API) output
for all 500 instances. Finds bugs, missing features, and gaps.

Saves one .txt report per instance in BugHunt/ and a summary file.

Concurrency capped at 5 total (CLI + UI combined).
"""
import asyncio
import json
import os
import sys
import time
import traceback
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from text_parser import parse_ruckus_text, _find_columns, _parse_section, _classify_section

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUGHUNT_DIR = os.path.join(BASE_DIR, "BugHuntTres")
BINDINGS = "iacuDdRqTpAnvsSwWCbPM"
API_URL = "http://127.0.0.1:5111"
MAX_CONCURRENCY = 5  # Total across CLI + UI combined

# Global semaphore (set in main)
semaphore: asyncio.Semaphore = None

# ── CLI runner ────────────────────────────────────────────────────────────────

async def run_cli(instance: str, timeout: int = 240) -> Tuple[bool, str]:
    """Run ruckus CLI in batch text mode and return raw output."""
    cmd = ["ruckus", "-b", "-m", instance, BINDINGS]
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w+b", suffix=".txt", delete=False)
    tmp_path = tmp.name
    try:
        async with semaphore:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=tmp,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        tmp.close()
        with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if proc.returncode == 0 or (content.strip() and len(content) > 100):
            return True, content
        return False, f"Exit {proc.returncode}"
    except asyncio.TimeoutError:
        tmp.close()
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        tmp.close()
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def run_ui(instance: str, timeout: int = 240) -> Tuple[bool, dict]:
    """Run analysis via the UI API and return parsed JSON."""
    import aiohttp
    url = f"{API_URL}/api/analyze/{instance}"
    try:
        async with semaphore:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return True, data
                    else:
                        text = await resp.text()
                        return False, {"error": text, "status": resp.status}
    except Exception as e:
        return False, {"error": str(e)}


# ── Text parser for CLI output ────────────────────────────────────────────────

def parse_cli_sections(raw_text: str) -> Dict[str, List[Dict]]:
    """Parse CLI text output into sections with classified view keys."""
    return parse_ruckus_text(raw_text)


def count_cli_sections(raw_text: str) -> Dict[str, int]:
    """Quick count of rows per section from CLI text."""
    sections = parse_cli_sections(raw_text)
    return {k: len(v) for k, v in sections.items()}


# ── Comparison engine ─────────────────────────────────────────────────────────

# Canonical view list (all possible views from both CLI text and JSON API)
ALL_VIEWS = [
    "InstanceView", "AlertView", "ChangeRequestView", "UpdateSetView",
    "MysqlDbserverView", "PsqlDbserverView", "_DbserverView",
    "MysqlDatabaseView", "PsqlDatabaseView",
    "MysqlDatabaseReplicationView", "PsqlDatabaseReplicationView",
    "MysqlDatabaseQueryView", "PsqlDatabaseQueryView",
    "MysqlDatabaseTableView", "PsqlDatabaseTableView", "_DatabaseTableView",
    "DatabasePoolView", "AppServerView", "NodeView", "ServletView",
    "SemaphoreSetView", "SemaphoreView", "SchedulerView", "JobView",
    "JvmThreadView", "BackupView",
    "LoadBalancingPoolView", "LoadBalancingPoolMemberView",
]


def compare_instance(instance: str, cli_text: str, ui_data: dict) -> dict:
    """Compare CLI text output against UI JSON output.
    Returns a structured comparison result."""

    result = {
        "instance": instance,
        "bugs": [],
        "missing_features": [],
        "gaps": [],
        "view_comparison": {},
        "field_gaps": [],
        "health": ui_data.get("health", {}),
        "errors": ui_data.get("errors", []),
    }

    # Parse CLI text into sections
    cli_sections = parse_cli_sections(cli_text)
    ui_views = ui_data.get("views", {})

    # Detect DB type from UI
    is_mysql = bool(
        ui_views.get("MysqlDatabaseView", {}).get("data")
        or ui_views.get("MysqlDbserverView", {}).get("data")
    )
    is_psql = bool(
        ui_views.get("PsqlDatabaseView", {}).get("data")
        or ui_views.get("PsqlDbserverView", {}).get("data")
    )
    db_type = "mysql" if is_mysql else ("psql" if is_psql else "unknown")

    result["db_type"] = db_type

    # ── 1. View count comparison ──────────────────────────────────────────

    cli_counts = {k: len(v) for k, v in cli_sections.items()}
    ui_counts = {}
    for k, v in ui_views.items():
        data = v.get("data", [])
        ui_counts[k] = len(data)

    # Map generic CLI keys to their resolved UI equivalents
    prefix = "Mysql" if is_mysql else "Psql"
    cli_key_remap = {
        "_DatabaseTableView": f"{prefix}DatabaseTableView",
        "_DbserverView": f"{prefix}DbserverView",
    }

    # LB Pool Members: JSON nests members inside pool objects, CLI flattens them.
    # Compute real UI member count from both pool views.
    lb_pool_data = ui_views.get("LoadBalancingPoolView", {}).get("data", [])
    lb_member_data = ui_views.get("LoadBalancingPoolMemberView", {}).get("data", [])
    real_lb_member_count = 0
    for pool in lb_pool_data:
        if isinstance(pool, dict):
            real_lb_member_count += len(pool.get("members", []))
    for pool in lb_member_data:
        if isinstance(pool, dict) and pool.get("members"):
            real_lb_member_count += len(pool.get("members", []))

    # Compare each view
    all_keys = set(list(cli_counts.keys()) + list(ui_counts.keys()))
    for key in sorted(all_keys):
        resolved_key = cli_key_remap.get(key, key)
        cli_count = cli_counts.get(key, cli_counts.get(resolved_key, -1))
        ui_count = ui_counts.get(resolved_key, ui_counts.get(key, -1))

        # Skip internal/generic keys
        if key.startswith("Unknown_"):
            continue

        # LB Pool Members: use real member count
        if resolved_key == "LoadBalancingPoolMemberView" and real_lb_member_count > 0:
            ui_count = real_lb_member_count

        comp = {
            "cli_count": cli_count if cli_count >= 0 else 0,
            "ui_count": ui_count if ui_count >= 0 else 0,
        }

        if cli_count > 0 and ui_count <= 0:
            comp["status"] = "MISSING_IN_UI"
            result["bugs"].append(f"View {resolved_key}: CLI has {cli_count} items but UI has 0")
        elif cli_count <= 0 and ui_count > 0:
            comp["status"] = "UI_ONLY"
        elif cli_count > 0 and ui_count > 0:
            diff = abs(cli_count - ui_count)
            if diff == 0:
                comp["status"] = "MATCH"
            elif diff <= max(2, cli_count * 0.1):
                comp["status"] = "CLOSE"
            else:
                comp["status"] = "MISMATCH"
                if cli_count > ui_count:
                    result["gaps"].append(
                        f"View {resolved_key}: CLI={cli_count} vs UI={ui_count} (UI missing {cli_count - ui_count})")
        else:
            comp["status"] = "BOTH_EMPTY"

        result["view_comparison"][resolved_key] = comp

    # ── 2. Field-level null checks (JSON API missing data) ────────────────

    # Check for critical null fields in key views
    # DB-type-specific field checks
    if is_mysql:
        critical_field_checks = {
            "MysqlDatabaseView": [
                "hll", "appConnections", "allConnections",
                "queriesPerSecond", "residentSetSizeMb", "isReadOnly",
            ],
            "MysqlDatabaseReplicationView": [
                "slaveIoRunning", "slaveSqlRunning", "usingGtid",
                "parallelMode", "slaveParallelThreads", "secondsBehindMaster",
            ],
            "MysqlDbserverView": ["lifeCycle"],
        }
    else:
        critical_field_checks = {
            "PsqlDatabaseView": [
                "appConnections", "allConnections",
                "residentSetSizeMb",
            ],
            "PsqlDatabaseReplicationView": [
                "replicationLag", "sysClusterStateLag",
            ],
            "PsqlDbserverView": ["lifeCycle"],
        }
    # Also check the alternate prefix view if it exists (mixed-prefix instances)
    alt_prefix = "Psql" if is_mysql else "Mysql"
    for alt_key in [f"{alt_prefix}DbserverView"]:
        if alt_key in ui_views and alt_key not in critical_field_checks:
            critical_field_checks[alt_key] = ["lifeCycle"]

    for view_key, fields in critical_field_checks.items():
        view_data = ui_views.get(view_key, {}).get("data", [])
        if not view_data:
            continue
        for field in fields:
            null_count = sum(1 for item in view_data if item.get(field) is None)
            total = len(view_data)
            if null_count > 0:
                pct = null_count / total * 100
                severity = "BUG" if pct > 80 else "GAP"
                entry = f"{view_key}.{field}: {null_count}/{total} null ({pct:.0f}%)"
                result["field_gaps"].append(entry)
                if severity == "BUG":
                    result["bugs"].append(f"Field always null: {view_key}.{field}")

    # ── 3. Health score sanity checks ─────────────────────────────────────

    health = ui_data.get("health", {})
    overall = health.get("overall", -1)
    cats = health.get("categories", {})

    # Check for 0-score categories that might be false alarms
    for cat_key, cat_val in cats.items():
        score = cat_val.get("score", -1)
        if score == 0:
            # Check if there's actually data in the relevant view
            issues = cat_val.get("issues", [])
            result["bugs"].append(
                f"Health category '{cat_val.get('label', cat_key)}' scored 0 — "
                f"{len(issues)} issues: {'; '.join(i.get('msg','')[:60] for i in issues[:3])}"
            )

    # Check for databases=0 false alarm (the original bug we fixed)
    db_cat = cats.get("databases", {})
    if db_cat.get("score", 100) == 0:
        db_view_key = f"{prefix}DatabaseView"
        db_count = len(ui_views.get(db_view_key, {}).get("data", []))
        if db_count > 0:
            result["bugs"].append(
                f"Databases scored 0 but has {db_count} databases — likely false alarm from health computation")

    # ── 4. Deep analysis checks ───────────────────────────────────────────

    deep = ui_data.get("deep_analysis", {})
    if not deep:
        result["missing_features"].append("No deep analysis computed")
    else:
        composite = deep.get("composite", {})
        if not composite:
            result["missing_features"].append("Deep analysis has no composite score")
        red_flags = deep.get("red_flags", [])
        if red_flags:
            result["gaps"].append(f"Red flags: {'; '.join(str(f)[:80] for f in red_flags[:5])}")

    # ── 5. Instance info completeness ─────────────────────────────────────

    inst_info = ui_data.get("instance_info", {})
    if not inst_info:
        result["missing_features"].append("No instance_info in API response")
    else:
        for field in ["name", "version", "customer", "status", "datacenter"]:
            if not inst_info.get(field):
                result["gaps"].append(f"instance_info.{field} is empty")

    # ── 6. Check for Psql ghost views (wrong prefix) ──────────────────────

    if is_mysql:
        for key in ui_views:
            if key.startswith("Psql"):
                result["bugs"].append(f"MySQL instance has Psql ghost view: {key} ({len(ui_views[key].get('data',[]))} items)")

    if is_psql:
        for key in ui_views:
            if key.startswith("Mysql"):
                result["bugs"].append(f"PostgreSQL instance has Mysql ghost view: {key} ({len(ui_views[key].get('data',[]))} items)")

    # ── 7. Error analysis ─────────────────────────────────────────────────

    errors = ui_data.get("errors", [])
    non_squawk = [e for e in errors if "Squawk" not in str(e)]
    if non_squawk:
        result["gaps"].append(f"Non-squawk errors: {'; '.join(str(e)[:80] for e in non_squawk[:5])}")

    return result


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(result: dict, output_dir: str):
    """Write a per-instance comparison report."""
    instance = result["instance"]
    path = os.path.join(output_dir, f"{instance}.txt")

    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"BugHunt Report: {instance}")
    lines.append(f"DB Type: {result.get('db_type', '?')}")
    lines.append(f"Overall Health: {result.get('health', {}).get('overall', '?')}")
    lines.append(f"{'='*70}")

    # Bugs
    lines.append(f"\n--- BUGS ({len(result['bugs'])}) ---")
    for b in result["bugs"]:
        lines.append(f"  [BUG] {b}")

    # Missing Features
    lines.append(f"\n--- MISSING FEATURES ({len(result['missing_features'])}) ---")
    for m in result["missing_features"]:
        lines.append(f"  [MISSING] {m}")

    # Gaps
    lines.append(f"\n--- GAPS ({len(result['gaps'])}) ---")
    for g in result["gaps"]:
        lines.append(f"  [GAP] {g}")

    # View Comparison
    lines.append(f"\n--- VIEW COMPARISON ---")
    lines.append(f"  {'View':<40} {'CLI':>6} {'UI':>6} {'Status':<15}")
    lines.append(f"  {'-'*40} {'-'*6} {'-'*6} {'-'*15}")
    for vk, vc in sorted(result["view_comparison"].items()):
        cli = vc["cli_count"]
        ui = vc["ui_count"]
        st = vc["status"]
        flag = " <<<" if st in ("MISSING_IN_UI", "MISMATCH") else ""
        lines.append(f"  {vk:<40} {cli:>6} {ui:>6} {st:<15}{flag}")

    # Field Gaps
    if result["field_gaps"]:
        lines.append(f"\n--- FIELD GAPS ---")
        for fg in result["field_gaps"]:
            lines.append(f"  {fg}")

    # Errors
    errors = result.get("errors", [])
    if errors:
        lines.append(f"\n--- API ERRORS ({len(errors)}) ---")
        for e in errors[:20]:
            lines.append(f"  {str(e)[:120]}")

    lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))

    return path


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def process_instance(instance: str, idx: int, total: int) -> dict:
    """Process a single instance: run CLI + UI, compare, save report."""
    t0 = time.time()
    print(f"[{idx+1}/{total}] {instance} — starting...", flush=True)

    # Run CLI and UI in parallel (but semaphore limits total concurrency)
    cli_task = run_cli(instance)
    ui_task = run_ui(instance)

    cli_result, ui_result = await asyncio.gather(cli_task, ui_task, return_exceptions=True)

    # Handle exceptions
    if isinstance(cli_result, Exception):
        cli_ok, cli_text = False, str(cli_result)
    else:
        cli_ok, cli_text = cli_result

    if isinstance(ui_result, Exception):
        ui_ok, ui_data = False, {"error": str(ui_result)}
    else:
        ui_ok, ui_data = ui_result
        if not isinstance(ui_data, dict):
            ui_data = {"error": str(ui_data)}

    elapsed = time.time() - t0

    # Skip if both failed
    if not cli_ok and not ui_ok:
        report = {
            "instance": instance,
            "bugs": [f"Both CLI and UI failed: CLI={cli_text[:100]}, UI={ui_data.get('error','?')[:100]}"],
            "missing_features": [],
            "gaps": [],
            "view_comparison": {},
            "field_gaps": [],
            "health": {},
            "errors": [],
            "db_type": "unknown",
        }
        write_report(report, BUGHUNT_DIR)
        print(f"[{idx+1}/{total}] {instance} — BOTH FAILED ({elapsed:.1f}s)", flush=True)
        return report

    if not cli_ok:
        # UI only - still save what we can
        report = {
            "instance": instance,
            "bugs": [f"CLI failed: {str(cli_text)[:200]}"],
            "missing_features": [],
            "gaps": [],
            "view_comparison": {},
            "field_gaps": [],
            "health": ui_data.get("health", {}),
            "errors": ui_data.get("errors", []),
            "db_type": "unknown",
        }
        write_report(report, BUGHUNT_DIR)
        print(f"[{idx+1}/{total}] {instance} — CLI FAILED ({elapsed:.1f}s)", flush=True)
        return report

    if not ui_ok:
        report = {
            "instance": instance,
            "bugs": [f"UI API failed: {str(ui_data.get('error','?'))[:200]}"],
            "missing_features": [],
            "gaps": [],
            "view_comparison": {},
            "field_gaps": [],
            "health": {},
            "errors": [],
            "db_type": "unknown",
        }
        write_report(report, BUGHUNT_DIR)
        print(f"[{idx+1}/{total}] {instance} — UI FAILED ({elapsed:.1f}s)", flush=True)
        return report

    # Both succeeded — compare
    try:
        result = compare_instance(instance, cli_text, ui_data)
    except Exception as e:
        result = {
            "instance": instance,
            "bugs": [f"Comparison error: {e}"],
            "missing_features": [],
            "gaps": [],
            "view_comparison": {},
            "field_gaps": [],
            "health": ui_data.get("health", {}),
            "errors": [],
            "db_type": "unknown",
        }

    write_report(result, BUGHUNT_DIR)
    bug_count = len(result["bugs"])
    gap_count = len(result["gaps"])
    missing_count = len(result["missing_features"])
    health = result.get("health", {}).get("overall", "?")
    status = "CLEAN" if bug_count == 0 else f"{bug_count} BUG(S)"
    print(f"[{idx+1}/{total}] {instance} — {status}, {gap_count} gaps, health={health} ({elapsed:.1f}s)", flush=True)

    return result


async def main():
    global semaphore
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    # Load instance list
    data_path = os.path.join(BASE_DIR, "output", "ruckus_aggregated_data.json")
    with open(data_path) as f:
        all_data = json.load(f)

    instances = [r["instance"] for r in all_data if r.get("instance")]
    total = len(instances)
    print(f"\n{'='*70}")
    print(f"  BugHunt — Comparing {total} instances (CLI vs UI)")
    print(f"  Concurrency: {MAX_CONCURRENCY}")
    print(f"  Output: {BUGHUNT_DIR}/")
    print(f"{'='*70}\n")

    os.makedirs(BUGHUNT_DIR, exist_ok=True)

    # Process in batches to avoid overwhelming everything
    BATCH_SIZE = 10
    all_results = []

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = instances[batch_start:batch_end]
        tasks = [
            process_instance(inst, batch_start + i, total)
            for i, inst in enumerate(batch)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                all_results.append({
                    "instance": "?", "bugs": [str(r)],
                    "missing_features": [], "gaps": [],
                    "view_comparison": {}, "field_gaps": [],
                })
            else:
                all_results.append(r)

    # ── Write summary ─────────────────────────────────────────────────────

    write_summary(all_results, BUGHUNT_DIR)
    print(f"\n{'='*70}")
    print(f"  BugHunt complete! Reports in: {BUGHUNT_DIR}/")
    print(f"  Summary: {os.path.join(BUGHUNT_DIR, '_SUMMARY.txt')}")
    print(f"{'='*70}")


def write_summary(results: list, output_dir: str):
    """Write aggregate summary of all comparisons."""
    lines = []
    lines.append("=" * 70)
    lines.append("BugHunt Summary — All Instances")
    lines.append(f"Total instances: {len(results)}")
    lines.append("=" * 70)

    # Aggregate stats
    total_bugs = 0
    total_gaps = 0
    total_missing = 0
    bug_instances = []
    all_bugs = defaultdict(int)  # bug message → count
    all_gaps = defaultdict(int)
    all_missing = defaultdict(int)
    all_field_gaps = defaultdict(int)

    view_missing_counts = defaultdict(int)  # view → how many instances it's missing in

    for r in results:
        bugs = r.get("bugs", [])
        gaps = r.get("gaps", [])
        missing = r.get("missing_features", [])
        field_gaps = r.get("field_gaps", [])

        total_bugs += len(bugs)
        total_gaps += len(gaps)
        total_missing += len(missing)

        if bugs:
            bug_instances.append(r["instance"])

        for b in bugs:
            # Normalize bug message (strip instance-specific details)
            normalized = _normalize_bug(b)
            all_bugs[normalized] += 1

        for g in gaps:
            normalized = _normalize_bug(g)
            all_gaps[normalized] += 1

        for m in missing:
            all_missing[m] += 1

        for fg in field_gaps:
            # Extract field name pattern
            field_name = fg.split(":")[0] if ":" in fg else fg
            all_field_gaps[field_name] += 1

        # Track view-level missing
        for vk, vc in r.get("view_comparison", {}).items():
            if vc.get("status") == "MISSING_IN_UI":
                view_missing_counts[vk] += 1

    lines.append(f"\nTotal bugs: {total_bugs}")
    lines.append(f"Total gaps: {total_gaps}")
    lines.append(f"Total missing features: {total_missing}")
    lines.append(f"Instances with bugs: {len(bug_instances)}/{len(results)}")

    # Top bugs by frequency
    lines.append(f"\n{'='*70}")
    lines.append("TOP BUGS BY FREQUENCY")
    lines.append(f"{'='*70}")
    for bug, count in sorted(all_bugs.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"  [{count:>3}x] {bug}")

    # Top gaps by frequency
    lines.append(f"\n{'='*70}")
    lines.append("TOP GAPS BY FREQUENCY")
    lines.append(f"{'='*70}")
    for gap, count in sorted(all_gaps.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"  [{count:>3}x] {gap}")

    # Missing features
    lines.append(f"\n{'='*70}")
    lines.append("MISSING FEATURES")
    lines.append(f"{'='*70}")
    for m, count in sorted(all_missing.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count:>3}x] {m}")

    # Field gaps
    lines.append(f"\n{'='*70}")
    lines.append("FIELD-LEVEL GAPS (null fields in JSON)")
    lines.append(f"{'='*70}")
    for fg, count in sorted(all_field_gaps.items(), key=lambda x: -x[1])[:30]:
        lines.append(f"  [{count:>3}x] {fg}")

    # Views missing in UI
    lines.append(f"\n{'='*70}")
    lines.append("VIEWS MISSING IN UI (present in CLI but not UI)")
    lines.append(f"{'='*70}")
    for vk, count in sorted(view_missing_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count:>3}x] {vk}")

    # DB type distribution
    db_types = defaultdict(int)
    for r in results:
        db_types[r.get("db_type", "unknown")] += 1
    lines.append(f"\n{'='*70}")
    lines.append("DB TYPE DISTRIBUTION")
    lines.append(f"{'='*70}")
    for dt, count in sorted(db_types.items(), key=lambda x: -x[1]):
        lines.append(f"  {dt}: {count}")

    # Health score distribution
    health_scores = []
    for r in results:
        h = r.get("health", {}).get("overall", -1)
        if h >= 0:
            health_scores.append(h)
    if health_scores:
        lines.append(f"\n{'='*70}")
        lines.append("HEALTH SCORE DISTRIBUTION")
        lines.append(f"{'='*70}")
        lines.append(f"  Count: {len(health_scores)}")
        lines.append(f"  Mean:  {sum(health_scores)/len(health_scores):.1f}")
        lines.append(f"  Min:   {min(health_scores)}")
        lines.append(f"  Max:   {max(health_scores)}")
        buckets = {"0-49": 0, "50-69": 0, "70-84": 0, "85-94": 0, "95-100": 0}
        for s in health_scores:
            if s < 50: buckets["0-49"] += 1
            elif s < 70: buckets["50-69"] += 1
            elif s < 85: buckets["70-84"] += 1
            elif s < 95: buckets["85-94"] += 1
            else: buckets["95-100"] += 1
        for bucket, count in buckets.items():
            bar = "█" * int(count / max(len(health_scores), 1) * 50)
            lines.append(f"  {bucket:>7}: {count:>4} {bar}")

    # Instances with bugs (list)
    lines.append(f"\n{'='*70}")
    lines.append(f"INSTANCES WITH BUGS ({len(bug_instances)})")
    lines.append(f"{'='*70}")
    for inst in sorted(bug_instances):
        lines.append(f"  {inst}")

    lines.append("")

    path = os.path.join(output_dir, "_SUMMARY.txt")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"\nSummary written to: {path}")


def _normalize_bug(msg: str) -> str:
    """Normalize a bug message by stripping instance-specific details."""
    import re
    # Replace instance names, IPs, numbers with placeholders
    msg = re.sub(r'\b(db|app)\d+\.\w+\.\w+\b', '<server>', msg)
    msg = re.sub(r'\bAlert\d+\b', 'Alert<N>', msg)
    msg = re.sub(r'\bCHG\d+\b', 'CHG<N>', msg)
    msg = re.sub(r'\d+/\d+', '<N>/<N>', msg)
    # Keep the pattern but normalize counts
    msg = re.sub(r'CLI=\d+', 'CLI=<N>', msg)
    msg = re.sub(r'UI=\d+', 'UI=<N>', msg)
    msg = re.sub(r'CLI has \d+', 'CLI has <N>', msg)
    msg = re.sub(r'UI has \d+', 'UI has <N>', msg)
    msg = re.sub(r'UI missing \d+', 'UI missing <N>', msg)
    msg = re.sub(r'\d+ items', '<N> items', msg)
    msg = re.sub(r'\d+ databases', '<N> databases', msg)
    msg = re.sub(r'\d+ issues', '<N> issues', msg)
    msg = re.sub(r'scored \d+', 'scored <N>', msg)
    return msg


if __name__ == "__main__":
    try:
        import aiohttp
    except ImportError:
        print("Installing aiohttp...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
        import aiohttp

    asyncio.run(main())
