import json

with open("/tmp/ruckus_tables.json") as f:
    raw = f.read()

start = raw.find("{")
d = json.loads(raw[start:])
data = d.get("Data", {})

for k, v in data.items():
    print(f"{k}: type={type(v).__name__}, len={len(v) if isinstance(v, list) else 'N/A'}")
    if isinstance(v, list) and v:
        print(f"  First item keys: {list(v[0].keys()) if isinstance(v[0], dict) else v[0]}")
        print(f"  First item: {json.dumps(v[0], indent=2)[:300]}")

# Check the raw GraphQL structure for pagination hints
raw_d = d
for key in ["extensions", "metadata", "pageInfo", "pagination"]:
    if key in raw_d:
        print(f"\nFound '{key}': {raw_d[key]}")

print(f"\nTop-level keys: {list(d.keys())}")
print(f"Errors: {len(d.get('errors', []))}")

# Check first error for table count hints
errors = d.get("errors", [])
if errors:
    e = errors[0]
    msg = e.get("message", "")[:200]
    path = e.get("path", [])
    print(f"First error path: {path}")
    print(f"First error msg: {msg}")
