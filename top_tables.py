"""Quick report: Top 100 tables by row count across all prod instances."""
import json
import os
import glob
import re

cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "cache")
all_tables = []

for path in glob.glob(os.path.join(cache_dir, "*.json")):
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        continue

    views = data.get("views", {})

    # Get customer name
    inst = views.get("instance", {})
    if isinstance(inst, list) and inst:
        inst = inst[0]
    if not isinstance(inst, dict):
        inst = {}
    customer = inst.get("customer", "")
    instance = inst.get("name", os.path.basename(path).replace(".json", ""))
    used_for = (inst.get("usedFor") or "").lower()

    # Only production
    if used_for and "prod" not in used_for:
        continue

    tables = views.get("tables", [])
    if not isinstance(tables, list):
        continue

    for t in tables:
        if not isinstance(t, dict):
            continue
        rows = t.get("rows", 0) or 0
        data_len = t.get("dataLength", 0) or 0
        idx_len = t.get("indexLength", 0) or 0
        total_bytes = data_len + idx_len
        all_tables.append({
            "customer": customer or instance,
            "instance": instance,
            "table": t.get("name", "?"),
            "rows": rows,
            "size_bytes": total_bytes,
        })

# Deduplicate: same instance + table = keep max rows entry
dedup = {}
for t in all_tables:
    key = (t["instance"], t["table"])
    if key not in dedup or t["rows"] > dedup[key]["rows"]:
        dedup[key] = t
all_tables = list(dedup.values())

total_instances = len(set(t["instance"] for t in all_tables))

# Normalize partitioned table names into their base table
def _normalize_table(name):
    # ts_c_56_4 -> ts_c, ts_index_36_5 -> ts_index
    if re.match(r'^ts_c_', name):
        return 'ts_c'
    if re.match(r'^ts_index_', name):
        return 'ts_index'
    # syslog0005 -> syslog, sys_audit0113 -> sys_audit, etc.
    m = re.match(r'^(.+?)\d{3,}$', name)
    if m:
        return m.group(1)
    return name

for t in all_tables:
    t["table"] = _normalize_table(t["table"])

# Re-dedup after normalization: same instance + table = sum rows
dedup2 = {}
for t in all_tables:
    key = (t["instance"], t["table"])
    if key not in dedup2:
        dedup2[key] = dict(t)
    else:
        dedup2[key]["rows"] += t["rows"]
        dedup2[key]["size_bytes"] += t["size_bytes"]
all_tables = list(dedup2.values())

# Deduplicate by table name globally: keep the instance with the most rows
by_table = {}
for t in all_tables:
    tname = t["table"]
    if tname not in by_table or t["rows"] > by_table[tname]["rows"]:
        by_table[tname] = t
all_tables = list(by_table.values())

# Filter to tables > 5M rows, sort by row count desc
MIN_ROWS = 5_000_000
by_rows = sorted([t for t in all_tables if t["rows"] >= MIN_ROWS], key=lambda x: x["rows"], reverse=True)


def fmt_num(n):
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_size(b):
    if b >= 1_099_511_627_776:
        return f"{b/1_099_511_627_776:.1f} TB"
    if b >= 1_073_741_824:
        return f"{b/1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b/1_048_576:.1f} MB"
    return f"{b/1024:.0f} KB"


print(f"{len(by_rows)} unique tables with >5M rows across {total_instances:,} production instances")
print()
header = f"{'#':<4} {'Customer':<35} {'Instance':<25} {'Table Name':<40} {'Row Count':>15}"
print(header)
print("-" * len(header))
for i, t in enumerate(by_rows, 1):
    cust = t["customer"][:34]
    inst = t["instance"][:24]
    tbl = t["table"][:39]
    print(f"{i:<4} {cust:<35} {inst:<25} {tbl:<40} {fmt_num(t['rows']):>15}")
