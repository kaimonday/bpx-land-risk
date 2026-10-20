"""FastAPI service over the DuckDB warehouse.

Run with `python cli.py serve`. DuckDB allows one writer at a time, so every
request opens a short-lived connection under a process-wide lock, and dbt
rebuilds run under the same lock.
"""
from __future__ import annotations

import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from landrisk.build import run_dbt
from landrisk.config import DB_PATH, GROQ_API_KEY, LEASE_PDF_DIR, REVIEW_THRESHOLD
from landrisk.db import connect, init_raw
from landrisk.extract import extract_all, normalize
from landrisk.fields import FIELD_BY_NAME, FIELDS

app = FastAPI(title="bpx land risk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (3000, 3001)],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
DOC_ID = re.compile(r"^[A-Z]{3}-[0-9A-Za-z_-]{1,40}$")


@contextmanager
def db():
    with _lock:
        con = connect()
        try:
            yield con
        finally:
            con.close()


def rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _check_id(document_id: str) -> str:
    if not DOC_ID.match(document_id):
        raise HTTPException(400, "Invalid document id")
    return document_id


@app.on_event("startup")
def _startup() -> None:
    init_raw()


@app.get("/api/health")
def health():
    return {"ok": True, "database": DB_PATH.name}


@app.get("/api/summary")
def summary():
    with db() as con:
        try:
            sev = rows(con, "select severity, count(*) as n from main.lease_risk group by 1")
            flags = rows(con, """select flag, count(*) as n
                                 from (select unnest(risk_flags) as flag from main.lease_risk) group by 1""")
            meta = rows(con, """select count(*) as leases, max(latest_reported_month) as latest_reported_month,
                                       max(as_of_date) as as_of_date,
                                       sum(gross_acres) filter (where severity = 'high') as high_risk_acres
                                from main.lease_risk""")[0]
            queue = con.execute("select count(*) from main.review_queue").fetchone()[0]
            operators = [r["operator_name"] for r in rows(con, "select operator_name from raw.rrc_operators order by 1")]
        except Exception as exc:  # marts not built yet
            raise HTTPException(503, f"Warehouse not built yet: run `python cli.py demo`. ({exc})")
    return {**meta, "by_severity": {r["severity"]: r["n"] for r in sev},
            "by_flag": {r["flag"]: r["n"] for r in flags},
            "review_queue": queue, "operators": operators, "review_threshold": REVIEW_THRESHOLD}


@app.get("/api/risks")
def risks(severity: str | None = Query(None, pattern="^(high|medium|low|none)$")):
    sql = "select * from main.lease_risk"
    params = []
    if severity:
        sql += " where severity = ?"
        params.append(severity)
    sql += " order by severity_rank desc, next_deadline nulls last, document_id"
    with db() as con:
        return rows(con, sql, params)


@app.get("/api/leases/{document_id}")
def lease(document_id: str):
    _check_id(document_id)
    with db() as con:
        risk = rows(con, "select * from main.lease_risk where document_id = ?", [document_id])
        if not risk:
            raise HTTPException(404, "Lease not found")
        fields = rows(con, """select field_name, extracted_value, final_value, confidence, llm_confidence,
                                     evidence, validation_notes, field_status, review_action, reviewer, reviewed_at
                              from main.int_lease_fields_resolved where document_id = ?""", [document_id])
        production = rows(con, """select production_month, liquids_bbl, gas_mcf, boe
                                  from main.lease_production_monthly where document_id = ?
                                  order by production_month""", [document_id])
        wells = rows(con, """select w.api_number, w.well_no, w.county, w.rrc_lease_key
                             from main.stg_land__tract_xref x
                             join main.stg_rrc__wells w using (rrc_lease_key)
                             where x.tract_id = (select tract_id from main.leases where document_id = ?)
                             order by w.api_number""", [document_id])
    order = {f.name: i for i, f in enumerate(FIELDS)}
    fields.sort(key=lambda f: order.get(f["field_name"], 99))
    return {"risk": risk[0], "fields": fields, "production": production, "wells": wells}


@app.get("/api/review-queue")
def review_queue():
    with db() as con:
        return rows(con, "select * from main.review_queue order by document_id, confidence")


@app.get("/api/label-fields")
def label_fields(skip: str = Query("", max_length=200)):
    """Every field of every document with its latest decision, for labeling.

    Reads the raw tables directly, so new decisions show up without a dbt
    rebuild. `skip` is a comma-separated list of fields to leave out."""
    skipped = [f for f in skip.split(",") if f in FIELD_BY_NAME]
    with db() as con:
        out = rows(con, """
            with latest as (select document_id, arg_max(extraction_id, extracted_at) as eid
                            from raw.lease_extraction_fields group by 1),
            decisions as (select document_id, field_name, arg_max(action, reviewed_at) as review_action,
                                 arg_max(corrected_value, reviewed_at) as corrected_value
                          from raw.lease_reviews group by 1, 2)
            select e.document_id, e.field_name, e.value as extracted_value, e.confidence, e.llm_confidence,
                   e.evidence, e.validation_notes, d.review_action, d.corrected_value
            from raw.lease_extraction_fields e
            join latest l on l.document_id = e.document_id and l.eid = e.extraction_id
            left join decisions d on d.document_id = e.document_id and d.field_name = e.field_name
            order by e.document_id""")
    order = {f.name: i for i, f in enumerate(FIELDS)}
    out = [r for r in out if r["field_name"] not in skipped]
    out.sort(key=lambda r: (r["document_id"], order.get(r["field_name"], 99)))
    return out


class ReviewIn(BaseModel):
    document_id: str
    field_name: str
    action: Literal["accept", "correct", "absent"]  # absent: the lease doesn't state this field
    corrected_value: str | None = None
    reviewer: str = Field(default="demo-user", max_length=80)
    note: str | None = Field(default=None, max_length=500)


@app.post("/api/reviews")
def add_review(review: ReviewIn):
    _check_id(review.document_id)
    field = FIELD_BY_NAME.get(review.field_name)
    if not field:
        raise HTTPException(400, "Unknown field")
    value, action = None, review.action
    if action == "absent":
        action = "correct"  # stored as a correction to null
    elif action == "correct":
        value, problem = normalize(field.kind, review.corrected_value)
        if problem or (value is None and field.required):
            raise HTTPException(422, problem or "A corrected value is required for this field")
    with db() as con:
        con.execute("insert into raw.lease_reviews values (?, ?, ?, ?, ?, ?, ?, ?)",
                    [str(uuid.uuid4()), review.document_id, review.field_name, action,
                     value, review.reviewer, review.note, datetime.now()])
    return {"ok": True, "stored_value": value, "note": "Run /api/refresh to apply reviews to the risk models."}


@app.post("/api/refresh")
def refresh():
    with _lock:
        ok = run_dbt("build")
    if not ok:
        raise HTTPException(500, "dbt build failed; check the server log")
    return {"ok": True}


@app.get("/api/documents/{document_id}/pdf")
def document_pdf(document_id: str):
    _check_id(document_id)
    path = LEASE_PDF_DIR / f"{document_id}.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF not found")
    return FileResponse(path, media_type="application/pdf")


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    """Extract a newly uploaded lease PDF with Groq and rebuild the models."""
    if not GROQ_API_KEY:
        raise HTTPException(400, "Uploads use Groq; set GROQ_API_KEY in .env")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF")
    stem = re.sub(r"[^0-9A-Za-z_-]", "", (file.filename or "lease").rsplit(".", 1)[0])[:30] or "lease"
    document_id = f"UPL-{stem}"
    LEASE_PDF_DIR.mkdir(parents=True, exist_ok=True)
    (LEASE_PDF_DIR / f"{document_id}.pdf").write_bytes(file.file.read())
    with _lock:
        extract_all(provider="groq", only=[document_id])
        ok = run_dbt("build")
    return {"ok": ok, "document_id": document_id}
