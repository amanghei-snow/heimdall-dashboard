#!/usr/bin/env python3
"""
Standalone batch collector — uses subprocess.run with hard OS timeouts.
No asyncio, no event loops, no deadlocks.

Usage:
    python3 batch_collect.py [--workers 25] [--timeout 120] [--force]
"""
import argparse
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from collector import (
    _cache_path,
    _load_cached,
    _save_cache,
    _parse_combined_response,
    extract_table_info,
)

# ── Sync ruckus call ─────────────────────────────────────────────────────────

def run_ruckus_sync(instance, bindings, timeout=120):
    """Run ruckus CLI synchronously. Returns (ok, output_or_error)."""
    cmd = [config.RUCKUS_BIN, instance, bindings, "-b", "--json-full"]
    tmp = tempfile.NamedTemporaryFile(mode="w+b", suffix=".json", delete=False)
    tmp_path = tmp.name
    try:
        proc = subprocess.run(
            cmd,
            stdout=tmp,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        tmp.close()
        with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if proc.returncode == 0 or (content.strip() and "{" in content):
            return True, content
        return False, f"Exit {proc.returncode}"
    except subprocess.TimeoutExpired:
        tmp.close()
        return False, f"Timeout after {timeout}s"
    except FileNotFoundError:
        tmp.close()
        return False, "ruckus binary not found"
    except Exception as e:
        tmp.close()
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def collect_instance_sync(instance, chunk_timeout=120, instance_timeout=300):
    """Collect all views for one instance synchronously."""
    t0 = time.time()
    result = {
        "instance": instance,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "views": {},
        "errors": [],
        "chunk_stats": {},
    }

    chunks = getattr(config, "RUCKUS_BINDING_CHUNKS", None)
    if not chunks:
        chunks = [config.RUCKUS_COMBINED_BINDINGS]

    for chunk_bindings in chunks:
        if time.time() - t0 > instance_timeout:
            result["errors"].append({"view": chunk_bindings, "error": f"Instance timeout ({instance_timeout}s)"})
            result["chunk_stats"][chunk_bindings] = {"status": "skipped (instance timeout)", "views": 0}
            continue

        ct0 = time.time()
        ok, output = run_ruckus_sync(instance, chunk_bindings, timeout=chunk_timeout)
        elapsed = round(time.time() - ct0, 1)

        if ok:
            parsed = _parse_combined_response(output)
            if "_error" in parsed:
                # Fatal error like "Cannot find CI" — bail immediately
                if parsed.get("_fatal"):
                    result["errors"] = [{"view": chunk_bindings, "error": parsed["_error"]}]
                    result["chunk_stats"][chunk_bindings] = {
                        "status": "failed (invalid instance)", "views": 0, "elapsed": elapsed,
                    }
                    return result
                result["chunk_stats"][chunk_bindings] = {
                    "status": "failed (parse)", "views": 0, "elapsed": elapsed,
                }
                result["errors"].append({"view": chunk_bindings, "error": parsed["_error"]})
            else:
                for vk, vdata in parsed["views"].items():
                    if vk not in result["views"] or not result["views"][vk]:
                        result["views"][vk] = vdata
                result["chunk_stats"][chunk_bindings] = {
                    "status": "ok", "views": len(parsed["views"]), "elapsed": elapsed,
                }
        else:
            # Retry once
            ok2, output2 = run_ruckus_sync(instance, chunk_bindings, timeout=chunk_timeout)
            elapsed2 = round(time.time() - ct0, 1)
            if ok2:
                parsed = _parse_combined_response(output2)
                if "_error" not in parsed:
                    for vk, vdata in parsed["views"].items():
                        if vk not in result["views"] or not result["views"][vk]:
                            result["views"][vk] = vdata
                    result["chunk_stats"][chunk_bindings] = {
                        "status": "ok (retry)", "views": len(parsed["views"]), "elapsed": elapsed2,
                    }
                    continue
            result["chunk_stats"][chunk_bindings] = {
                "status": "failed", "views": 0, "elapsed": elapsed2 if ok2 else elapsed,
                "error": output,
            }
            result["errors"].append({"view": chunk_bindings, "error": output})

    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def find_instances_needing_collection(force=False):
    """Find instances that need (re-)collection."""
    agg_path = os.path.join(config.OUTPUT_DIR, "ruckus_aggregated_data.json")
    if not os.path.exists(agg_path):
        print(f"ERROR: {agg_path} not found — run main.py first")
        return []

    with open(agg_path) as f:
        agg_data = json.load(f)

    instances = [row["instance"] for row in agg_data if row.get("instance")]

    if force:
        return instances

    need_collection = []
    cached_count = 0
    for inst in instances:
        # Read cache directly (ignore 24h TTL for batch runs)
        cache_file = _cache_path(inst)
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cached = json.load(f)
                ti = extract_table_info(cached)
                if len(ti) > 0:
                    cached_count += 1
                    continue
            except (json.JSONDecodeError, KeyError):
                pass
        need_collection.append(inst)

    print(f"Total instances: {len(instances)}")
    print(f"Cached with data: {cached_count}")
    print(f"Need collection: {len(need_collection)}")
    return need_collection


def main():
    parser = argparse.ArgumentParser(description="Batch collect ruckus data")
    parser.add_argument("--workers", type=int, default=25, help="Parallel workers")
    parser.add_argument("--timeout", type=int, default=120, help="Per-chunk timeout (seconds)")
    parser.add_argument("--instance-timeout", type=int, default=300, help="Per-instance timeout (seconds)")
    parser.add_argument("--force", action="store_true", help="Re-collect all instances")
    args = parser.parse_args()

    instances = find_instances_needing_collection(force=args.force)
    if not instances:
        print("Nothing to collect!")
        return

    os.makedirs(config.CACHE_DIR, exist_ok=True)

    succeeded = 0
    failed = 0
    invalid = 0
    timed_out = 0
    start_time = time.time()

    def _worker(inst):
        return inst, collect_instance_sync(inst, args.timeout, args.instance_timeout)

    print(f"\nCollecting {len(instances)} instances (workers={args.workers}, "
          f"chunk_timeout={args.timeout}s, instance_timeout={args.instance_timeout}s)\n")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_worker, inst): inst for inst in instances}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                inst, data = future.result()
                _save_cache(inst, data, bindings=config.RUCKUS_COMBINED_BINDINGS)

                has_views = len(data.get("views", {})) > 0
                is_invalid = any(
                    cs.get("status", "").startswith("failed (invalid")
                    for cs in data.get("chunk_stats", {}).values()
                )
                is_timeout = any(
                    "timeout" in cs.get("status", "").lower()
                    for cs in data.get("chunk_stats", {}).values()
                )

                if is_invalid:
                    invalid += 1
                    status = "INVALID"
                elif has_views:
                    succeeded += 1
                    status = "OK"
                elif is_timeout:
                    timed_out += 1
                    status = "TIMEOUT"
                else:
                    failed += 1
                    status = "FAIL"

                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                remaining = len(instances) - done
                eta_m = (remaining / rate / 60) if rate > 0 else 0

                print(f"[{done}/{len(instances)}] {status:7s} {inst:40s} "
                      f"views={len(data.get('views',{}))} "
                      f"| OK={succeeded} FAIL={failed} INVALID={invalid} TIMEOUT={timed_out} "
                      f"| {rate:.1f}/s ETA={eta_m:.0f}m", flush=True)

            except Exception as e:
                inst = futures[future]
                failed += 1
                print(f"[{done}/{len(instances)}] ERROR  {inst:40s} {e}", flush=True)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} minutes")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed:    {failed}")
    print(f"  Invalid:   {invalid}")
    print(f"  Timed out: {timed_out}")
    print(f"  Total:     {succeeded + failed + invalid + timed_out}")


if __name__ == "__main__":
    main()
