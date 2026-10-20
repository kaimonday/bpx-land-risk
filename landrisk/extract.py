"""Turn lease PDFs into structured, confidence-scored records.

Pipeline per document:
  1. Pull text from the PDF (pypdf; scanned images would need OCR first).
  2. Ask an LLM for every field with a value, a self-reported confidence and a
     verbatim evidence quote.
  3. Normalize types, then run deterministic checks that can only LOWER
     confidence: missing required fields, out-of-range values, evidence that
     does not appear in the document (a hallucination guard), and documents
     with conflicting royalty rates or separate execution/effective dates.
  4. Store one row per field. Anything under the review threshold lands in the
     human review queue.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path

import httpx
from pypdf import PdfReader

from .config import GROQ_API_KEY, GROQ_MODEL, LEASE_PDF_DIR, REVIEW_THRESHOLD
from .db import connect, init_raw
from .fields import FIELD_BY_NAME, FIELDS

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ---------- text ----------

def pdf_text(path: Path) -> tuple[str, int]:
    reader = PdfReader(str(path))
    pages = [p.extract_text() or "" for p in reader.pages]
    return "\n\n".join(pages), len(pages)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


# ---------- providers ----------

def _prompt(text: str) -> list[dict]:
    spec = "\n".join(f'- "{f.name}" ({f.kind}{"" if f.required else ", nullable"}): {f.description}'
                     for f in FIELDS)
    return [
        {"role": "system", "content": (
            "You are an experienced oil and gas land analyst abstracting lease documents. "
            "Extract exactly the requested fields. Use null when a field is absent. "
            "Confidence is your probability (0 to 1) that the value is correct. "
            "Evidence must be copied verbatim from the document (max 300 characters). "
            "Respond with JSON only.")},
        {"role": "user", "content": (
            f"Fields:\n{spec}\n\n"
            'Return: {"fields": {"<field>": {"value": ..., "confidence": 0.0-1.0, "evidence": "..."}}}\n\n'
            f"DOCUMENT:\n{text}")},
    ]


def call_groq(text: str, model: str = GROQ_MODEL, retries: int = 4) -> dict:
    if not GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    body = {"model": model, "messages": _prompt(text), "temperature": 0,
            "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    for attempt in range(retries):
        r = httpx.post(GROQ_URL, json=body, headers=headers, timeout=90)
        if r.status_code == 429 and attempt < retries - 1:
            wait = float(r.headers.get("retry-after", 2 ** (attempt + 1)))
            print(f"  rate limited, waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content).get("fields", {})
    raise RuntimeError("Groq request failed after retries")


def call_mock(document_id: str, text: str, seed: int = 11) -> dict:
    """Offline stand-in for the LLM, built from the generator's answer key.

    It deliberately makes the mistakes real models make (reading the body
    royalty instead of the Exhibit A override, shaky confidence on a few
    fields) so the validation and review flow can be exercised without an API.
    """
    rng = random.Random(f"{seed}-{document_id}")
    with connect(must_exist=True) as con:
        truth = {name: (value, evidence) for name, value, evidence in con.execute(
            "select field_name, value, evidence from raw.lease_ground_truth where document_id = ?",
            [document_id]).fetchall()}
    if not truth:
        raise SystemExit(f"Mock provider needs ground truth for {document_id}; use --provider groq for real documents.")
    out = {}
    for name, (value, evidence) in truth.items():
        conf = round(rng.uniform(0.88, 0.99), 2)
        v = value if value != "" else None
        if name == "royalty_rate" and evidence and evidence.startswith("EXHIBIT A"):
            m = re.search(r"4\. Royalties\..*?(?=\n\n|\s5\.|\s6\.|\s7\.)", text, re.S)
            v, evidence, conf = "0.125", (m.group(0) if m else None), 0.9  # missed the rider
        elif rng.random() < 0.08:
            conf = round(rng.uniform(0.55, 0.8), 2)
        out[name] = {"value": v, "confidence": conf, "evidence": evidence}
    return out


# ---------- normalization + validation ----------

_FRACTION = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def normalize(kind: str, value) -> tuple[str | None, str | None]:
    """Return (normalized string value, problem note)."""
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null", "none", "n/a")):
        return None, None
    s = str(value).strip()
    try:
        if kind == "text":
            return s, None
        if kind == "bool":
            if isinstance(value, bool):
                return str(value).lower(), None
            low = s.lower()
            if low in ("true", "yes", "y"):
                return "true", None
            if low in ("false", "no", "n"):
                return "false", None
            return None, f"unparseable boolean {s!r}"
        if kind == "int":
            return str(int(float(s.replace(",", "")))), None
        if kind == "decimal":
            m = _FRACTION.search(s)
            if m:
                return str(float(Fraction(int(m.group(1)), int(m.group(2))))), None
            m = _PERCENT.search(s)
            if m:
                return str(float(m.group(1)) / 100), None
            return str(float(s.replace(",", "").replace("$", ""))), None
        if kind == "date":
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
                try:
                    return datetime.strptime(s, fmt).date().isoformat(), None
                except ValueError:
                    pass
            return None, f"unparseable date {s!r}"
    except (ValueError, ZeroDivisionError):
        return None, f"unparseable {kind} {s!r}"
    return s, None


def _royalty_rates_in(text: str) -> set[float]:
    rates = set()
    for sentence in re.split(r"(?<=[.;])\s+", text):
        if "royalt" not in sentence.lower():
            continue
        for a, b in _FRACTION.findall(sentence):
            if int(b) in (4, 5, 6, 8, 16, 32):
                rates.add(round(int(a) / int(b), 4))
        for p in _PERCENT.findall(sentence):
            rates.add(round(float(p) / 100, 4))
    return rates


def validate(raw: dict, text: str) -> list[dict]:
    doc = _norm(text)
    royalty_rates = _royalty_rates_in(text)
    split_dates = "executed on" in doc and "effective as of" in doc
    rows = []
    for f in FIELDS:
        item = raw.get(f.name) or {}
        llm_conf = item.get("confidence")
        try:
            llm_conf = max(0.0, min(1.0, float(llm_conf)))
        except (TypeError, ValueError):
            llm_conf = 0.5
        value, problem = normalize(f.kind, item.get("value"))
        evidence = (item.get("evidence") or "").strip() or None
        conf, notes = llm_conf, []
        if problem:
            notes.append(problem)
            conf = min(conf, 0.3)

        if value is None and f.required:
            notes.append("required field missing")
            conf = 0.0
        if value is not None:
            if evidence is None or _norm(evidence)[:120] not in doc:
                notes.append("evidence not found verbatim in document")
                conf *= 0.5
            if f.name == "royalty_rate" and not 0.125 <= float(value) <= 0.30:
                notes.append("royalty outside typical 1/8 to 30% range")
                conf = min(conf, 0.3)
            if f.name == "primary_term_months" and not 6 <= int(value) <= 120:
                notes.append("primary term outside 6 to 120 months")
                conf = min(conf, 0.3)
            if f.name == "gross_acres" and not 0 < float(value) <= 5000:
                notes.append("acreage out of range")
                conf = min(conf, 0.3)
            if f.name == "effective_date" and not date(1950, 1, 1) <= date.fromisoformat(value) <= date.today():
                notes.append("effective date outside 1950 to today")
                conf = min(conf, 0.3)
        if f.name == "royalty_rate" and len(royalty_rates) > 1:
            notes.append(f"document states {len(royalty_rates)} different royalty rates; check riders/exhibits")
            conf = min(conf, 0.6)
        if f.name == "effective_date" and split_dates:
            notes.append("document has separate execution and effective dates")
            conf = min(conf, 0.75)
        rows.append({"field_name": f.name, "value": value, "llm_confidence": round(llm_conf, 3),
                     "confidence": round(conf, 3), "evidence": evidence,
                     "validation_notes": "; ".join(notes) or None})

    # Cross-field: a shut-in clause needs both the payment and the deadline.
    by = {r["field_name"]: r for r in rows}
    pay, days = by["shut_in_payment_per_acre"], by["shut_in_grace_days"]
    if (pay["value"] is None) != (days["value"] is None):
        for r in (pay, days):
            r["confidence"] = min(r["confidence"], 0.5)
            r["validation_notes"] = "; ".join(filter(None, [r["validation_notes"],
                                              "shut-in payment and deadline must both be present"]))
    return rows


# ---------- runner ----------

def extract_all(provider: str = "groq", only: list[str] | None = None) -> int:
    init_raw()
    pdfs = sorted(LEASE_PDF_DIR.glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if p.stem in only]
    if not pdfs:
        raise SystemExit(f"No PDFs in {LEASE_PDF_DIR}. Run `python cli.py generate` first.")

    model = GROQ_MODEL if provider == "groq" else "mock"
    done = 0
    for path in pdfs:
        doc_id = path.stem
        text, pages = pdf_text(path)
        raw = call_groq(text) if provider == "groq" else call_mock(doc_id, text)
        rows = validate(raw, text)
        ext_id = str(uuid.uuid4())
        stamp = datetime.now()  # one timestamp for the whole run
        with connect() as con:
            con.execute("delete from raw.lease_documents where document_id = ?", [doc_id])
            con.execute("insert into raw.lease_documents values (?, ?, ?, ?, ?)",
                        [doc_id, path.name, pages, len(text), stamp])
            con.executemany(
                "insert into raw.lease_extraction_fields values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [[ext_id, doc_id, provider, model, r["field_name"], r["value"], r["llm_confidence"],
                  r["confidence"], r["evidence"], r["validation_notes"], stamp] for r in rows])
        low = sum(1 for r in rows if r["confidence"] < REVIEW_THRESHOLD)
        print(f"  {doc_id}: {len(rows)} fields, {low} below review threshold")
        done += 1
    print(f"Extracted {done} documents with provider={provider}")
    return done
