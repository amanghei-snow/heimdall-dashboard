"""Identify instances with no table/row data and diagnose why."""
import json, os, config
from scoring import get_ranked_instances
from collector import extract_table_info

ranked = get_ranked_instances(top_n=config.DEFAULT_TOP_N, csv_only=True)

no_cache = []
cache_no_views = []
cache_empty_tables = []
ok = []

for inst, score, cov, dims in ranked:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    if not os.path.exists(cp):
        no_cache.append(inst)
        continue
    try:
        with open(cp) as f:
            d = json.load(f)
        views = d.get("views", {})
        if not views:
            cache_no_views.append(inst)
        else:
            ti = extract_table_info(d)
            total_rows = sum(t.get("rows", 0) or 0 for t in ti)
            if len(ti) == 0 and total_rows == 0:
                cache_empty_tables.append(inst)
            else:
                ok.append(inst)
    except Exception:
        no_cache.append(inst)

print(f"Total ranked: {len(ranked)}")
print(f"OK (have table data): {len(ok)}")
print(f"No cache file: {len(no_cache)}")
print(f"Cache but no views: {len(cache_no_views)}")
print(f"Cache + views but 0 tables: {len(cache_empty_tables)}")
print(f"Need collection: {len(no_cache) + len(cache_no_views) + len(cache_empty_tables)}")
print("---")
print(f"Sample no-cache: {no_cache[:5]}")
print(f"Sample no-views: {cache_no_views[:5]}")
print(f"Sample empty-tables: {cache_empty_tables[:5]}")
