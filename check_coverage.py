"""Check why only ~4,821 instances have table data."""
import json
import os
import glob

cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "cache")
total = 0
has_tables = 0
is_prod = 0
no_tables = 0
non_prod = 0

for path in glob.glob(os.path.join(cache_dir, "*.json")):
    total += 1
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        continue
    views = data.get("views", {})
    inst = views.get("instance", {})
    if isinstance(inst, list) and inst:
        inst = inst[0]
    if not isinstance(inst, dict):
        inst = {}
    used_for = (inst.get("usedFor") or "").lower()
    tables = views.get("tables", [])
    has_tbl = isinstance(tables, list) and len(tables) > 0

    if not has_tbl:
        no_tables += 1
        continue
    has_tables += 1

    if used_for and "prod" not in used_for:
        non_prod += 1
        continue
    is_prod += 1

print(f"Total cache files:        {total:,}")
print(f"No table data:            {no_tables:,}")
print(f"Has table data:           {has_tables:,}")
print(f"Filtered out (non-prod):  {non_prod:,}")
print(f"Has tables + is prod:     {is_prod:,}")
