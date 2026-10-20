"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  api, FLAG_LABELS, STATUS_LABELS, fmtDate, fmtMonth, fmtNumber, fmtRoyalty,
  type LeaseRisk, type Severity, type Summary,
} from "@/lib/api";

const SEVERITY_LABEL: Record<Severity, string> = { high: "Act now", medium: "Watch", low: "Verify data", none: "Clear" };

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [risks, setRisks] = useState<LeaseRisk[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Severity | "all">("all");

  useEffect(() => {
    Promise.all([api.summary(), api.risks()])
      .then(([s, r]) => { setSummary(s); setRisks(r); })
      .catch((e: Error) => setError(e.message));
  }, []);

  const urgent = useMemo(
    () => (risks ?? []).filter((r) => r.severity === "high" || r.severity === "medium"),
    [risks],
  );
  const shown = useMemo(
    () => (risks ?? []).filter((r) => filter === "all" || r.severity === filter),
    [risks, filter],
  );
  const plat = useMemo(
    () => [...(risks ?? [])].sort((a, b) => (a.tract_id ?? "").localeCompare(b.tract_id ?? "")),
    [risks],
  );

  if (error) {
    return (
      <div className="notice error">
        <p><strong>The API isn't reachable.</strong> Start it with <code>python cli.py serve</code>, and if this is a
          fresh checkout run <code>python cli.py demo</code> first. Details: {error}</p>
      </div>
    );
  }
  if (!summary || !risks) return <p className="muted">Loading leases…</p>;

  const high = summary.by_severity.high ?? 0;
  const medium = summary.by_severity.medium ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{high} of {summary.leases} leases need action now</h1>
          <p className="muted">
            {medium} more to watch. {fmtNumber(summary.high_risk_acres)} gross acres at risk. Production through{" "}
            {fmtMonth(summary.latest_reported_month)} (latest RRC report), checked {fmtDate(summary.as_of_date)}.
          </p>
        </div>
        <Link href="/review" className="button">Review {summary.review_queue} extracted fields</Link>
      </div>

      <div className="plat-layout">
        <div>
          <div className="plat" role="list" aria-label="Tracts by risk">
            {plat.map((r) => (
              <Link
                key={r.document_id}
                href={`/leases/${r.document_id}`}
                role="listitem"
                className="tract"
                data-severity={r.severity}
                data-unverified={r.fields_needing_review > 0}
                aria-label={`${r.tract_id}, ${SEVERITY_LABEL[r.severity]}: ${r.recommended_action}`}
              >
                <span className="tract-id">{r.tract_id ?? r.document_id}</span>
                <span className="lessor">{r.lessor}</span>
                <span className="acres">{fmtNumber(r.gross_acres, 0)} acres, {r.county}</span>
              </Link>
            ))}
          </div>
          <div className="legend">
            <span><span className="swatch" style={{ background: "var(--high-tint)" }} />Act now</span>
            <span><span className="swatch" style={{ background: "var(--medium-tint)" }} />Watch</span>
            <span><span className="swatch" style={{ background: "var(--low-tint)" }} />Data unverified</span>
            <span><span className="swatch" style={{ background: "var(--none-tint)" }} />Clear</span>
            <span><span className="swatch hatch" />Has fields awaiting review</span>
          </div>
        </div>

        <aside aria-labelledby="act-heading">
          <h2 id="act-heading">What to do next</h2>
          {urgent.length === 0 ? (
            <p className="muted" style={{ marginTop: "0.5rem" }}>Nothing urgent. Every lease is producing or well inside its primary term.</p>
          ) : (
            <ol className="actions" style={{ marginTop: "0.75rem" }}>
              {urgent.map((r) => (
                <li key={r.document_id} data-severity={r.severity}>
                  <div className="when">
                    {r.next_deadline ? fmtDate(r.next_deadline) : SEVERITY_LABEL[r.severity]}
                  </div>
                  <Link href={`/leases/${r.document_id}`}>{r.tract_id}, {r.lessor}</Link>
                  <p className="small">{r.recommended_action}</p>
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>

      <div className="filters">
        <h2>All leases</h2>
        {(["all", "high", "medium", "low", "none"] as const).map((s) => (
          <button key={s} className="chip" aria-pressed={filter === s} onClick={() => setFilter(s)}>
            {s === "all" ? `All (${risks.length})` : `${SEVERITY_LABEL[s]} (${summary.by_severity[s] ?? 0})`}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Tract</th><th>Lessor</th><th>County</th><th>Status</th><th className="num">Acres</th>
              <th>Royalty</th><th>Last production</th><th>Flags</th><th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.document_id}>
                <td><Link href={`/leases/${r.document_id}`}>{r.tract_id ?? r.document_id}</Link></td>
                <td>{r.lessor}</td>
                <td>{r.county}</td>
                <td>{STATUS_LABELS[r.lease_status]}</td>
                <td className="num">{fmtNumber(r.gross_acres, 2)}</td>
                <td>{fmtRoyalty(r.royalty_rate)}</td>
                <td>{r.last_production_month ? fmtMonth(r.last_production_month) : <span className="muted">None</span>}</td>
                <td>{r.risk_flags.map((f) => FLAG_LABELS[f] ?? f).join(", ")}</td>
                <td><span className="sev" data-severity={r.severity}>{SEVERITY_LABEL[r.severity]}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
