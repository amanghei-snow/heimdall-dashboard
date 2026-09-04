"""Check cache age for empty instances to see if they'll be re-collected."""
import json, os, time, config

cache_dir = config.CACHE_DIR
now = time.time()
stale = 0
fresh = 0
no_tables_total = 0

for fn in os.listdir(cache_dir):
    if not fn.endswith(".json"):
        continue
    fp = os.path.join(cache_dir, fn)
    try:
        with open(fp) as f:
            d = json.load(f)
        views = d.get("views", {})
        tables = views.get("tables", [])
        if len(tables) == 0:
            no_tables_total += 1
            age_h = (now - d.get("_ts", 0)) / 3600
            if age_h > 24:
                stale += 1
            else:
                fresh += 1
    except Exception:
        pass

print(f"Cache files with 0 tables: {no_tables_total}")
print(f"  Stale (>24h, will re-collect): {stale}")
print(f"  Fresh (<24h, will be skipped): {fresh}")
