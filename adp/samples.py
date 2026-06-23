"""Deterministic synthetic sample data.

Three heterogeneous sources keyed by (county, quarter) — mirroring an economic
panel built from flood-insurance, property-transaction and mortgage data. Fixed
seed => reproducible, so the demo, tests and eval are byte-stable and run with no
network access.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

_COUNTIES = [
    "Miami-Dade", "Broward", "Palm Beach", "Hillsborough", "Orange",
    "Duval", "Pinellas", "Lee", "Polk", "Brevard",
]
_QUARTERS = [f"{y}Q{q}" for y in range(2018, 2023) for q in range(1, 5)]  # 2018Q1..2022Q4


def generate_samples(out_dir: str | Path, seed: int = 42) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    fema, prop, hmda = [], [], []
    for c in _COUNTIES:
        base = rng.uniform(0.8, 1.6)  # per-county scale factor
        for i, qtr in enumerate(_QUARTERS):
            trend = 1.0 + 0.012 * i
            fema.append({
                "county": c, "quarter": qtr,
                "policies_count": int(2000 * base * trend * rng.uniform(0.9, 1.1)),
                "total_premium": round(2_500_000 * base * trend * rng.uniform(0.9, 1.1), 2),
            })
            prop.append({
                "county": c, "quarter": qtr,
                "median_sale_price": round(320_000 * base * trend * rng.uniform(0.92, 1.08), 2),
                "sales_count": int(900 * base * rng.uniform(0.85, 1.15)),
            })
            hmda.append({
                "county": c, "quarter": qtr,
                "loan_count": int(1500 * base * trend * rng.uniform(0.88, 1.12)),
                "total_loan_amount": round(480_000_000 * base * trend * rng.uniform(0.9, 1.1), 2),
            })

    paths = {
        "fema_policies": out / "fema_policies.csv",
        "property_sales": out / "property_sales.csv",
        "hmda_loans": out / "hmda_loans.csv",
    }
    for rows, path in ((fema, paths["fema_policies"]), (prop, paths["property_sales"]), (hmda, paths["hmda_loans"])):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    return {name: str(p) for name, p in paths.items()}
