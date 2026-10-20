"""Load the Texas RRC Production Data Query (PDQ) dump, filtered to one operator
or to the leases with wells in a set of counties.

The PDQ dump (PDQ_DSV.zip, ~3.7 GB) is a monthly full snapshot. Download it from
the RRC "Data Sets Available for Download" page, then point this at the zip (or
at a folder of extracted .dsv files). Format per the RRC PDQ dump user manual:
one `.dsv` per table, `}` delimited, header row, no quoting, space-padded values.

Production is reported per RRC (regulatory) lease, not per well; wells map to
leases through OG_WELL_COMPLETION.
"""
from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DATA_DIR
from .db import connect, init_raw

DELIM = "}"

LEASE_CYCLE_KEEP = [
    "OIL_GAS_CODE", "DISTRICT_NO", "LEASE_NO", "CYCLE_YEAR_MONTH", "OPERATOR_NO",
    "OPERATOR_NAME", "LEASE_NAME", "FIELD_NAME", "LEASE_OIL_PROD_VOL",
    "LEASE_GAS_PROD_VOL", "LEASE_COND_PROD_VOL", "LEASE_CSGD_PROD_VOL",
    "PROD_REPORT_FILED_FLAG",  # Y once the operator files that month; months not yet filed carry N
]
WELL_KEEP = [
    "OIL_GAS_CODE", "DISTRICT_NO", "LEASE_NO", "WELL_NO", "API_COUNTY_CODE",
    "API_UNIQUE_NO", "COUNTY_NAME", "WELL_SHUTIN_DT", "WELLBORE_SHUTIN_DT",
]


class Source:
    """Reads tables from either the zip or a directory of extracted files."""

    def __init__(self, zip_path: Path | None = None, dir_path: Path | None = None):
        if bool(zip_path) == bool(dir_path):
            raise ValueError("Pass exactly one of zip_path or dir_path")
        self.zip = zipfile.ZipFile(zip_path) if zip_path else None
        self.dir = dir_path

    def _find(self, table: str) -> str | Path:
        target = f"{table}_DATA_TABLE.DSV"
        if self.zip:
            for name in self.zip.namelist():
                if name.upper().endswith(target):
                    return name
        else:
            for p in self.dir.rglob("*"):
                if p.name.upper() == target:
                    return p
        raise FileNotFoundError(f"{target} not found in the PDQ source")

    @contextmanager
    def open(self, table: str) -> Iterator[io.TextIOBase]:
        member = self._find(table)
        if self.zip:
            raw = self.zip.open(member)
            f = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        else:
            f = open(member, encoding="latin-1", newline="")
        try:
            yield f
        finally:
            f.close()


def _rows(f: io.TextIOBase) -> Iterator[dict[str, str]]:
    header = [h.strip().upper() for h in f.readline().rstrip("\r\n").split(DELIM)]
    for line in f:
        parts = line.rstrip("\r\n").split(DELIM)
        if len(parts) != len(header):
            continue  # malformed line; the dump has no quoting to recover with
        yield {h: v.strip() for h, v in zip(header, parts)}


def find_operators(src: Source, pattern: str) -> dict[str, str]:
    rx = re.compile(pattern, re.IGNORECASE)
    with src.open("OG_OPERATOR_DW") as f:
        return {r["OPERATOR_NO"]: r["OPERATOR_NAME"] for r in _rows(f)
                if rx.search(r.get("OPERATOR_NAME", ""))}


def leases_in_counties(src: Source, counties: list[str]) -> set[tuple[str, str, str]]:
    """RRC leases with at least one well completed in any of these counties."""
    wanted = {c.strip().upper() for c in counties}
    with src.open("OG_WELL_COMPLETION") as f:
        return {(r["OIL_GAS_CODE"], r["DISTRICT_NO"], r["LEASE_NO"]) for r in _rows(f)
                if r.get("COUNTY_NAME", "").upper() in wanted}


def ingest(src: Source, pattern: str | None = None, since: str = "201801",
           counties: list[str] | None = None) -> dict[str, int]:
    """Load production for one operator (pattern) or for every RRC lease with a
    well in the given counties, whoever operates it."""
    init_raw()
    county_keys: set[tuple[str, str, str]] | None = None
    operators: dict[str, str] = {}
    if counties:
        county_keys = leases_in_counties(src, counties)
        print(f"{len(county_keys):,} RRC leases have wells in {', '.join(counties)}")
        if not county_keys:
            raise SystemExit("No RRC wells found in those counties; check the spelling.")
    else:
        operators = find_operators(src, pattern)
        if not operators:
            raise SystemExit(f"No RRC operator name matches /{pattern}/. Adjust RRC_OPERATOR_PATTERN.")
        print("Matched operators:")
        for no, name in sorted(operators.items()):
            print(f"  {no}  {name}")

    tmp = DATA_DIR / "_rrc_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    lease_csv, well_csv = tmp / "lease_cycle.csv", tmp / "wells.csv"

    started = time.time()
    lease_keys: set[tuple[str, str, str]] = set()
    n_scanned = n_kept = 0
    with src.open("OG_LEASE_CYCLE") as f, open(lease_csv, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(LEASE_CYCLE_KEEP)
        for r in _rows(f):
            n_scanned += 1
            if n_scanned % 5_000_000 == 0:
                print(f"  scanned {n_scanned:,} production rows, kept {n_kept:,}")
            key = (r["OIL_GAS_CODE"], r["DISTRICT_NO"], r["LEASE_NO"])
            keep = key in county_keys if county_keys is not None else r["OPERATOR_NO"] in operators
            if keep and r["CYCLE_YEAR_MONTH"] >= since:
                w.writerow([r.get(c, "") for c in LEASE_CYCLE_KEEP])
                lease_keys.add(key)
                if county_keys is not None:
                    operators[r["OPERATOR_NO"]] = r.get("OPERATOR_NAME", "")
                n_kept += 1

    n_wells = 0
    with src.open("OG_WELL_COMPLETION") as f, open(well_csv, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(WELL_KEEP)
        for r in _rows(f):
            if (r["OIL_GAS_CODE"], r["DISTRICT_NO"], r["LEASE_NO"]) in lease_keys:
                w.writerow([r.get(c, "") for c in WELL_KEEP])
                n_wells += 1

    with connect() as con:
        # The dump is a full snapshot, so each load replaces the previous one.
        con.execute("delete from raw.rrc_operators")
        con.execute("delete from raw.rrc_lease_cycle")
        con.execute("delete from raw.rrc_well_completion")
        con.executemany("insert into raw.rrc_operators values (?, ?, now())", list(operators.items()))
        cols = ", ".join(c.lower() for c in LEASE_CYCLE_KEEP)
        con.execute(f"""insert into raw.rrc_lease_cycle ({cols}, loaded_at)
            select *, now() from read_csv(?, header=true, all_varchar=true)""", [str(lease_csv)])
        cols = ", ".join(c.lower() for c in WELL_KEEP)
        con.execute(f"""insert into raw.rrc_well_completion ({cols}, loaded_at)
            select *, now() from read_csv(?, header=true, all_varchar=true)""", [str(well_csv)])

    lease_csv.unlink()
    well_csv.unlink()
    tmp.rmdir()
    stats = {"rows_scanned": n_scanned, "production_rows": n_kept,
             "rrc_leases": len(lease_keys), "wells": n_wells}
    print(f"Loaded {stats} in {time.time() - started:.0f}s")
    return stats
