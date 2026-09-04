import json

with open("/tmp/ruckus_now_i.json") as f:
    raw = f.read()

start = raw.find("{")
d = json.loads(raw[start:])
data = d.get("Data", {})
for k, v in data.items():
    if isinstance(v, list) and v:
        print(f"{k}: {len(v)} items")
        if isinstance(v[0], dict):
            for ik, iv in v[0].items():
                s = str(iv)[:80]
                print(f"  {ik}: {s}")
    elif isinstance(v, dict):
        print(f"{k}: dict")
        for ik, iv in v.items():
            s = str(iv)[:80]
            print(f"  {ik}: {s}")
