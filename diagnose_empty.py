"""Check what views the 'cache + views but 0 tables' instances actually have."""
import json, os, config
from scoring import get_ranked_instances

ranked = get_ranked_instances(top_n=config.DEFAULT_TOP_N, csv_only=True)

# Check a few "cache + views but 0 tables" instances
samples = ["now", "grupordprod", "smithnephew", "bmwidsprod", "amazonots"]
for inst in samples:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    if not os.path.exists(cp):
        print(f"{inst}: NO CACHE")
        continue
    with open(cp) as f:
        d = json.load(f)
    views = d.get("views", {})
    print(f"\n{inst}: {len(views)} views => {list(views.keys())[:10]}")
    # Check if any view has table-like data
    for vname, vdata in list(views.items())[:3]:
        if isinstance(vdata, list):
            print(f"  {vname}: list of {len(vdata)} items")
            if vdata:
                print(f"    sample keys: {list(vdata[0].keys())[:8] if isinstance(vdata[0], dict) else type(vdata[0])}")
        elif isinstance(vdata, dict):
            print(f"  {vname}: dict with keys {list(vdata.keys())[:8]}")
        else:
            print(f"  {vname}: {type(vdata)}")

# Also check a "no views" sample
print("\n--- No-views samples ---")
for inst in ["novartiscorp", "atyourserviceportal"]:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    if not os.path.exists(cp):
        print(f"{inst}: NO CACHE")
        continue
    with open(cp) as f:
        d = json.load(f)
    print(f"{inst}: top keys={list(d.keys())[:10]}, views={list(d.get('views',{}).keys())[:5]}")
