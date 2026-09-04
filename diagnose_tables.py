"""Check what the tables view looks like for instances where extract_table_info returns empty."""
import json, os, config
from collector import extract_table_info

samples = ["now", "grupordprod", "smithnephew", "bmwidsprod", "amazonots"]
for inst in samples:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    with open(cp) as f:
        d = json.load(f)

    # What extract_table_info returns
    ti = extract_table_info(d)
    print(f"\n=== {inst} ===")
    print(f"extract_table_info returned: {len(ti)} tables")

    # What the raw tables view looks like
    tables_view = d.get("views", {}).get("tables", [])
    print(f"Raw tables view: type={type(tables_view).__name__}, len={len(tables_view) if isinstance(tables_view, list) else 'N/A'}")
    if isinstance(tables_view, list) and tables_view:
        sample = tables_view[0]
        print(f"  First item keys: {list(sample.keys()) if isinstance(sample, dict) else type(sample)}")
        if isinstance(sample, dict):
            for k, v in list(sample.items())[:6]:
                print(f"    {k}: {v}")
    elif isinstance(tables_view, dict):
        print(f"  Dict keys: {list(tables_view.keys())[:10]}")
