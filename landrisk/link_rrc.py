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


# Where lease forms start the land description: "the following described land,
# hereinafter called leased premises: (use Exhibit A ...): Being 623.29 acres ..."
# or "... and is described as follows: All those certain tracts ...".
_LAND_START = [
    re.compile(r"following described land[^:]{0,120}:\s*(?:\([^)]{0,80}\):\s*)?", re.I),
    re.compile(r"described as follows[^:]{0,120}:\s*", re.I),
    re.compile(r"following described land[^.]{0,80}\.\s*", re.I),
]
_ABSTRACT = re.compile(r"\bA\s*[-\u2013]\s*(\d{1,5})\b|\bAbst(?:ract|\.)?\s*(?:No\.?\s*)?(\d{1,5})\b")
# "Railroad Commission Of Texas District 7B no. 18751": a lease citing its RRC lease.
# The dump codes districts differently (7B is 08), so match on the number alone.
_RRC_NUMBER = re.compile(r"(?:Railroad Commission|R\.?R\.?C\.?)[^.]{0,80}?\b(?:no\.?|number|#)\s*(\d{4,6})\b", re.I)
_SURVEY = re.compile(r"((?:[A-Z][\w.&'’-]*\s+){1,5}(?:Survey|League)(?:\s+No\.?\s*\d+)?)")
_ACRES = re.compile(r"([\d,]+(?:\.\d+)?)\s+(?:gross\s+)?acres", re.I)


def land_description(text: str) -> dict:
    """The lease's land description, with the abstract numbers, surveys and
    acreage it names: what you look up in the RRC GIS viewer to find the wells
    on the land."""
    flat = re.sub(r"\s+", " ", text)
    snippet = None
    for pattern in _LAND_START:
        m = pattern.search(flat)
        if m:
            snippet = flat[m.end():m.end() + 450]
            break
    if snippet is None:  # fall back to the first sentence naming acres and a survey or abstract
        for sentence in re.split(r"(?<=\.)\s+", flat[:20000]):
            if _ACRES.search(sentence) and (_SURVEY.search(sentence) or _ABSTRACT.search(sentence)):
                snippet = sentence[:450]
                break
    if snippet and len(snippet) == 450:
        snippet = snippet[:snippet.rfind(" ")] + " …"
    scope = snippet or ""
    abstracts = list(dict.fromkeys(f"A-{a or b}" for a, b in _ABSTRACT.findall(scope)))
    surveys = list(dict.fromkeys(s.strip() for s in _SURVEY.findall(scope)))[:3]
    acres = _ACRES.search(scope)
    return {"text": snippet, "abstracts": abstracts, "surveys": surveys,
            "acres": acres.group(1).replace(",", "") if acres else None}


def _shared_words(lease: dict, cand: dict) -> int:
    """Tie-break between equal scores: words the RRC name shares with the lessor,
    counting the entity words scoring ignores ("NOACK FARMS" over "NOACK -A-")."""
    words = lambda s: set(re.findall(r"[A-Z0-9]+", (s or "").upper()))
    return len(words(lease.get("lessor")) & words(cand.get("lease_name")))


def _ranked(lease: dict, pool: list[dict]) -> list[tuple[tuple[float, str], dict]]:
    return sorted(((score_candidate(lease, c), c) for c in pool),
                  key=lambda x: (-x[0][0], -_shared_words(lease, x[1])))


_KEY = re.compile(r"^[OG]-[0-9A-Z]{2}-\d{1,6}$")


def named_in_land(lease_name: str | None, land: str | None) -> bool:
    """Whether the land description names this RRC lease, e.g. "The F. C. Hinds
    oil and gas lease" names "HINDS, F. C." and "HINDS, F.C. -H-" (unit
    suffixes ignored) but not "HINDS, B.T." or "HINDS, ADDIE"."""
    if not lease_name or not land:
        return False
    name = re.sub(r'"[^"]*"|-\s*\w{1,3}\s*-|\b(?:UNIT|LEASE|NO\.?\s*\d+)\b', " ", lease_name.upper())
    head, _, tail = name.partition(",")
    land_words = set(re.findall(r"[A-Z0-9]+", land.upper()))
    head_words = tokens(head)
    if not head_words or not head_words <= land_words:
        return False
    parts = re.findall(r"[A-Z]+", tail)
    words = [w for w in parts if len(w) > 1 and w not in STOP]
    initials = [w for w in parts if len(w) == 1]
    if words and not set(words) <= land_words:
        return False
    if initials:
        surname = re.escape(sorted(head_words)[0])
        letters = r"\.?\s*".join(initials) + r"\.?"
        return bool(re.search(rf"\b{letters}\s+{surname}\b|\b{surname},?\s+{letters}", land, re.I))
    return True


def cited_rrc_numbers(text: str | None) -> set[str]:
    return {n.lstrip("0") for n in _RRC_NUMBER.findall(text or "")}


def score_candidate(lease: dict, cand: dict) -> tuple[float, str]:
    """(score 0-1, short reason) for one RRC lease as the source of a lease's production.

    `lease` may carry `land`, the land description: leases on land that already
    produced often name the RRC lease there ("The F. C. Hinds oil and gas
    lease") or cite its number, which beats any name similarity."""
    land = lease.get("land")
    if (cand.get("lease_no") or "").lstrip("0") in cited_rrc_numbers(land):
        return 1.0, f"the lease cites RRC lease no. {cand['lease_no'].lstrip('0')}"
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
    named = 0.3 if named_in_land(cand.get("lease_name"), land) else 0.0
    if named:
        reasons.insert(0, "named in the land description")
    if timing_note:
        reasons.append(timing_note)
    # Capped below 1.0, which is kept for a cited RRC lease number.
    return round(min(0.99, 0.5 * lessor + 0.3 * lessee + timing + named), 3), "; ".join(reasons)


_FIELDS = ("lessor", "lessee", "county", "effective_date")
_VOLUME = """coalesce(try_cast(lease_oil_prod_vol as bigint), 0) + coalesce(try_cast(lease_gas_prod_vol as bigint), 0)
             + coalesce(try_cast(lease_cond_prod_vol as bigint), 0) + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0)"""


def _leases(con, document_id: str | None = None) -> list[dict]:
    """Lessor, lessee, county and effective date per lease, with reviews applied,
    read from the raw tables so it works without a dbt build."""
    rows = con.execute(f"""
        with latest as (select document_id, arg_max(extraction_id, extracted_at) as eid
                        from raw.lease_extraction_fields group by 1),
        decisions as (select document_id, field_name, arg_max(action, reviewed_at) as action,
                             arg_max(corrected_value, reviewed_at) as value
                      from raw.lease_reviews group by 1, 2)
        select e.document_id,
               {", ".join(f"max(case when e.field_name = '{f}' then coalesce(case when d.action = 'correct' then d.value end, d.value, e.value) end) as {f}" for f in _FIELDS)}
        from raw.lease_extraction_fields e
        join latest l on l.document_id = e.document_id and l.eid = e.extraction_id
        left join decisions d on d.document_id = e.document_id and d.field_name = e.field_name
        where ? is null or e.document_id = ?
        group by 1 order by 1""", [document_id, document_id]).fetchall()
    return [dict(zip(["document_id", *_FIELDS], r)) for r in rows]


def _rrc_leases(con, county: str | None = None) -> list[dict]:
    """RRC leases with their latest name and operator and first/last producing month."""
    rows = con.execute(f"""
        with wells as (
            select oil_gas_code, district_no, lease_no, upper(trim(any_value(county_name))) as county
            from raw.rrc_well_completion group by 1, 2, 3
            having ? is null or upper(trim(any_value(county_name))) = upper(?)
        ), prod as (
            select oil_gas_code, district_no, lease_no,
                   arg_max(trim(lease_name), cycle_year_month) as lease_name,
                   arg_max(trim(operator_name), cycle_year_month) as operator_name,
                   min(cycle_year_month) filter (where {_VOLUME} > 0) as first_prod_ym,
                   max(cycle_year_month) filter (where {_VOLUME} > 0) as last_prod_ym
            from raw.rrc_lease_cycle
            where (oil_gas_code, district_no, lease_no) in (select oil_gas_code, district_no, lease_no from wells)
            group by 1, 2, 3
        )
        select p.*, w.county from prod p join wells w using (oil_gas_code, district_no, lease_no)""",
        [county, county]).fetchall()
    cols = ["oil_gas_code", "district_no", "lease_no", "lease_name", "operator_name",
            "first_prod_ym", "last_prod_ym", "county"]
    return [dict(zip(cols, r), key=f"{r[0]}-{r[1]}-{r[2]}") for r in rows]


def _land(document_id: str) -> dict:
    from .config import LEASE_PDF_DIR
    from .extract import pdf_text
    path = LEASE_PDF_DIR / f"{document_id}.pdf"
    return land_description(pdf_text(path)[0]) if path.exists() else land_description("")


def _annual_boe(con, keys: list[str]) -> dict[str, list[tuple[int, float]]]:
    """Yearly BOE per RRC lease, for the small charts on the linking screen."""
    if not keys:
        return {}
    rows = con.execute(f"""
        select oil_gas_code || '-' || district_no || '-' || lease_no as key, left(cycle_year_month, 4)::int as year,
               sum(coalesce(try_cast(lease_oil_prod_vol as bigint), 0) + coalesce(try_cast(lease_cond_prod_vol as bigint), 0)
                   + (coalesce(try_cast(lease_gas_prod_vol as bigint), 0) + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0)) / 6.0)
        from raw.rrc_lease_cycle
        where oil_gas_code || '-' || district_no || '-' || lease_no in ({",".join("?" * len(keys))})
        group by 1, 2 order by 1, 2""", keys).fetchall()
    out: dict[str, list[tuple[int, float]]] = {}
    for key, year, boe in rows:
        out.setdefault(key, []).append((year, round(boe or 0, 1)))
    return out


def _linked_keys(con, document_id: str) -> list[str]:
    return [r[0] for r in con.execute("""select oil_gas_code || '-' || district_no || '-' || lease_no
                                         from raw.tract_rrc_xref where tract_id = ? order by 1""",
                                      [document_id]).fetchall()]


def _claimed_elsewhere(con, document_id: str, keys: list[str]) -> dict[str, list[str]]:
    """RRC leases among `keys` that other lease documents already claim."""
    if not keys:
        return {}
    rows = con.execute(f"""select oil_gas_code || '-' || district_no || '-' || lease_no as key, tract_id
                           from raw.tract_rrc_xref
                           where tract_id <> ? and oil_gas_code || '-' || district_no || '-' || lease_no
                                 in ({",".join("?" * len(keys))}) order by 2""", [document_id, *keys]).fetchall()
    out: dict[str, list[str]] = {}
    for key, doc in rows:
        out.setdefault(key, []).append(doc)
    return out


def link_overview(con) -> list[dict]:
    """Each lease with its link state, for the list on the linking screen."""
    decisions = {d: dec for d, dec in con.execute(
        "select document_id, arg_max(decision, decided_at) from raw.rrc_link_decisions group by 1").fetchall()}
    linked = {d: n for d, n in con.execute(
        "select tract_id, count(*) from raw.tract_rrc_xref group by 1").fetchall()}
    out = []
    for lease in _leases(con):
        d = lease["document_id"]
        status = "linked" if linked.get(d) else ("no_match" if decisions.get(d) == "no_match" else "open")
        out.append({**lease, "status": status, "linked_count": linked.get(d, 0)})
    return out


def link_detail(con, document_id: str, top: int = 8) -> dict:
    """One lease: its land description, scored candidates in its county, and the
    RRC leases already linked (shown even when they aren't top candidates)."""
    found = _leases(con, document_id)
    if not found:
        raise KeyError(document_id)
    lease = found[0]
    land = _land(document_id)
    pool = _rrc_leases(con, lease["county"]) if lease["county"] else []
    scored = _ranked({**lease, "land": land["text"]}, pool)
    linked = _linked_keys(con, document_id)
    chosen = [(s, c) for s, c in scored[:top]]
    shown = {c["key"] for _, c in chosen}
    extra = [k for k in linked if k not in shown]
    if extra:  # linked leases outside the top candidates, possibly from another county
        by_key = {c["key"]: c for _, c in scored}
        missing = [k for k in extra if k not in by_key]
        if missing:
            by_key.update({c["key"]: c for c in _rrc_leases(con) if c["key"] in missing})
        chosen += [((None, "linked"), by_key[k]) for k in extra if k in by_key]
    series = _annual_boe(con, [c["key"] for _, c in chosen])
    elsewhere = _claimed_elsewhere(con, document_id, [c["key"] for _, c in chosen])
    candidates = [{**c, "score": s, "why": why, "linked": c["key"] in linked, "annual_boe": series.get(c["key"], []),
                   "linked_to": elsewhere.get(c["key"], [])}
                  for (s, why), c in chosen]
    decision = con.execute("select arg_max(decision, decided_at) from raw.rrc_link_decisions where document_id = ?",
                           [document_id]).fetchone()[0]
    return {"lease": lease, "land": land, "candidates": candidates, "linked": linked,
            "decision": decision, "county_rrc_leases": len(pool)}


def search_rrc(con, q: str, county: str | None = None, limit: int = 20) -> list[dict]:
    """RRC leases by name or lease number, to add one the shortlist missed."""
    q = q.strip().upper()
    if len(q) < 2:
        return []
    hits = [c for c in _rrc_leases(con, county)
            if q in (c["lease_name"] or "").upper() or q.lstrip("0") == c["lease_no"].lstrip("0")
            or q == c["key"].upper()]
    hits.sort(key=lambda c: (c["last_prod_ym"] or ""), reverse=True)
    series = _annual_boe(con, [c["key"] for c in hits[:limit]])
    elsewhere = _claimed_elsewhere(con, "", [c["key"] for c in hits[:limit]])
    return [{**c, "score": None, "why": "search result", "linked": False, "annual_boe": series.get(c["key"], []),
             "linked_to": elsewhere.get(c["key"], [])} for c in hits[:limit]]


def save_links(con, document_id: str, keys: list[str], no_match: bool = False, reviewer: str = "reviewer") -> None:
    """Replace a lease's RRC links; with no_match, record that nothing produces on its land."""
    parsed = []
    for key in ([] if no_match else keys):
        if not _KEY.match(key):
            raise ValueError(f"bad RRC lease key {key!r}; expected e.g. O-01-16355")
        parsed.append((document_id, *key.split("-", 2)))
    # Each linked lease gets all of an RRC lease's production, so two lease
    # documents can't share one: that would count it twice (and fails the
    # tract_allocations_do_not_exceed_100pct test). Two documents covering the
    # same land, like a top lease over an existing one, usually mean only one is
    # in effect.
    conflicts = _claimed_elsewhere(con, document_id, [] if no_match else keys)
    if conflicts:
        detail = "; ".join(f"{k} is already linked to {', '.join(docs)}" for k, docs in conflicts.items())
        raise ValueError(f"{detail}. Its production can only be counted for one lease; unlink it there first "
                         "if this lease is the one in effect.")
    con.execute("delete from raw.tract_rrc_xref where tract_id = ?", [document_id])
    if parsed:
        # A linked lease gets the whole of each RRC lease's production; real
        # leases rarely say how a pooled unit is split.
        con.executemany("insert into raw.tract_rrc_xref values (?, ?, ?, ?, 1.0, now())", parsed)
    con.execute("insert into raw.rrc_link_decisions values (?, ?, ?, ?, now())",
                [document_id, "no_match" if no_match else ("linked" if parsed else "cleared"),
                 ",".join(keys) if not no_match else None, reviewer])


def suggest(top: int = 5, out: Path = CANDIDATES) -> int:
    with connect(must_exist=True) as con:
        leases, rrc = _leases(con), _rrc_leases(con)
    if not rrc:
        raise SystemExit("No RRC production loaded. Run ingest-rrc with --county first.")
    by_county: dict[str, list[dict]] = {}
    for c in rrc:
        by_county.setdefault(c["county"] or "", []).append(c)

    out.parent.mkdir(parents=True, exist_ok=True)
    kept, old_rows = {}, []  # confirmations from an earlier run survive a rewrite
    if out.exists():
        with out.open(newline="") as fh:
            old_rows = list(csv.DictReader(fh))
        kept = {(r["document_id"], r["rrc_lease"]): r.get("confirm", "") for r in old_rows}
    cols = ["document_id", "lessor", "lessee", "county", "effective_date", "rank", "score", "rrc_lease",
            "rrc_lease_name", "rrc_operator", "first_production", "last_production", "why", "confirm"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        for lease in leases:
            pool = by_county.get((lease["county"] or "").upper(), [])
            land = _land(lease["document_id"])["text"]
            scored = _ranked({**lease, "land": land}, pool)[:top]
            if not scored:
                w.writerow({**{k: lease.get(k) for k in cols if k in lease}, "rank": "",
                            "why": f"no RRC leases loaded for county {lease['county']!r}"})
            for rank, ((score, why), c) in enumerate(scored, start=1):
                w.writerow({**{k: lease.get(k) for k in cols if k in lease}, "rank": rank, "score": score,
                            "rrc_lease": f"{c['oil_gas_code']}-{c['district_no']}-{c['lease_no']}",
                            "rrc_lease_name": c["lease_name"], "rrc_operator": c["operator_name"],
                            "first_production": c["first_prod_ym"], "last_production": c["last_prod_ym"],
                            "why": why, "confirm": kept.get((lease["document_id"], f"{c['oil_gas_code']}-{c['district_no']}-{c['lease_no']}"), "")})
            # Confirmed rows outside this run's top candidates (added by hand) stay.
            emitted = {f"{c['oil_gas_code']}-{c['district_no']}-{c['lease_no']}" for _, c in scored}
            for r in old_rows:
                if (r["document_id"] == lease["document_id"] and r["rrc_lease"] not in emitted
                        and (r.get("confirm") or "").strip()):
                    w.writerow({k: r.get(k, "") for k in cols})
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
