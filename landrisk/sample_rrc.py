"""Write a small synthetic RRC PDQ dump (same files, columns and `}` delimiter as
the real PDQ_DSV.zip) so the whole pipeline runs offline.

The operator is fictional and clearly labelled SYNTHETIC. For the real demo,
download the PDQ dump and run `python cli.py ingest-rrc --zip <path>`.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from .config import SAMPLE_PDQ_DIR

DELIM = "}"
SAMPLE_OPERATOR_NO = "999001"
SAMPLE_OPERATOR_NAME = "SAMPLE OPERATING CO (SYNTHETIC)"
OTHER_OPERATOR_NO = "999002"
OTHER_OPERATOR_NAME = "OTHER SAMPLE OPERATOR (SYNTHETIC)"
LATEST_CYCLE = (2026, 7)  # RRC data typically lags 2-3 months
FIRST_CYCLE = (2019, 1)

LEASE_CYCLE_COLS = [
    "OIL_GAS_CODE", "DISTRICT_NO", "LEASE_NO", "CYCLE_YEAR", "CYCLE_MONTH",
    "CYCLE_YEAR_MONTH", "LEASE_NO_DISTRICT_NO", "OPERATOR_NO", "FIELD_NO",
    "FIELD_TYPE", "GAS_WELL_NO", "PROD_REPORT_FILED_FLAG", "LEASE_OIL_PROD_VOL",
    "LEASE_OIL_ALLOW", "LEASE_OIL_ENDING_BAL", "LEASE_GAS_PROD_VOL",
    "LEASE_GAS_ALLOW", "LEASE_GAS_LIFT_INJ_VOL", "LEASE_COND_PROD_VOL",
    "LEASE_COND_LIMIT", "LEASE_COND_ENDING_BAL", "LEASE_CSGD_PROD_VOL",
    "LEASE_CSGD_LIMIT", "LEASE_CSGD_GAS_LIFT", "LEASE_OIL_TOT_DISP",
    "LEASE_GAS_TOT_DISP", "LEASE_COND_TOT_DISP", "LEASE_CSGD_TOT_DISP",
    "DISTRICT_NAME", "LEASE_NAME", "OPERATOR_NAME", "FIELD_NAME",
]
OPERATOR_COLS = [
    "OPERATOR_NO", "OPERATOR_NAME", "P5_STATUS_CODE", "P5_LAST_FILED_DT",
    "OPERATOR_TAX_CERT_FLAG", "OPERATOR_SB639_FLAG", "FA_OPTION_CODE",
    "RECORD_STATUS_CODE", "EFILE_STATUS_CODE", "EFILE_EFFECTIVE_DT",
    "CREATE_BY", "CREATE_DT", "MODIFY_BY", "MODIFY_DT",
]
WELL_COLS = [
    "OIL_GAS_CODE", "DISTRICT_NO", "LEASE_NO", "WELL_NO", "API_COUNTY_CODE",
    "API_UNIQUE_NO", "ONSHORE_ASSC_CNTY", "DISTRICT_NAME", "COUNTY_NAME",
    "OIL_WELL_UNIT_NO", "WELL_ROOT_NO", "WELLBORE_SHUTIN_DT", "WELL_SHUTIN_DT",
    "WELL_14B2_STATUS_CODE", "WELL_SUBJECT_14B2_FLAG", "WELLBORE_LOCATION_CODE",
]

COUNTIES = [("REEVES", "389"), ("LOVING", "301"), ("WARD", "475"),
            ("CULBERSON", "109"), ("MIDLAND", "329"), ("HOWARD", "227")]

# (pattern, count): how many trailing months of zero production each lease has
PATTERNS = [("active", 15), ("gap_2", 2), ("gap_4", 3), ("gap_8", 3), ("gap_14", 1)]


def _months():
    y, m = FIRST_CYCLE
    while (y, m) <= LATEST_CYCLE:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _pad(value: str, width: int) -> str:
    # Real PDQ values are space padded; mimic that so the ingest trims correctly.
    return value.ljust(width)


def write_sample(out_dir: Path = SAMPLE_PDQ_DIR, seed: int = 7) -> Path:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    months = list(_months())

    leases = []
    n = 0
    for pattern, count in PATTERNS:
        for _ in range(count):
            n += 1
            county, code = COUNTIES[n % len(COUNTIES)]
            leases.append({
                "lease_no": f"{40000 + n * 37:06d}",
                "district_no": "10",  # dump code 10 = RRC District 08 (Permian)
                "name": f"SAMPLE UNIT {n:02d} (SYNTHETIC)",
                "county": county, "county_code": code,
                "pattern": pattern,
                "first_prod": rng.randint(0, 30),  # months after FIRST_CYCLE
                "ip": rng.uniform(9000, 26000),
                "wells": rng.randint(1, 4),
            })

    with open(out_dir / "OG_OPERATOR_DW_DATA_TABLE.dsv", "w") as f:
        f.write(DELIM.join(OPERATOR_COLS) + "\n")
        for no, name in [(SAMPLE_OPERATOR_NO, SAMPLE_OPERATOR_NAME), (OTHER_OPERATOR_NO, OTHER_OPERATOR_NAME)]:
            row = [no, _pad(name, 50), "A", "20260101"] + [""] * (len(OPERATOR_COLS) - 4)
            f.write(DELIM.join(row) + "\n")

    with open(out_dir / "OG_LEASE_CYCLE_DATA_TABLE.dsv", "w") as f:
        f.write(DELIM.join(LEASE_CYCLE_COLS) + "\n")
        for lease in leases:
            gap = 0 if lease["pattern"] == "active" else int(lease["pattern"].split("_")[1])
            for i, (y, m) in enumerate(months):
                age = i - lease["first_prod"]
                if age < 0:
                    continue
                if i >= len(months) - gap:
                    oil = gas = 0
                else:
                    # hyperbolic-ish decline with noise
                    oil = lease["ip"] / (1 + 0.08 * age) ** 1.4 * rng.uniform(0.85, 1.1)
                    gas = oil * rng.uniform(2.5, 4.0)
                row = {c: "" for c in LEASE_CYCLE_COLS}
                row.update({
                    "OIL_GAS_CODE": "O", "DISTRICT_NO": lease["district_no"],
                    "LEASE_NO": lease["lease_no"], "CYCLE_YEAR": str(y),
                    "CYCLE_MONTH": f"{m:02d}", "CYCLE_YEAR_MONTH": f"{y}{m:02d}",
                    "LEASE_NO_DISTRICT_NO": lease["lease_no"] + lease["district_no"],
                    "OPERATOR_NO": SAMPLE_OPERATOR_NO, "FIELD_NO": "60000100",
                    "PROD_REPORT_FILED_FLAG": "Y",
                    "LEASE_OIL_PROD_VOL": str(int(oil)),
                    "LEASE_CSGD_PROD_VOL": str(int(gas)),
                    "LEASE_GAS_PROD_VOL": "0", "LEASE_COND_PROD_VOL": "0",
                    "DISTRICT_NAME": "08", "LEASE_NAME": _pad(lease["name"], 50),
                    "OPERATOR_NAME": _pad(SAMPLE_OPERATOR_NAME, 50),
                    "FIELD_NAME": _pad("PHANTOM (WOLFCAMP)", 32),
                })
                f.write(DELIM.join(row[c] for c in LEASE_CYCLE_COLS) + "\n")
        # Rows for another operator that the ingest filter must drop
        for y, m in months[-6:]:
            row = {c: "" for c in LEASE_CYCLE_COLS}
            row.update({"OIL_GAS_CODE": "O", "DISTRICT_NO": "10", "LEASE_NO": "099999",
                        "CYCLE_YEAR_MONTH": f"{y}{m:02d}", "OPERATOR_NO": OTHER_OPERATOR_NO,
                        "LEASE_OIL_PROD_VOL": "500", "OPERATOR_NAME": OTHER_OPERATOR_NAME})
            f.write(DELIM.join(row[c] for c in LEASE_CYCLE_COLS) + "\n")

    with open(out_dir / "OG_WELL_COMPLETION_DATA_TABLE.dsv", "w") as f:
        f.write(DELIM.join(WELL_COLS) + "\n")
        uid = 30000
        for lease in leases:
            for w in range(lease["wells"]):
                uid += rng.randint(3, 40)
                row = {c: "" for c in WELL_COLS}
                row.update({
                    "OIL_GAS_CODE": "O", "DISTRICT_NO": lease["district_no"],
                    "LEASE_NO": lease["lease_no"], "WELL_NO": f"{w + 1}H",
                    "API_COUNTY_CODE": lease["county_code"], "API_UNIQUE_NO": f"{uid:05d}",
                    "DISTRICT_NAME": "08", "COUNTY_NAME": lease["county"],
                })
                f.write(DELIM.join(row[c] for c in WELL_COLS) + "\n")

    return out_dir


if __name__ == "__main__":
    print(write_sample())
