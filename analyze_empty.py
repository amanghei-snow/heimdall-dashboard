"""Analyze what CSV data the empty-table instances have."""
import json, os, config
from scoring import get_ranked_instances, load_prod_transactions
from collector import extract_table_info, extract_db_info

ranked = get_ranked_instances(top_n=config.DEFAULT_TOP_N, csv_only=True)
csv_data = load_prod_transactions()

empty_with_db = 0
empty_no_db = 0
empty_with_score = 0
has_db_info = 0
total_empty = 0

for inst, score, cov, dims in ranked:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    if os.path.exists(cp):
        with open(cp) as f:
            d = json.load(f)
        ti = extract_table_info(d)
        if len(ti) > 0:
            continue
        views = d.get("views", {})
        if not views:
            continue
        # This is a "cache+views but 0 tables" instance
        total_empty += 1
        csv_d = csv_data.get(inst, {})
        db_gb = csv_d.get("db_gb", 0)
        if db_gb > 0:
            empty_with_db += 1
        else:
            empty_no_db += 1
        if score > 0:
            empty_with_score += 1
        # Check if ruckus returned db info
        dbi = extract_db_info(d)
        if dbi["total_size_gb"] > 0:
            has_db_info += 1

    elif inst not in csv_data:
        continue

print(f"Instances with views but 0 tables: {total_empty}")
print(f"  Have CSV db_gb > 0: {empty_with_db}")
print(f"  CSV db_gb = 0: {empty_no_db}")
print(f"  Have score > 0: {empty_with_score}")
print(f"  Have ruckus DB info: {has_db_info}")
print()

# Show a few examples with their CSV data
print("--- Top 10 empty instances by CSV db_gb ---")
empties = []
for inst, score, cov, dims in ranked:
    cp = os.path.join(config.CACHE_DIR, f"{inst}.json")
    if not os.path.exists(cp):
        continue
    with open(cp) as f:
        d = json.load(f)
    if not d.get("views", {}):
        continue
    ti = extract_table_info(d)
    if len(ti) > 0:
        continue
    csv_d = csv_data.get(inst, {})
    empties.append((inst, csv_d.get("db_gb", 0), csv_d.get("txn_90d", 0), score))

empties.sort(key=lambda x: x[1], reverse=True)
for inst, db_gb, txn, sc in empties[:10]:
    print(f"  {inst:30s} db_gb={db_gb:>12,.1f}  txn={txn:>12,.0f}  score={sc:.4f}")
