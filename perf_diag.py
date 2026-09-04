import os, json

f = "output/ruckus_dashboard.html"
size = os.path.getsize(f)
print(f"Dashboard size: {size / 1024 / 1024:.1f} MB")

with open(f) as fh:
    content = fh.read()

# Find ALL_ROWS JSON
ar_idx = content.find("const ALL_ROWS = ")
td_idx = content.find(";\nconst TIER_DATA", ar_idx)
json_str = content[ar_idx + len("const ALL_ROWS = "):td_idx]
print(f"ALL_ROWS JSON: {len(json_str) / 1024 / 1024:.1f} MB")

rows = json.loads(json_str)
print(f"Row count: {len(rows)}")

# Check per-row size
sizes = [len(json.dumps(r)) for r in rows[:100]]
avg_size = sum(sizes) / len(sizes)
print(f"Avg row JSON size (sample 100): {avg_size:.0f} bytes")

# What keys are biggest?
sample = rows[0]
for k, v in sorted(sample.items(), key=lambda x: len(json.dumps(x[1])), reverse=True)[:10]:
    print(f"  {k}: {len(json.dumps(v))} bytes, type={type(v).__name__}")

# How many rows have top_tables?
with_tables = sum(1 for r in rows if r.get("top_tables"))
total_table_entries = sum(len(r.get("top_tables", [])) for r in rows)
table_json_size = sum(len(json.dumps(r.get("top_tables", []))) for r in rows)
print(f"\nRows with top_tables: {with_tables}")
print(f"Total top_table entries: {total_table_entries}")
print(f"top_tables JSON size: {table_json_size / 1024 / 1024:.1f} MB")

# Other large fields
nodes_size = sum(len(json.dumps(r.get("nodes", []))) for r in rows)
errors_size = sum(len(json.dumps(r.get("collection_errors", []))) for r in rows)
print(f"nodes JSON size: {nodes_size / 1024 / 1024:.1f} MB")
print(f"collection_errors JSON size: {errors_size / 1024 / 1024:.1f} MB")
