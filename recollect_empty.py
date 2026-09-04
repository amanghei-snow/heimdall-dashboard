"""
Delete stale/empty cache files so they get re-collected on next run.
Targets instances that have views but 0 tables.
"""
import json, os, time, config
from collector import extract_table_info

cache_dir = config.CACHE_DIR
current_bindings = config.RUCKUS_COMBINED_BINDINGS
deleted = 0
skipped = 0
already_ok = 0

for fn in sorted(os.listdir(cache_dir)):
    if not fn.endswith(".json"):
        continue
    fp = os.path.join(cache_dir, fn)
    try:
        with open(fp) as f:
            d = json.load(f)
    except Exception:
        continue

    views = d.get("views", {})
    ti = extract_table_info(d)

    # Skip instances that already have table data
    if len(ti) > 0:
        already_ok += 1
        continue

    # Skip instances with no views at all (never collected successfully)
    if not views:
        skipped += 1
        continue

    # This instance has views but 0 tables - delete cache to force re-collection
    os.remove(fp)
    deleted += 1

print(f"Deleted {deleted} stale cache files (had views but 0 tables)")
print(f"Skipped {skipped} (no views at all)")
print(f"Already OK: {already_ok}")
print(f"These will be re-collected on next collection run.")
