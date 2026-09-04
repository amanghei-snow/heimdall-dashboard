"""Delete cache files that have no views at all."""
import json, os, config

cache_dir = config.CACHE_DIR
deleted = 0

for fn in sorted(os.listdir(cache_dir)):
    if not fn.endswith(".json"):
        continue
    fp = os.path.join(cache_dir, fn)
    try:
        with open(fp) as f:
            d = json.load(f)
    except Exception:
        os.remove(fp)
        deleted += 1
        continue

    views = d.get("views", {})
    if not views:
        os.remove(fp)
        deleted += 1

print(f"Deleted {deleted} cache files with no views")
