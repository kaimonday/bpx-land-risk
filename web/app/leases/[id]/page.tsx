"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  api, FIELD_LABELS, FLAG_LABELS, STATUS_LABELS, fmtDate, fmtMonth, fmtNumber, fmtRoyalty,
  type FieldRow, type LeaseDetail, leaseName } from "@/lib/api";
import ProductionChart from "./production-chart";

function Confidence({ value, threshold = 0.85 }: { value: number; threshold?: number }) {
  return (
    <span className="conf" data-low={value < threshold}>
      <span className="bar"><span style={{ width: `${Math.round(value * 100)}%` }} /></span>
      {Math.round(value * 100)}%
    </span>
  );
}

function fieldValue(f: FieldRow) {
  const v = f.final_value;
  if (v == null || v === "") return <span className="muted">None</span>;
  if (f.field_name === "royalty_rate") return fmtRoyalty(Number(v));
  if (f.field_name === "effective_date") return fmtDate(v);
  if (f.field_name === "pugh_clause") return v === "true" ? "Yes" : "No";
  return v;
}

const STATUS_TEXT: Record<FieldRow["field_status"], string> = {
  reviewed: "Reviewed", auto_accepted: "Auto-accepted", needs_review: "Needs review",
};

export default function LeasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<LeaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.lease(id).then(setData).catch((e: Error) => setError(e.message));
  }, [id]);

  if (error) return <div className="notice error"><p>Couldn't load {id}: {error}</p></div>;
  if (!data) return <p className="muted">Loading lease…</p>;
  const r = data.risk;

  return (
    <>
      <p className="small"><Link href="/">All leases</Link></p>
      <div className="page-head" style={{ marginTop: "0.5rem" }}>
        <div>
          <h1>{leaseName(r)}</h1>
          <p className="muted">{r.county} County, {fmtNumber(r.gross_acres, 2)} gross acres. Document {r.document_id}.</p>
        </div>
        <a className="button" href={api.pdfUrl(r.document_id)} target="_blank" rel="noreferrer">Open lease PDF</a>
      </div>

      <div className="action-callout" data-severity={r.severity}>
        <p><strong>{r.recommended_action}</strong></p>
        {r.risk_flags.length > 0 && (
          <p className="small muted" style={{ marginTop: "0.3rem" }}>{r.risk_flags.map((f) => FLAG_LABELS[f] ?? f).join(", ")}</p>
        )}
      </div>

      <dl className="facts">
        <div><dt>Status</dt><dd>{STATUS_LABELS[r.lease_status]}</dd></div>
        <div><dt>Primary term ends</dt><dd>{fmtDate(r.primary_term_end)}</dd></div>
        <div><dt>Royalty</dt><dd>{fmtRoyalty(r.royalty_rate)}</dd></div>
        <div><dt>Last production</dt><dd>{r.last_production_month ? fmtMonth(r.last_production_month) : "None"}</dd></div>
        <div><dt>Shut-in payment due</dt><dd>{r.shut_in_due_date ? fmtDate(r.shut_in_due_date) : "Not triggered"}</dd></div>
        <div><dt>Cumulative production</dt><dd>{fmtNumber(r.cumulative_boe)} BOE</dd></div>
      </dl>

      <div className="detail-grid">
        <section>
          <h2>Production</h2>
          <ProductionChart data={data.production} />
          {data.wells.length > 0 && (
            <>
              <h3 style={{ margin: "1.5rem 0 0.5rem" }}>Wells on the linked RRC leases</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>API number</th><th>Well</th><th>RRC lease</th></tr></thead>
                  <tbody>
                    {data.wells.map((w) => (
                      <tr key={w.api_number}><td>{w.api_number}</td><td>{w.well_no}</td><td>{w.rrc_lease_key}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

        <section style={{ marginTop: 0 }}>
          <h2>Abstracted terms</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Field</th><th>Value</th><th>Confidence</th><th>Status</th></tr></thead>
              <tbody>
                {data.fields.map((f) => (
                  <tr key={f.field_name}>
                    <td>{FIELD_LABELS[f.field_name] ?? f.field_name}</td>
                    <td>{fieldValue(f)}</td>
                    <td><Confidence value={f.confidence} /></td>
                    <td>
                      {f.field_status === "needs_review"
                        ? <Link href={`/review?doc=${r.document_id}`}>{STATUS_TEXT[f.field_status]}</Link>
                        : STATUS_TEXT[f.field_status]}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}
