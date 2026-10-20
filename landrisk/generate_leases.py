"""Generate synthetic oil and gas lease PDFs plus an answer key.

Every page is watermarked as synthetic and the lessee is fictional: these are
demo documents, not records. Tracts are tied to the RRC leases already loaded,
so the risk scenarios (healthy HBP, production gaps, expiring undeveloped
acreage) come from real production patterns when real RRC data is loaded.
"""
from __future__ import annotations

import random
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .config import LEASE_PDF_DIR, SYNTHETIC_LESSEE, ensure_dirs
from .db import connect, init_raw

WATERMARK = "SYNTHETIC DEMO DOCUMENT - NOT A REAL LEASE - GENERATED FOR TESTING"

ROYALTY_WORDS = {
    0.125: "one-eighth (1/8)",
    0.1875: "three-sixteenths (3/16)",
    0.2: "one-fifth (1/5)",
    0.225: "twenty-two and one-half percent (22.5%)",
    0.25: "one-fourth (1/4)",
}
TERM_WORDS = {36: "three (3) years", 42: "forty-two (42) months",
              48: "four (4) years", 60: "five (5) years"}
NUM_WORDS = {60: "sixty (60)", 90: "ninety (90)", 120: "one hundred twenty (120)",
             180: "one hundred eighty (180)"}
FIRST = ["Margaret", "Daniel", "Ruth", "Wendell", "Lucia", "Harold", "Imelda", "Travis",
         "Beatrice", "Ramon", "Opal", "Clifford", "Yolanda", "Vernon", "Estela", "Russell"]
LAST = ["Holloway", "Castillo", "Harlan", "Pruitt", "Saenz", "McBride", "Villarreal",
        "Odom", "Tijerina", "Brannon", "Escobedo", "Gaither", "Quintero", "Stroud"]
COMPANIES = ["Lone Draw Minerals, LP", "Pecos Valley Royalty Partners, Ltd.",
             "Caprock Mineral Holdings, LLC", "Salt Flat Royalties, LP"]
SURVEYS = ["T&P RR Co. Survey", "H&TC RR Co. Survey", "PSL Survey", "GC&SF RR Co. Survey"]
COUNTY_FALLBACK = ["Reeves", "Loving", "Ward", "Culberson", "Midland", "Howard"]


@dataclass
class Lease:
    document_id: str
    scenario: str
    truth: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    paragraphs: list = field(default_factory=list)  # (style, text)
    xref: list = field(default_factory=list)  # (oil_gas_code, district_no, lease_no, alloc)


def _fmt_date(d: date, style: int) -> str:
    if style == 0:
        day = d.day
        suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"the {day}{suffix} day of {d:%B}, {d.year}"
    if style == 1:
        return f"{d:%B} {d.day}, {d.year}"
    return f"{d:%m/%d/%Y}"


def _lessor(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.45:
        return f"{rng.choice(FIRST)} {rng.choice('ABCDEJLMR')}. {rng.choice(LAST)}"
    if kind < 0.65:
        a, b = rng.choice(FIRST), rng.choice(FIRST)
        ln = rng.choice(LAST)
        return f"{a} {ln} and {b} {ln}, husband and wife"
    if kind < 0.8:
        ln = rng.choice(LAST)
        return f"The {rng.choice(FIRST)} {ln} Family Trust, {rng.choice(FIRST)} {ln}, Trustee"
    if kind < 0.9:
        return f"Estate of {rng.choice(FIRST)} {rng.choice(LAST)}, Deceased"
    return rng.choice(COMPANIES)


def _rrc_leases(con) -> list[dict]:
    """RRC leases with their production status as of the latest reported month."""
    rows = con.execute("""
        with p as (
            select oil_gas_code, district_no, lease_no, cycle_year_month as ym,
                   coalesce(try_cast(lease_oil_prod_vol as bigint), 0)
                 + coalesce(try_cast(lease_cond_prod_vol as bigint), 0) as liquids,
                   coalesce(try_cast(lease_gas_prod_vol as bigint), 0)
                 + coalesce(try_cast(lease_csgd_prod_vol as bigint), 0) as gas
            from raw.rrc_lease_cycle
        ), latest as (select max(ym) as latest_ym from p)
        select p.oil_gas_code, p.district_no, p.lease_no,
               max(case when liquids + gas > 0 then p.ym end) as last_prod_ym,
               min(case when liquids + gas > 0 then p.ym end) as first_prod_ym,
               any_value(latest.latest_ym) as latest_ym,
               (select any_value(county_name) from raw.rrc_well_completion w
                 where w.oil_gas_code = p.oil_gas_code and w.district_no = p.district_no
                   and w.lease_no = p.lease_no) as county
        from p, latest
        group by all
        having last_prod_ym is not null
        order by p.oil_gas_code, p.district_no, p.lease_no  -- the caller shuffles with a fixed seed
    """).fetchall()
    out = []
    for og, dist, lno, last_ym, first_ym, latest_ym, county in rows:
        ly, lm = int(last_ym[:4]), int(last_ym[4:])
        ty, tm = int(latest_ym[:4]), int(latest_ym[4:])
        gap = (ty - ly) * 12 + (tm - lm)
        out.append({"key": (og, dist, lno), "gap": gap, "first_ym": first_ym,
                    "county": (county or "").title() or None})
    return out


def _build(rng: random.Random, doc_no: int, scenario: str, as_of: date,
           county: str, rrc: list[tuple], first_prod_ym: str | None) -> Lease:
    lease = Lease(document_id=f"LSE-{doc_no:04d}", scenario=scenario)
    t, ev, paras = lease.truth, lease.evidence, lease.paragraphs

    term = rng.choice(list(TERM_WORDS))
    if scenario in ("developed", "developed_gap"):
        # Signed before first production, so the primary term has long ended.
        fy = int(first_prod_ym[:4]) if first_prod_ym else as_of.year - 5
        eff = date(max(fy - rng.randint(1, 3), 2012), rng.randint(1, 12), rng.randint(1, 28))
        if (eff.year * 12 + eff.month + term) > (as_of.year * 12 + as_of.month):
            eff = date(as_of.year - 6, eff.month, eff.day)
    elif scenario == "expiring_undeveloped":
        end = as_of + timedelta(days=rng.randint(20, 170))
        eff = date(end.year - term // 12, end.month, min(end.day, 28)) - timedelta(days=(term % 12) * 30)
    elif scenario == "expired_undeveloped":
        end = as_of - timedelta(days=rng.randint(15, 200))
        eff = date(end.year - term // 12, end.month, min(end.day, 28)) - timedelta(days=(term % 12) * 30)
    else:  # primary_term_ok
        eff = as_of - timedelta(days=rng.randint(90, 400))

    acres = round(rng.choice([40, 80, 120, 160, 200, 240, 320, 640]) * rng.uniform(0.97, 1.0), 2)
    tract = f"T-{doc_no:04d}"
    lessor = _lessor(rng)
    royalty = rng.choice([0.1875, 0.1875, 0.2, 0.2, 0.225, 0.25])
    rider = rng.random() < 0.2  # royalty amended by Exhibit A: a classic extraction trap
    body_royalty = 0.125 if rider else royalty
    has_shut_in = rng.random() < 0.85
    shut_in_rate = rng.choice([10, 20, 25, 50]) if has_shut_in else None
    shut_in_days = rng.choice([60, 90, 90, 120]) if has_shut_in else None
    pugh = rng.random() < 0.5
    depth = rng.choice([None, None, None, 9500, 11000, 12500])
    cdc = rng.choice([None, None, 120, 180]) if scenario.startswith("developed") else None
    date_style = rng.randint(0, 2)
    signed_later = rng.random() < 0.25

    t.update({
        "lessor": lessor, "lessee": SYNTHETIC_LESSEE, "effective_date": eff.isoformat(),
        "primary_term_months": str(term), "royalty_rate": f"{royalty}",
        "county": county, "state": "TX", "tract_id": tract, "gross_acres": f"{acres:.2f}",
        "depth_limit_ft": str(depth) if depth else "", "pugh_clause": str(pugh).lower(),
        "shut_in_payment_per_acre": f"{shut_in_rate:.2f}" if shut_in_rate else "",
        "shut_in_grace_days": str(shut_in_days) if shut_in_days else "",
        "continuous_drilling_days": str(cdc) if cdc else "",
    })

    paras.append(("title", "OIL, GAS AND MINERAL LEASE"))
    paras.append(("small", f"Document No. {lease.document_id}"))

    if signed_later:
        signed = eff + timedelta(days=rng.randint(5, 40))
        s = (f"This Oil, Gas and Mineral Lease is executed on {_fmt_date(signed, 1)} but is made "
             f"effective as of {_fmt_date(eff, date_style)} (the \"Effective Date\"), by and between "
             f"{lessor}, as Lessor, and {SYNTHETIC_LESSEE}, as Lessee.")
    else:
        s = (f"THIS AGREEMENT made and entered into {_fmt_date(eff, date_style)}, by and between "
             f"{lessor}, hereinafter called Lessor, and {SYNTHETIC_LESSEE}, hereinafter called Lessee.")
    paras.append(("body", s))
    ev.update(lessor=s, lessee=s, effective_date=s)

    section, block, twp = rng.randint(1, 48), rng.randint(1, 60), rng.randint(1, 4)
    survey = rng.choice(SURVEYS)
    s = (f"1. Grant. Lessor, in consideration of ten dollars and other good and valuable consideration, "
         f"hereby grants, leases and lets exclusively unto Lessee the following described land for the "
         f"purpose of exploring for, producing and marketing oil and gas: Tract {tract}, being {acres:.2f} "
         f"acres of land, more or less, out of Section {section}, Block {block}, Township {twp}, {survey}, "
         f"{county} County, Texas.")
    paras.append(("body", s))
    ev.update(tract_id=s, gross_acres=s, county=s, state=s)

    if depth:
        s = (f"2. Depth Limitation. This lease covers only those depths from the surface of the earth to "
             f"{depth:,} feet below the surface, and all depths below {depth:,} feet are excepted and "
             f"reserved to Lessor.")
        paras.append(("body", s))
        ev["depth_limit_ft"] = s

    s = (f"3. Term. Subject to the other provisions herein, this lease shall remain in force for a term of "
         f"{TERM_WORDS[term]} from the Effective Date (the \"primary term\") and as long thereafter as oil "
         f"or gas is produced in paying quantities from said land or lands pooled therewith.")
    paras.append(("body", s))
    ev["primary_term_months"] = s

    s = (f"4. Royalties. Lessee shall deliver to the credit of Lessor, free of cost, {ROYALTY_WORDS[body_royalty]} "
         f"of all oil produced and saved from the leased premises, and shall pay Lessor "
         f"{ROYALTY_WORDS[body_royalty]} of the market value at the well of gas sold or used.")
    paras.append(("body", s))
    ev["royalty_rate"] = s

    if has_shut_in:
        s = (f"5. Shut-in Royalty. If, after the primary term, all wells on the leased premises or lands pooled "
             f"therewith are shut in and production ceases, this lease shall not terminate if Lessee pays to "
             f"Lessor, within {NUM_WORDS[shut_in_days]} days after the date production ceased, a shut-in royalty "
             f"equal to ${shut_in_rate:.2f} per acre then covered by this lease, and the lease shall be "
             f"considered to be producing in paying quantities for so long as such payments are made.")
        paras.append(("body", s))
        ev.update(shut_in_payment_per_acre=s, shut_in_grace_days=s)

    if cdc:
        s = (f"6. Continuous Development. After the primary term, Lessee may maintain this lease as to acreage "
             f"not included in a producing unit by commencing a new well within {NUM_WORDS[cdc]} days after "
             f"the completion or abandonment of the preceding well, with no more than {NUM_WORDS[cdc]} days "
             f"elapsing between such operations.")
        paras.append(("body", s))
        ev["continuous_drilling_days"] = s

    if pugh:
        s = ("7. Release of Non-Producing Acreage. Notwithstanding anything to the contrary, at the end of the "
             "primary term or any continuous development period, this lease shall terminate as to all lands "
             "and depths not included within a producing or pooled unit, and Lessee shall execute a release of "
             "such lands and depths.")
        paras.append(("body", s))
        ev["pugh_clause"] = s
    else:
        s = ("7. Pooling. Lessee may pool the leased premises with other lands to form units, and production "
             "from any unit shall be treated as production from the leased premises.")
        paras.append(("body", s))
        ev["pugh_clause"] = s

    paras.append(("body", "8. Entire Agreement. This lease, together with any exhibits attached hereto, "
                          "constitutes the entire agreement between the parties."))
    paras.append(("body", f"IN WITNESS WHEREOF, this instrument is executed as of the date first written above."))
    paras.append(("body", f"LESSOR: {lessor}"))
    paras.append(("body", f"LESSEE: {SYNTHETIC_LESSEE}"))

    if rider:
        s = (f"EXHIBIT A - Addendum. Notwithstanding Paragraph 4 of the lease to which this Exhibit is attached, "
             f"the royalty payable to Lessor on oil and gas shall be {ROYALTY_WORDS[royalty]} rather than "
             f"one-eighth (1/8). In the event of a conflict, this Exhibit controls.")
        paras.append(("pagebreak", ""))
        paras.append(("title", "EXHIBIT A"))
        paras.append(("body", s))
        ev["royalty_rate"] = s

    lease.xref = rrc
    return lease


def _render(lease: Lease) -> None:
    path = LEASE_PDF_DIR / f"{lease.document_id}.pdf"
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Times-Roman",
                          fontSize=10.5, leading=14, spaceAfter=8)
    title = ParagraphStyle("t", parent=styles["Title"], fontName="Times-Bold", fontSize=14)
    small = ParagraphStyle("s", parent=body, fontSize=9, alignment=1)

    def stamp(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillGray(0.45)
        canvas.drawCentredString(LETTER[0] / 2, LETTER[1] - 0.45 * inch, WATERMARK)
        canvas.drawCentredString(LETTER[0] / 2, 0.45 * inch, WATERMARK)
        canvas.restoreState()

    from reportlab.platypus import PageBreak
    story = []
    for style, text in lease.paragraphs:
        if style == "pagebreak":
            story.append(PageBreak())
        elif style == "title":
            story.extend([Paragraph(text, title), Spacer(1, 4)])
        elif style == "small":
            story.append(Paragraph(text, small))
        else:
            story.append(Paragraph(text.replace("&", "&amp;"), body))
    SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=0.9 * inch, bottomMargin=0.9 * inch,
                      title=f"Synthetic lease {lease.document_id}").build(story, onFirstPage=stamp, onLaterPages=stamp)


def generate(count: int = 18, seed: int = 42, as_of: date | None = None) -> list[Lease]:
    ensure_dirs()
    init_raw()
    rng = random.Random(seed)
    as_of = as_of or date.today()

    with connect() as con:
        rrc = _rrc_leases(con)
    gaps = [r for r in rrc if r["gap"] >= 3]
    active = [r for r in rrc if r["gap"] < 3]
    rng.shuffle(gaps)
    rng.shuffle(active)

    n_undeveloped = max(3, count // 4)
    n_gap = min(len(gaps), max(3, count // 4))
    n_active = min(len(active), count - n_undeveloped - n_gap)
    if not rrc:
        print("No RRC production loaded; generating undeveloped-acreage leases only.")

    plan: list[tuple[str, dict | None]] = (
        [("developed_gap", r) for r in gaps[:n_gap]]
        + [("developed", r) for r in active[:n_active]]
    )
    undeveloped = ["expiring_undeveloped", "expired_undeveloped", "expiring_undeveloped", "primary_term_ok"]
    i = 0
    while len(plan) < count:
        plan.append((undeveloped[i % len(undeveloped)], None))
        i += 1

    if LEASE_PDF_DIR.exists():
        shutil.rmtree(LEASE_PDF_DIR)
    ensure_dirs()

    leases = []
    for i, (scenario, r) in enumerate(plan, start=1):
        county = (r and r["county"]) or rng.choice(COUNTY_FALLBACK)
        xref = [(*r["key"], 1.0)] if r else []
        lease = _build(rng, i, scenario, as_of, county, xref, r and r["first_ym"])
        _render(lease)
        leases.append(lease)

    with connect() as con:
        # New documents replace the old set, so earlier extractions and reviews no longer apply.
        for table in ("lease_ground_truth", "tract_rrc_xref", "lease_documents",
                      "lease_extraction_fields", "lease_reviews"):
            con.execute(f"delete from raw.{table}")
        for lease in leases:
            for name, value in lease.truth.items():
                con.execute("insert into raw.lease_ground_truth values (?, ?, ?, ?, ?)",
                            [lease.document_id, name, value, lease.evidence.get(name), lease.scenario])
            for og, dist, lno, alloc in lease.xref:
                con.execute("insert into raw.tract_rrc_xref values (?, ?, ?, ?, ?, now())",
                            [lease.truth["tract_id"], og, dist, lno, alloc])

    counts: dict[str, int] = {}
    for lease in leases:
        counts[lease.scenario] = counts.get(lease.scenario, 0) + 1
    print(f"Generated {len(leases)} synthetic leases in {LEASE_PDF_DIR}: {counts}")
    return leases
