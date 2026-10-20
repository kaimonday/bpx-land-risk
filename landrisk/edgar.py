"""Collect real, executed oil and gas leases from SEC EDGAR filing exhibits.

Small operators often attach signed leases to their filings (usually as
EX-10 exhibits). EDGAR full-text search is free and allows scripted access
as long as requests carry a User-Agent naming who you are and stay under
10 per second: https://www.sec.gov/os/accessing-edgar-data

The documents are real and name real people, so everything lands under
data/ (gitignored). They have no answer key: `ingest` writes a labels.csv to
fill in by hand, and `load_labels` turns it into raw.lease_ground_truth so
`python cli.py evaluate` can score extraction against it.
"""
from __future__ import annotations

import csv
import html
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import DATA_DIR
from .db import connect, init_raw
from .fields import FIELD_NAMES

EDGAR_DIR = DATA_DIR / "real" / "edgar"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}"

# Phrases that appear in the body of a granting clause, not just in a mention.
QUERIES = [
    '"paid up oil and gas lease" "lessor hereby grants"',
    '"producers 88" "oil and gas lease"',
    '"oil and gas lease" "lessor hereby grants" "primary term"',
    '"oil, gas and mineral lease" "lessor" "lessee" "royalty"',
]
REQUEST_GAP_S = 0.2  # five requests a second, half of SEC's limit
SKIP = "-"           # label value meaning "don't score this field"


# ---------- pure helpers (tested) ----------

def html_to_text(raw: str, keep_paragraphs: bool = False) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    if keep_paragraphs:
        raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ")
    if keep_paragraphs:
        lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", text).strip()


# "Oil and gas lease" itself, not "oil and gas leases" or "oil and gas lease acreage".
LEASE_TITLE = re.compile(r"oil(,)? (and |& )?gas( and mineral)? lease\b(?! acreage)")
# Titles of documents that mention leases without being one. "Lease agreement"
# alone is usually a surface or sand lease; "oil and gas lease agreement" is fine.
OTHER_TITLES = re.compile(r"purchase and sale agreement|purchase of|bill of sale|assignment"
                          r"|farmout agreement|participation agreement|non-competition|merger"
                          r"|(?<!gas )lease agreement")
GRANTING_CLAUSE = re.compile(r"leases?,? and lets|lessor (?:does )?hereby (?:grants?|leases?)")


def looks_like_executed_lease(text: str) -> tuple[bool, str]:
    """Cheap screen for one signed oil and gas lease, not a blank form, a bundle or a mention."""
    low = re.sub(r"\s+", " ", text.lower())
    if not 5_000 <= len(text) <= 150_000:
        return False, f"length {len(text)} outside 5k-150k characters"
    if "lessor" not in low or "lessee" not in low:
        return False, "no lessor/lessee"
    title = LEASE_TITLE.search(low[:1500])
    other = OTHER_TITLES.search(low[:1500])
    if not title or (other and other.start() < title.start()):
        return False, "not titled as an oil and gas lease"
    head = low[:4000]
    if "form of" in head[:400] or head.count("____") >= 3 or re.search(r"day of\s*_{2,}", head):
        return False, "blank form"
    if not re.search(r"(day of|dated|effective)[^.]{0,40}\b(19[5-9]\d|20[0-3]\d)\b", head):
        return False, "no execution or effective date near the top"
    starts = [m.start() for m in GRANTING_CLAUSE.finditer(low)]
    clauses = [p for i, p in enumerate(starts) if i == 0 or p - starts[i - 1] > 200]
    if len(clauses) > 1:
        return False, "more than one granting clause (a bundle of leases)"
    return True, "ok"


def _shingles(text: str, n: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.85) -> bool:
    """Same exhibit refiled (an S-1 and an S-4, say) with different headers and spacing."""
    sa, sb = _shingles(a), _shingles(b)
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= threshold


def label_rows(path: Path) -> list[tuple[str, str, str | None]]:
    """(document_id, field_name, value) from a filled labels.csv.

    A blank cell means the lease doesn't state that field (truth is null), a
    "-" means the field isn't scored. Rows with no labels at all are skipped
    so a half-finished sheet can be loaded.
    """
    out = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            labels = {f: (row.get(f) or "").strip() for f in FIELD_NAMES if f in row}
            if not any(v and v != SKIP for v in labels.values()):
                continue
            for field, value in labels.items():
                if value != SKIP:
                    out.append((row["document_id"], field, value or None))
    return out


# ---------- EDGAR access ----------

@dataclass
class Exhibit:
    company: str
    form: str
    filed: str
    file_type: str
    url: str


class Edgar:
    def __init__(self, user_agent: str):
        if "@" not in user_agent:
            raise SystemExit("SEC_USER_AGENT must name you and include a contact email, "
                             "e.g. 'Jane Doe jane@example.com'. Set it in .env.")
        self.client = httpx.Client(headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True)
        self._last = 0.0

    def get(self, url: str, **params) -> httpx.Response:
        wait = REQUEST_GAP_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.client.get(url, params=params or None)
        r.raise_for_status()
        return r

    def search(self, query: str, pages: int = 3) -> list[Exhibit]:
        found = []
        for page in range(pages):
            hits = self.get(SEARCH_URL, q=query, **{"from": page * 100}).json()["hits"]["hits"]
            for h in hits:
                s = h["_source"]
                if not s.get("file_type", "").upper().startswith(("EX-10", "EX-99")):
                    continue
                accession, filename = h["_id"].split(":", 1)
                cik = str(int(s["ciks"][0]))
                found.append(Exhibit(
                    company=re.sub(r"\s*\(.*$", "", (s.get("display_names") or ["?"])[0]),
                    form=(s.get("root_forms") or [s.get("form", "")])[0],
                    filed=s.get("file_date", ""),
                    file_type=s["file_type"],
                    url=ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""), filename=filename)))
            if len(hits) < 100:
                break
        return found


# ---------- output ----------

def _write_pdf(text: str, path: Path, title: str) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    body = ParagraphStyle("body", parent=getSampleStyleSheet()["BodyText"], fontName="Times-Roman",
                          fontSize=10.5, leading=14, spaceAfter=6)
    story = [Paragraph(html.escape(p), body) for p in text.split("\n") if p.strip()]
    SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                      title=title).build(story)


def ingest(count: int = 20, out_dir: Path = EDGAR_DIR, user_agent: str | None = None,
           prefer_state: str = "texas") -> list[dict]:
    """Find `count` executed leases, write them as PDFs plus manifest.csv and labels.csv."""
    edgar = Edgar(user_agent or os.getenv("SEC_USER_AGENT", ""))
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    candidates: dict[str, Exhibit] = {}
    for q in QUERIES:
        for ex in edgar.search(q):
            candidates.setdefault(ex.url, ex)
    print(f"{len(candidates)} candidate exhibits from {len(QUERIES)} searches")

    rejected: dict[str, int] = {}
    preferred, others = [], []
    for ex in candidates.values():
        text = html_to_text(edgar.get(ex.url).text, keep_paragraphs=True)
        ok, reason = looks_like_executed_lease(text)
        if ok and any(near_duplicate(text, kept) for _, kept in preferred + others):
            ok, reason = False, "duplicate"
        if not ok:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        (preferred if prefer_state in text[:6000].lower() else others).append((ex, text))
        if len(preferred) >= count:
            break
    accepted = (preferred + others)[:count]
    print(f"accepted {len(accepted)} ({len(preferred)} mention {prefer_state.title()}); rejected: {rejected}")

    manifest = []
    for i, (ex, text) in enumerate(accepted, start=1):
        doc_id = f"EDG-{i:04d}"
        _write_pdf(text, pdf_dir / f"{doc_id}.pdf", f"{ex.company} {ex.file_type}")
        manifest.append({"document_id": doc_id, "company": ex.company, "form": ex.form,
                         "filed": ex.filed, "exhibit": ex.file_type, "source_url": ex.url})

    with (out_dir / "manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, list(manifest[0]) if manifest else ["document_id"])
        w.writeheader()
        w.writerows(manifest)
    labels = out_dir / "labels.csv"
    if labels.exists():
        print(f"kept your existing {labels}; delete it to regenerate a blank sheet")
    else:
        with labels.open("w", newline="") as fh:
            w = csv.DictWriter(fh, ["document_id", "source_url", *FIELD_NAMES])
            w.writeheader()
            for m in manifest:
                w.writerow({"document_id": m["document_id"], "source_url": m["source_url"], "tract_id": SKIP})
    print(f"wrote {len(manifest)} PDFs to {pdf_dir}, plus manifest.csv and labels.csv")
    return manifest


def load_labels(path: Path = EDGAR_DIR / "labels.csv") -> int:
    rows = label_rows(path)
    docs = sorted({r[0] for r in rows})
    init_raw()
    with connect() as con:
        if docs:
            con.execute(f"delete from raw.lease_ground_truth where document_id in ({','.join('?' * len(docs))})", docs)
        con.executemany("insert into raw.lease_ground_truth values (?, ?, ?, null, 'edgar')", rows)
    print(f"loaded {len(rows)} labels for {len(docs)} documents")
    return len(docs)


# ---------- labels from the review screen ----------

def labels_from_reviews(extracted, reviews, field_names, skip):
    """Turn label-mode reviews into answer-key rows.

    extracted: (document_id, field_name, extracted_value) from the latest run.
    reviews: (document_id, field_name, action, corrected_value, reviewed_at).
    Returns ({document_id: {field: value}} for fully labeled documents,
             {document_id: (labeled, total)}). Accept keeps the extracted value,
    correct uses the corrected one (None means "not in the lease").
    """
    latest = {}
    for doc, field, action, value, at in reviews:
        if (doc, field) not in latest or at > latest[(doc, field)][2]:
            latest[(doc, field)] = (action, value, at)
    values = {(d, f): v for d, f, v in extracted}
    done, progress = {}, {}
    for doc in sorted({d for d, _, _ in extracted}):
        fields = [f for f in field_names if f not in skip and (doc, f) in values]
        labeled = {}
        for f in fields:
            if (doc, f) in latest:
                action, value, _ = latest[(doc, f)]
                labeled[f] = value if action == "correct" else values[(doc, f)]
        progress[doc] = (len(labeled), len(fields))
        if fields and len(labeled) == len(fields):
            done[doc] = labeled
    return done, progress


def export_labels(out_dir: Path = EDGAR_DIR, skip: set[str] = frozenset({"tract_id"})) -> int:
    """Write labels.csv and training.jsonl from finished label-mode reviews and
    load them as the answer key. Leases with unlabeled fields are held back."""
    import json

    from .extract import pdf_text

    with connect(must_exist=True) as con:
        extracted = con.execute("""
            with latest as (select document_id, arg_max(extraction_id, extracted_at) as eid
                            from raw.lease_extraction_fields group by 1)
            select e.document_id, e.field_name, e.value from raw.lease_extraction_fields e
            join latest l on l.document_id = e.document_id and l.eid = e.extraction_id""").fetchall()
        reviews = con.execute("""select document_id, field_name, action, corrected_value, reviewed_at
                                 from raw.lease_reviews""").fetchall()
    done, progress = labels_from_reviews(extracted, reviews, FIELD_NAMES, set(skip))
    for doc, (n, total) in progress.items():
        print(f"  {doc}: {n}/{total} fields labeled{'' if n == total else '  (held back)'}")
    if not done:
        print("No fully labeled leases yet.")
        return 0

    sources = {}
    if (out_dir / "manifest.csv").exists():
        sources = {m["document_id"]: m["source_url"] for m in csv.DictReader((out_dir / "manifest.csv").open())}
    labels_path = out_dir / "labels.csv"
    with labels_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["document_id", "source_url", *FIELD_NAMES])
        w.writeheader()
        for doc, fields in done.items():
            row = {f: (SKIP if f in skip else (fields.get(f) or "")) for f in FIELD_NAMES}
            w.writerow({"document_id": doc, "source_url": sources.get(doc, ""), **row})
    with (out_dir / "training.jsonl").open("w") as fh:
        for doc, fields in done.items():
            text, _ = pdf_text(out_dir / "pdfs" / f"{doc}.pdf")
            fh.write(json.dumps({"document_id": doc, "source_url": sources.get(doc), "text": text,
                                 "fields": fields}) + "\n")
    print(f"wrote {labels_path} and training.jsonl for {len(done)} leases")
    return load_labels(labels_path)
