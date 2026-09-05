"""Backfill instance.company from cmdb_ci_service xlsx files."""
import os
import re
from collections import defaultdict
from openpyxl import load_workbook
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Map multiple xlsx sources
XLSX_PATHS = [
    "/Users/aman.ghei/Downloads/cmdb_ci_service.xlsx",
    "/Users/aman.ghei/Downloads/cmdb_ci_service (1).xlsx",
    "/Users/aman.ghei/Downloads/cmdb_ci_service (2).xlsx",
    "/Users/aman.ghei/Downloads/cmdb_ci_service (3).xlsx",
    "/Users/aman.ghei/Downloads/cmdb_ci_service (4).xlsx",
]


def build_mapping():
    mapping = {}
    for path in XLSX_PATHS:
        if not os.path.exists(path):
            continue
        try:
            wb = load_workbook(path, data_only=True)
        except Exception as e:
            print(f"Skipping {path}: {e}")
            continue
        ws = wb.active
        if ws.max_row < 2:
            continue
        headers = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1) if ws.cell(row=1, column=c).value}
        name_col = headers.get("Name") or headers.get("name")
        company_col = headers.get("Company") or headers.get("company")
        suffix_col = headers.get("Suffix (calc)") or headers.get("Suffix")
        if not name_col:
            continue
        for row in ws.iter_rows(min_row=2, values_only=True):
            name = row[name_col - 1]
            company = row[company_col - 1] if company_col else None
            suffix = row[suffix_col - 1] if suffix_col else None
            if not name:
                continue
            inst = suffix
            if not inst and isinstance(name, str):
                if name.lower().startswith("snc instance - "):
                    inst = name[len("SNC Instance - "):]
                else:
                    inst = name
            if not inst:
                continue
            inst = str(inst).strip().lower()
            if company and str(company).strip() and inst not in mapping:
                mapping[inst] = str(company).strip()
    return mapping


def backfill(engine, mapping):
    e = create_engine(engine)
    updated = 0
    with e.connect() as c:
        # Update in batches to avoid huge parameter lists
        items = list(mapping.items())
        batch_size = 1000
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            # SQLAlchemy text supports individual execute for simplicity
            for inst, company in batch:
                c.execute(
                    text("UPDATE instances SET company = :company WHERE instance = :inst AND (company IS NULL OR company = '' OR company = 'Unknown')"),
                    {"inst": inst, "company": company},
                )
            c.commit()
            updated += len(batch)
            print(f"  processed {min(updated, len(items))} / {len(items)}")
    print(f"Done: {len(mapping)} mappings processed")


if __name__ == "__main__":
    mapping = build_mapping()
    print(f"Built mapping with {len(mapping)} instance->company entries")

    local_url = os.getenv("DATABASE_URL", "mysql+pymysql://ruckus:ruckus@localhost:3306/ruckus_aggregator")
    print("\nBackfilling local...")
    backfill(local_url, mapping)

    render_url = os.getenv(
        "RENDER_DATABASE_URL",
        "postgresql://heimdall:F8PKD2ASPB2PDsgvgfa9oplTXUGgLmW4@dpg-dad12p710e5c73cs6qgg-a.oregon-postgres.render.com/heimdall_p8ps",
    )
    print("\nBackfilling Render...")
    backfill(render_url, mapping)
