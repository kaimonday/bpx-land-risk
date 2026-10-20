"""Link recorded leases to the RRC leases that report their production.

A recorded lease (a contract over land) and an RRC lease (a regulatory unit
that reports production) are different records with no shared key. RRC
leases are usually named after the landowner, so candidates are scored on:
  - the RRC lease name against the lessor's name (most weight)
  - the RRC operator against the lessee (operators change, so less weight)
  - production that starts, or continues, after the lease's effective date
within the lease's county. `suggest` writes the top candidates per lease to a
CSV; a person marks the right one (or none) in its `confirm` column and
`apply_links` loads those as raw.tract_rrc_xref rows keyed by document id.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .config import DATA_DIR
from .db import connect, init_raw

CANDIDATES = DATA_DIR / "real" / "edgar" / "rrc_candidates.csv"

# Words that say what kind of party or unit something is, not who it is.
STOP = {
    "LLC", "INC", "CO", "CORP", "CORPORATION", "COMPANY", "LP", "LTD", "LLP", "PARTNERS", "PARTNERSHIP",
    "THE", "AND", "OF", "ET", "AL", "UX", "VIR", "JR", "SR", "II", "III", "IV", "TRUST", "TRUSTEE",
    "ESTATE", "FAMILY", "FARMS", "RANCH", "UNIT", "LEASE", "GAS", "OIL", "OPERATING", "ENERGY",
    "PETROLEUM", "RESOURCES", "PROPERTIES", "HUSBAND", "WIFE", "DECEASED", "XXX",
}


def tokens(name: str | None) -> set[str]:
    return {t for t in re.findall(r"[A-Z0-9]+", (name or "").upper()) if len(t) > 1 and t not in STOP}


COMPANY_WORDS = {"LLC", "INC", "CO", "CORP", "CORPORATION", "COMPANY", "LP", "LTD", "LLP", "PARTNERS"}


def _surnames(lessor: str) -> set[str]:
    """Surname of each person in "Dorothy Hinds; Frank Hinds" or "James V. Garcia
    and Eddie B. Garcia": the last real word of each name."""
    out = set()
    for person in re.split(r";|&|,|\band\b", lessor, flags=re.I):
        words = [w for w in re.findall(r"[A-Z0-9]+", person.upper()) if len(w) > 1 and w not in STOP]
        if words:
            out.add(words[-1])
    return out


def _rrc_surname_words(lease_name: str) -> set[str]:
    """RRC names people surname first: "HINDS, F. C." -> {HINDS}. Without a
    comma ("JANSSEN UNIT A", "NOACK FARMS") every distinctive word counts."""
    head = lease_name.split(",", 1)[0] if "," in lease_name else lease_name
    return tokens(head)


def name_score(a: str | None, b: str | None) -> float:
    """How well an RRC lease name (b) matches a lessor (a).

    For people, only surnames count: the lessor's last name against the RRC
    name's surname, so "Frank Hinds" matches "HINDS, F. C." but not "FRANK".
    For companies, the share of the shorter name's distinctive words found in
    the other. Falls back to all words when a name is nothing but entity words."""
    words_a = set(re.findall(r"[A-Z0-9]+", (a or "").upper()))
    if a and b and not (words_a & COMPANY_WORDS):
        surnames = _surnames(a)
        if surnames:
            return 1.0 if surnames & _rrc_surname_words(b) else 0.0
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        wa = set(re.findall(r"[A-Z0-9]+", (a or "").upper())) - {"LLC", "INC", "LP", "THE", "AND"}
        wb = set(re.findall(r"[A-Z0-9]+", (b or "").upper())) - {"LLC", "INC", "LP", "THE", "AND"}
        ta, tb = (ta or wa), (tb or wb)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def score_candidate(lease: dict, cand: dict) -> tuple[float, str]:
    """(score 0-1, short reason) for one RRC lease as the source of a lease's production."""
    lessor = name_score(lease.get("lessor"), cand.get("lease_name"))
    lessee = name_score(lease.get("lessee"), cand.get("operator_name"))
    eff = (lease.get("effective_date") or "").replace("-", "")[:6]
    first, last = cand.get("first_prod_ym") or "", cand.get("last_prod_ym") or ""
    timing, timing_note = 0.0, ""
    if eff and first and first >= eff:
        timing, timing_note = 0.2, "production starts after the lease date"
    elif eff and last and last >= eff:
        timing, timing_note = 0.1, "production continues after the lease date"
    reasons = [f"lease name {lessor:.0%} like lessor", f"operator {lessee:.0%} like lessee"]
    if timing_note:
        reasons.append(timing_note)
    return round(0.5 * lessor + 0.3 * lessee + timing, 3), "; ".join(reasons)


def _leases(con) -> list[dict]:
    rows = con.execute("""
        select document_id,
               max(case when field_name = 'lessor' then final_value end) as lessor,
               max(case when field_name = 'lessee' then final_value end) as lessee,
               max(case when field_name = 'county' then final_value end) as county,
               max(case when field_name = 'effective_date' then final_value end) as effective_date
        from main.int_lease_fields_resolved group by 1 order by 1""").fetchall()
    return [dict(zip(["document_id", "lessor", "lessee", "county", "effective_date"], r)) for r in rows]


def _rrc_leases(con) -> list[dict]:
    rows = con.execute("""
        with wells as (
            select oil_gas_code, district_no, lease_no, upper(trim(any_value(county_name))) as county
            from raw.rrc_well_completion group by 1, 2, 3
        ), prod as (
            select oil_gas_code, district_no, lease_no,
                   arg_max(trim(lease_name), cycle_year_month) as lease_name,
                   arg_max(trim(operator_name), cycle_year_month) as operator_name,
                   min(cycle_year_month) filter (where coalesce(try_cast(lease_oil_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_gas_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_cond_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0) > 0) as first_prod_ym,
                   max(cycle_year_month) filter (where coalesce(try_cast(lease_oil_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_gas_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_cond_prod_vol as bigint), 0)
                       + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0) > 0) as last_prod_ym
            from raw.rrc_lease_cycle group by 1, 2, 3
        )
        select p.*, w.county from prod p join wells w using (oil_gas_code, district_no, lease_no)""").fetchall()
    cols = ["oil_gas_code", "district_no", "lease_no", "lease_name", "operator_name",
            "first_prod_ym", "last_prod_ym", "county"]
    return [dict(zip(cols, r)) for r in rows]


def suggest(top: int = 5, out: Path = CANDIDATES) -> int:
    with connect(must_exist=True) as con:
        leases, rrc = _leases(con), _rrc_leases(con)
    if not rrc:
        raise SystemExit("No RRC production loaded. Run ingest-rrc with --county first.")
    by_county: dict[str, list[dict]] = {}
    for c in rrc:
        by_county.setdefault(c["county"] or "", []).append(c)

    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["document_id", "lessor", "lessee", "county", "effective_date", "rank", "score", "rrc_lease",
            "rrc_lease_name", "rrc_operator", "first_production", "last_production", "why", "confirm"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        for lease in leases:
            pool = by_county.get((lease["county"] or "").upper(), [])
            scored = sorted(((score_candidate(lease, c), c) for c in pool), key=lambda x: -x[0][0])[:top]
            if not scored:
                w.writerow({**{k: lease.get(k) for k in cols if k in lease}, "rank": "",
                            "why": f"no RRC leases loaded for county {lease['county']!r}"})
            for rank, ((score, why), c) in enumerate(scored, start=1):
                w.writerow({**{k: lease.get(k) for k in cols if k in lease}, "rank": rank, "score": score,
                            "rrc_lease": f"{c['oil_gas_code']}-{c['district_no']}-{c['lease_no']}",
                            "rrc_lease_name": c["lease_name"], "rrc_operator": c["operator_name"],
                            "first_production": c["first_prod_ym"], "last_production": c["last_prod_ym"],
                            "why": why, "confirm": ""})
            best = scored[0][0][0] if scored else 0
            print(f"  {lease['document_id']} {lease['county'] or '?':12} best {best:.2f}  "
                  f"{scored[0][1]['lease_name'] if scored else '(none)'}")
    print(f"wrote {out}: mark the right row's confirm column with y, or leave a lease blank for no match")
    return len(leases)


def apply_links(path: Path = CANDIDATES) -> int:
    links = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("confirm") or "").strip().lower() in ("y", "yes", "x", "1", "true"):
                og, dist, lease_no = row["rrc_lease"].split("-", 2)
                links.append((row["document_id"], og, dist, lease_no))
    init_raw()
    docs = sorted({l[0] for l in links})
    with connect() as con:
        if docs:
            con.execute(f"delete from raw.tract_rrc_xref where tract_id in ({','.join('?' * len(docs))})", docs)
        # The lease's whole share of each linked RRC lease: real leases rarely
        # state how a pooled unit's production is split.
        con.executemany("insert into raw.tract_rrc_xref values (?, ?, ?, ?, 1.0, now())", links)
    print(f"linked {len(docs)} leases to {len(links)} RRC leases")
    return len(links)
