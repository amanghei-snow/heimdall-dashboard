"""
Async data collector using the ruckus CLI.

Runs ruckus in batch + JSON mode for each instance, collecting
multiple views (databases, tables, nodes, app servers, etc.).
Supports caching, resumption, and concurrent execution.
"""
import asyncio
import json
import os
import tempfile
import time
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

import config

console = Console()


# ── Cache helpers ────────────────────────────────────────────────────────────

def _cache_path(instance: str) -> str:
    return os.path.join(config.CACHE_DIR, f"{instance}.json")


def _load_cached(instance: str, bindings: str = None) -> Optional[dict]:
    path = _cache_path(instance)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Cache valid for 72 hours
            if time.time() - data.get("_ts", 0) < 259200:
                # Invalidate if bindings changed (new features added)
                if bindings and data.get("_bindings") and data["_bindings"] != bindings:
                    return None
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _save_cache(instance: str, data: dict, bindings: str = None):
    data["_ts"] = time.time()
    if bindings:
        data["_bindings"] = bindings
    path = _cache_path(instance)
    # If new collection failed (no views), keep the previous cache as .prev
    # so downstream can recover top_tables and other view data.
    new_views = len(data.get("views", {}))
    if new_views == 0 and os.path.exists(path):
        try:
            with open(path) as _f:
                old = json.load(_f)
            if len(old.get("views", {})) > 0:
                prev_path = path + ".prev"
                os.replace(path, prev_path)
        except Exception:
            pass
    with open(path, "w") as f:
        json.dump(data, f)


def find_retry_candidates() -> dict:
    """Scan cache for failed/partial instances.

    Returns {"failed": [...], "partial": [...], "success": [...]}
    where each entry is {"instance": str, "views": int, "chunk_failures": int, "errors": int}.
    """
    results = {"failed": [], "partial": [], "success": []}
    cache_dir = config.CACHE_DIR
    if not os.path.isdir(cache_dir):
        return results
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".json"):
            continue
        inst = fname[:-5]
        try:
            with open(os.path.join(cache_dir, fname)) as f:
                data = json.load(f)
        except Exception:
            results["failed"].append({"instance": inst, "views": 0, "chunk_failures": 0, "errors": 1})
            continue

        n_views = len(data.get("views", {}))
        chunk_stats = data.get("chunk_stats", {})
        chunk_failures = sum(
            1 for cs in chunk_stats.values()
            if isinstance(cs, dict) and cs.get("status", "").startswith("failed")
        )
        n_errors = len(data.get("errors", []))
        entry = {
            "instance": inst,
            "views": n_views,
            "chunk_failures": chunk_failures,
            "errors": n_errors,
            "chunks": {k: v for k, v in chunk_stats.items()} if chunk_stats else {},
        }
        if n_views == 0:
            results["failed"].append(entry)
        elif chunk_failures > 0:
            results["partial"].append(entry)
        else:
            results["success"].append(entry)
    return results


# ── Ruckus subprocess runner ────────────────────────────────────────────────

async def _run_ruckus(
    instance: str,
    bindings: str,
    username: str = None,
    password: str = None,
    env_flag: str = None,
    timeout: int = None,
    json_full: bool = True,
) -> Tuple[bool, str]:
    """
    Run ruckus CLI for a single instance + binding set.
    Returns (success, stdout_or_error).
    Set json_full=False for text/batch output (has more data for some views).
    """
    # Positional args (ci_name, bindings) must come before flags
    cmd = [config.RUCKUS_BIN, instance, bindings, "-b"]
    if json_full:
        cmd.append("--json-full")

    if username:
        cmd.extend(["-u", username])
    if password:
        cmd.extend(["-p", password])
    if env_flag:
        cmd.append(env_flag)

    tout = timeout or config.RUCKUS_TIMEOUT

    # Write stdout to temp file to avoid pipe buffer stalls on large responses
    tmp = tempfile.NamedTemporaryFile(mode="w+b", suffix=".json", delete=False)
    tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=tmp,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=tout)
        tmp.close()

        with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if proc.returncode == 0:
            return True, content
        else:
            # ruckus returns exit 1 when some bindings fail but still
            # produces valid partial JSON — return content if present
            if content.strip() and "{" in content:
                return True, content
            return False, f"Exit {proc.returncode}"

    except asyncio.TimeoutError:
        tmp.close()
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"Timeout after {tout}s"
    except FileNotFoundError:
        tmp.close()
        return False, "ruckus binary not found — is it installed?"
    except Exception as e:
        tmp.close()
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── JSON parser ──────────────────────────────────────────────────────────────

def _extract_json(raw_output: str) -> Optional[dict]:
    """Extract the JSON object from ruckus output (may have extra text)."""
    # ruckus --json-full outputs a JSON object after a header line;
    # use raw_decode which correctly handles strings containing braces
    decoder = json.JSONDecoder()
    # Try finding JSON start at newline-{ (common pattern) first
    idx = raw_output.find("\n{")
    if idx >= 0:
        try:
            obj, _ = decoder.raw_decode(raw_output, idx + 1)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Fallback: scan for first {
    idx = raw_output.find("{")
    while idx >= 0 and idx < len(raw_output):
        try:
            obj, _ = decoder.raw_decode(raw_output, idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = raw_output.find("{", idx + 1)
    return None


def _parse_combined_response(raw: str) -> dict:
    """Parse a combined ruckus JSON response into per-view dicts."""
    parsed = _extract_json(raw)
    if not parsed:
        return {"_error": "Could not parse JSON", "_raw_preview": raw[:500]}

    # Detect fatal errors (e.g. "Cannot find CI: xxx") — no point retrying
    errors = parsed.get("errors", [])
    for e in errors:
        if isinstance(e, str) and "Cannot find CI" in e:
            return {"_error": e, "_fatal": True, "views": {}, "errors": errors}

    views = {}
    data_block = parsed.get("Data", {})
    for ruckus_key, our_label in config.RUCKUS_VIEW_MAP.items():
        if ruckus_key in data_block:
            views[our_label] = data_block[ruckus_key]

    return {"views": views, "errors": errors}


# ── Collector for a single instance (chunked calls with retry) ────────────────

async def _collect_instance(
    instance: str,
    username: str = None,
    password: str = None,
    env_flag: str = None,
    semaphore: asyncio.Semaphore = None,
    progress_callback=None,
) -> dict:
    """Collect all views for a single instance, one binding flag at a time.

    Ruckus works reliably when given a single flag per call (~5-15s load
    time).  Flooding it with many flags causes timeouts and empty results.
    Each flag is called sequentially; failed flags are retried once.

    The semaphore gates the *entire instance* collection, not individual
    flag calls — so N instances run in parallel, each doing its flags
    sequentially.
    """
    result = {
        "instance": instance,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "views": {},
        "errors": [],
        "chunk_stats": {},
    }

    chunks = getattr(config, "RUCKUS_BINDING_CHUNKS", None)
    if not chunks:
        chunks = list(config.RUCKUS_COMBINED_BINDINGS)

    chunk_timeout = getattr(config, "RUCKUS_CHUNK_TIMEOUT", config.RUCKUS_TIMEOUT)
    max_retries = getattr(config, "RUCKUS_CHUNK_RETRIES", 1)

    async def _run_flag(flag: str) -> dict:
        """Run ruckus for a single binding flag and return parsed result."""
        t0 = time.time()
        ok, output = await _run_ruckus(
            instance, flag, username, password, env_flag,
            timeout=chunk_timeout,
        )
        elapsed = round(time.time() - t0, 1)

        if ok:
            parsed = _parse_combined_response(output)
            if "_error" in parsed:
                return {
                    "ok": False, "views": {}, "elapsed": elapsed,
                    "errors": [{"view": flag, "error": parsed["_error"]}],
                }
            return {
                "ok": True, "views": parsed["views"], "elapsed": elapsed,
                "errors": [{"view": "ruckus", "error": str(e)} for e in parsed.get("errors", [])],
            }
        else:
            return {
                "ok": False, "views": {}, "elapsed": elapsed,
                "errors": [{"view": flag, "error": output}],
            }

    # Run each flag sequentially, retry failures once
    for flag in chunks:
        flag_result = await _run_flag(flag)

        # Bail out immediately if the instance doesn't exist
        if not flag_result["ok"]:
            errs = flag_result.get("errors", [])
            if any("Cannot find CI" in (e.get("error", "") if isinstance(e, dict) else "") for e in errs):
                result["errors"] = errs
                result["chunk_stats"][flag] = {
                    "status": "failed (invalid instance)", "views": 0,
                    "elapsed": flag_result["elapsed"],
                }
                return result

        if flag_result["ok"]:
            for vk, vdata in flag_result["views"].items():
                if vk not in result["views"] or not result["views"][vk]:
                    result["views"][vk] = vdata
            result["chunk_stats"][flag] = {
                "status": "ok", "views": len(flag_result["views"]),
                "elapsed": flag_result["elapsed"],
            }
        else:
            # Retry once
            retry_result = await _run_flag(flag)
            if retry_result["ok"]:
                for vk, vdata in retry_result["views"].items():
                    if vk not in result["views"] or not result["views"][vk]:
                        result["views"][vk] = vdata
                result["chunk_stats"][flag] = {
                    "status": "ok (retry)", "views": len(retry_result["views"]),
                    "elapsed": retry_result["elapsed"],
                }
            else:
                result["chunk_stats"][flag] = {
                    "status": "failed", "views": 0,
                    "elapsed": flag_result["elapsed"],
                    "error": flag_result["errors"][0]["error"] if flag_result["errors"] else "unknown",
                }
                result["errors"].append({
                    "view": flag,
                    "error": f"Binding '{flag}' failed after retry",
                })

        # Forward non-fatal ruckus errors
        for err in flag_result["errors"]:
            if err.get("view") == "ruckus":
                result["errors"].append(err)

        if progress_callback:
            progress_callback(instance, flag, flag_result.get("ok", False))

    return result


# ── Batch collector ──────────────────────────────────────────────────────────

async def _collect_batch(
    instances: List[str],
    username: str = None,
    password: str = None,
    env_flag: str = None,
    concurrency: int = None,
    progress_callback=None,
) -> Dict[str, dict]:
    """Collect data for all instances concurrently."""
    sem = asyncio.Semaphore(concurrency or config.RUCKUS_CONCURRENCY)
    results = {}

    async def _worker(inst: str):
        async with sem:
            data = await _collect_instance(
                inst, username, password, env_flag,
            )
        _save_cache(inst, data, bindings=config.RUCKUS_COMBINED_BINDINGS)
        results[inst] = data
        if progress_callback:
            progress_callback(inst)

    tasks = [_worker(inst) for inst in instances]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


# ── Public API ───────────────────────────────────────────────────────────────

def collect_all(
    instances: List[str],
    username: str = None,
    password: str = None,
    env_flag: str = None,
    concurrency: int = None,
    use_cache: bool = True,
) -> Dict[str, dict]:
    """
    Main entry point: collect ruckus data for all instances.
    Uses a single combined ruckus call per instance (1 call instead of 8).
    Returns {instance_name: collected_data_dict}.
    """
    chunks = getattr(config, "RUCKUS_BINDING_CHUNKS", None)
    n_chunks = len(chunks) if chunks else 1
    console.print(f"\n[bold cyan]Step 2: Collecting data via ruckus[/bold cyan]")
    console.print(f"  Bindings: [cyan]{config.RUCKUS_COMBINED_BINDINGS}[/cyan] ({n_chunks} chunk{'s' if n_chunks > 1 else ''} per instance)")

    os.makedirs(config.CACHE_DIR, exist_ok=True)

    # Check cache first
    to_collect = []
    cached_results = {}

    if use_cache:
        for inst in instances:
            cached = _load_cached(inst, bindings=config.RUCKUS_COMBINED_BINDINGS)
            if cached:
                cached_results[inst] = cached
            else:
                to_collect.append(inst)
        if cached_results:
            console.print(
                f"  [green]{len(cached_results)}[/green] instances loaded from cache "
                f"(< 72h old)"
            )
    else:
        to_collect = list(instances)

    if not to_collect:
        console.print("  All instances loaded from cache — nothing to collect")
        return cached_results

    conc = concurrency or config.RUCKUS_CONCURRENCY
    console.print(
        f"  Collecting [cyan]{len(to_collect)}[/cyan] instances "
        f"(concurrency={conc})"
    )

    # Progress bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Collecting...", total=len(to_collect))
        status_file = os.path.join(config.OUTPUT_DIR, "status.txt")
        _completed = [0]

        def on_complete(inst):
            progress.advance(task)
            _completed[0] += 1
            try:
                with open(status_file, "w") as sf:
                    sf.write(f"{_completed[0]}/{len(to_collect)} instances collected\n")
                    sf.write(f"Last: {inst}\n")
                    sf.write(f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            except Exception:
                pass
            # Update scan progress file for dashboard
            try:
                pct = int(_completed[0] / len(to_collect) * 100)
                progress_file = os.path.join("data", ".scan.progress")
                with open(progress_file, "w") as pf:
                    json.dump({
                        "stage": "collecting",
                        "pct": pct,
                        "detail": f"{_completed[0]}/{len(to_collect)} instances ({inst})",
                        "ts": time.time(),
                    }, pf)
            except Exception:
                pass

        loop = asyncio.new_event_loop()
        try:
            new_results = loop.run_until_complete(
                _collect_batch(
                    to_collect,
                    username,
                    password,
                    env_flag,
                    concurrency,
                    on_complete,
                )
            )
        finally:
            loop.close()

    # Merge cached + new
    all_results = {**cached_results, **new_results}

    # Summary — use chunk_stats to distinguish full vs partial
    success_count = 0
    partial_count = 0
    fail_count = 0
    for d in all_results.values():
        has_views = len(d.get("views", {})) > 0
        chunk_stats = d.get("chunk_stats", {})
        chunk_failures = sum(
            1 for cs in chunk_stats.values()
            if cs.get("status", "").startswith("failed")
        )
        if not has_views:
            fail_count += 1
        elif chunk_failures > 0:
            partial_count += 1
        else:
            success_count += 1

    console.print(
        f"  Results: [green]{success_count} full[/green] · "
        f"[yellow]{partial_count} partial[/yellow] · "
        f"[red]{fail_count} failed[/red]"
    )
    if partial_count > 0:
        console.print(
            f"  [dim]Partial = some chunks timed out but other views collected OK[/dim]"
        )

    return all_results


# ── Data extraction helpers (for use by reporter) ───────────────────────────

def extract_db_info(data: dict) -> dict:
    """Extract database size info from collected ruckus data."""
    info = {"total_size_gb": 0, "databases": []}
    # databases view is a flat list of db server entries with sizeMb
    view = data.get("views", {}).get("databases", [])
    if isinstance(view, list):
        for db in view:
            if not isinstance(db, dict):
                continue
            size_mb = db.get("sizeMb", 0) or 0
            info["databases"].append(db)
            info["total_size_gb"] += size_mb / 1024
    # Also check dbservers view
    view2 = data.get("views", {}).get("dbservers", [])
    if isinstance(view2, list) and not info["databases"]:
        for db in view2:
            if not isinstance(db, dict):
                continue
            size_mb = db.get("sizeMb", 0) or 0
            info["databases"].append(db)
            info["total_size_gb"] += size_mb / 1024
    return info


def extract_table_info(data: dict) -> list:
    """Extract table-level info from collected ruckus data.

    Deduplicates across DB replicas — keeps the entry with the largest
    total size for each unique table name.
    """
    view = data.get("views", {}).get("tables", [])
    if not isinstance(view, list):
        return []
    unique = {}
    for t in view:
        if not isinstance(t, dict):
            continue
        name = t.get("name", "")
        dl = t.get("dataLength", 0) or 0
        il = t.get("indexLength", 0) or 0
        total = dl + il
        entry = {
            "name": name,
            "schema": t.get("schema", ""),
            "rows": t.get("rows", 0),
            "data_length": dl,
            "index_length": il,
            "total_size": total,
            "server": t.get("cmdbName", "") or (t.get("database", {}).get("host", {}).get("name", "") if isinstance(t.get("database"), dict) else ""),
        }
        if name not in unique or total > unique[name]["total_size"]:
            unique[name] = entry
    return list(unique.values())


def extract_node_info(data: dict) -> dict:
    """Extract node/app server info from collected ruckus data."""
    info = {"node_count": 0, "nodes": [], "app_servers": []}
    # nodes view is a flat list with host, operationalStatus, etc.
    nodes_view = data.get("views", {}).get("nodes", [])
    if isinstance(nodes_view, list):
        info["nodes"] = nodes_view
        info["node_count"] = len(nodes_view)
    # appservers view is a flat list
    apps_view = data.get("views", {}).get("appservers", [])
    if isinstance(apps_view, list):
        info["app_servers"] = apps_view
    return info


def extract_instance_info(data: dict) -> dict:
    """Extract instance-level metadata from collected ruckus data."""
    view = data.get("views", {}).get("instance", {})
    if not view or "_error" in view:
        return {}
    return view


# ── Capacity tier extraction ──────────────────────────────────────────────

def extract_capacity_tier(data: dict) -> str:
    """Extract capacity tier from ruckus databases or dbservers view."""
    for view_key in ("databases", "dbservers"):
        view = data.get("views", {}).get(view_key, [])
        if not isinstance(view, list):
            continue
        for item in view:
            if not isinstance(item, dict):
                continue
            cap = item.get("capacitySize", "") or item.get("desiredCapacitySize", "")
            if cap and str(cap).lower() not in ("", "null", "none"):
                return str(cap).strip().lower()
    return ""


# ── Targeted collection (fill only missing values) ──────────────────────

# Mapping: which internal view keys are provided by which binding char
_BINDING_FOR_VIEW = {
    "instance":       "i",
    "databases":      "d",
    "dbservers":      "D",
    "tables":         "T",
    "nodes":          "n",
    "appservers":     "A",
    "replication":    "R",
    "queries":        "q",
    "dbpools":        "p",
    "servlets":       "v",
    "semaphores":     "s",
    "semaphoresets":  "S",
    "scheduler":      "w",
    "jobs":           "W",
    "jvmthreads":     "C",
    "backups":        "b",
    "alerts":         "a",
    "changes":        "c",
    "updatesets":     "u",
    "lbpools":        "P",
    "lbmembers":      "M",
}


def determine_missing_bindings(cached: dict, csv_data: dict = None) -> str:
    """Determine which ruckus bindings are needed to fill missing data.

    Returns a binding string like 'dT' for databases+tables, or '' if nothing
    is missing.  Only requests bindings for views that the dashboard actually
    uses (databases, tables, nodes, appservers, instance).
    """
    views = cached.get("views", {})
    csv_d = csv_data or {}

    needed = set()

    # Tier missing → need databases view (has capacitySize)
    has_tier = bool(csv_d.get("capacity_tier"))
    if not has_tier:
        cap = extract_capacity_tier(cached)
        if not cap:
            needed.add("d")  # databases

    # Tables missing
    if not views.get("tables"):
        needed.add("T")

    # Nodes missing
    if not views.get("nodes"):
        needed.add("n")

    # App servers missing
    if not views.get("appservers"):
        needed.add("A")

    # DB size missing (databases or dbservers)
    if not views.get("databases") and not views.get("dbservers"):
        needed.add("d")
        needed.add("D")

    # Instance metadata missing
    if not views.get("instance"):
        needed.add("i")

    bindings = "".join(sorted(needed))
    return bindings


async def _collect_targeted(
    instance: str,
    bindings: str,
    username: str = None,
    password: str = None,
    env_flag: str = None,
    semaphore: asyncio.Semaphore = None,
) -> dict:
    """Run ruckus with only the specified bindings, return parsed views."""
    if semaphore:
        async with semaphore:
            ok, output = await _run_ruckus(
                instance, bindings, username, password, env_flag,
                timeout=config.RUCKUS_CHUNK_TIMEOUT,
            )
    else:
        ok, output = await _run_ruckus(
            instance, bindings, username, password, env_flag,
            timeout=config.RUCKUS_CHUNK_TIMEOUT,
        )

    if ok:
        parsed = _parse_combined_response(output)
        return parsed
    return {"views": {}, "errors": [{"view": bindings, "error": output}]}


def collect_missing(
    instances: List[str],
    collected: Dict[str, dict],
    csv_data: Dict[str, dict],
    username: str = None,
    password: str = None,
    env_flag: str = None,
    concurrency: int = None,
) -> Dict[str, dict]:
    """Targeted collection: for each instance, determine which bindings are
    missing and run ruckus with only those bindings.  Merges new views into
    existing cached data.  Returns the updated collected dict."""

    # Determine what each instance needs
    plan = {}  # inst -> bindings_str
    for inst in instances:
        cd = collected.get(inst, {})
        csv_d = csv_data.get(inst, {})
        bindings = determine_missing_bindings(cd, csv_d)
        if bindings:
            plan[inst] = bindings

    if not plan:
        console.print("  No missing bindings to collect")
        return collected

    # Group by bindings for logging
    binding_groups = {}
    for inst, b in plan.items():
        binding_groups.setdefault(b, []).append(inst)

    console.print(f"\n[bold cyan]Step 2b: Targeted collection for missing data[/bold cyan]")
    for b, insts in sorted(binding_groups.items(), key=lambda x: -len(x[1])):
        console.print(f"  Bindings [cyan]{b}[/cyan]: {len(insts)} instances")

    conc = concurrency or config.RUCKUS_CONCURRENCY

    async def _run_all():
        sem = asyncio.Semaphore(conc)
        updated = 0

        async def _worker(inst, bindings):
            nonlocal updated
            result = await _collect_targeted(
                inst, bindings, username, password, env_flag, sem,
            )
            new_views = result.get("views", {})
            if new_views:
                # Merge into existing cache
                if inst not in collected:
                    collected[inst] = {"instance": inst, "views": {}, "errors": []}
                cd = collected[inst]
                for vk, vdata in new_views.items():
                    if vk not in cd.get("views", {}) or not cd["views"].get(vk):
                        cd.setdefault("views", {})[vk] = vdata
                # Re-save cache
                _save_cache(inst, cd, bindings=bindings)
                updated += 1
            return inst

        tasks = [_worker(inst, bindings) for inst, bindings in plan.items()]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            ptask = progress.add_task("Targeted collection...", total=len(tasks))
            for coro in asyncio.as_completed(tasks):
                await coro
                progress.advance(ptask)

        return updated

    loop = asyncio.new_event_loop()
    try:
        updated = loop.run_until_complete(_run_all())
    finally:
        loop.close()

    console.print(f"  Updated [green]{updated}[/green] / {len(plan)} instances")
    return collected


# ── Tree walkers ─────────────────────────────────────────────────────────────

def _walk_for_dbs(obj, info, depth=0):
    """Recursively walk JSON to find database entries with size info."""
    if depth > 10:
        return
    if isinstance(obj, dict):
        # Look for keys that suggest database info
        if "combinedSize" in obj or "combined_size" in obj or "dataLength" in obj:
            info["databases"].append(obj)
            size = obj.get("combinedSize") or obj.get("combined_size") or 0
            if isinstance(size, (int, float)):
                info["total_size_gb"] += size / (1024 ** 3) if size > 1_000_000 else size
        for v in obj.values():
            _walk_for_dbs(v, info, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_dbs(item, info, depth + 1)


def _walk_for_tables(obj, tables, depth=0):
    """Recursively walk JSON to find table entries."""
    if depth > 10:
        return
    if isinstance(obj, dict):
        if "rows" in obj and "name" in obj:
            tables.append({
                "name": obj.get("name", ""),
                "schema": obj.get("schema", ""),
                "rows": obj.get("rows", 0),
                "data_length": obj.get("dataLength", 0),
                "index_length": obj.get("indexLength", 0),
                "total_size": (obj.get("dataLength", 0) or 0) + (obj.get("indexLength", 0) or 0),
                "server": obj.get("cmdbName", ""),
            })
        for v in obj.values():
            _walk_for_tables(v, tables, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_tables(item, tables, depth + 1)


def _walk_for_nodes(obj, info, depth=0):
    """Recursively walk JSON to find node/server entries."""
    if depth > 10:
        return
    if isinstance(obj, dict):
        # Node entries typically have hostname or name + status
        if ("hostname" in obj or "host" in obj) and ("status" in obj or "state" in obj):
            info["nodes"].append(obj)
            info["node_count"] = len(info["nodes"])
        elif "appServerName" in obj or "appserver" in obj:
            info["app_servers"].append(obj)
        for v in obj.values():
            _walk_for_nodes(v, info, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_nodes(item, info, depth + 1)
