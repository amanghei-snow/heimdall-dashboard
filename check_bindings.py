"""Check bindings and errors for empty-table instances."""
import json, os, config

for inst in ["now", "grupordprod", "smithnephew"]:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    with open(cp) as f:
        d = json.load(f)
    print(f"=== {inst} ===")
    print(f"  bindings: {d.get('_bindings', '?')}")
    print(f"  views: {list(d.get('views', {}).keys())}")
    print(f"  errors: {d.get('errors', [])}")
    cs = d.get("chunk_stats", [])
    if cs:
        for c in cs:
            if isinstance(c, dict):
                print(f"  chunk: rc={c.get('rc')}, ok={c.get('ok_views')}, fail={c.get('failed_views')}")
            else:
                print(f"  chunk_stat item: {c}")
    # Check tables view content
    tables = d.get("views", {}).get("tables", [])
    print(f"  tables view: type={type(tables).__name__}, len={len(tables) if isinstance(tables, list) else 'N/A'}")
    print()
