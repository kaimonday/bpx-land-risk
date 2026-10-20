"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtDate, fmtNumber, fmtYm, type LinkCandidate, type LinkDetail, type LinkLease } from "@/lib/api";

const STATUS_TEXT: Record<LinkLease["status"], (n: number) => string> = {
  linked: (n) => `Linked (${n})`,
  no_match: () => "No match",
  open: () => "Open",
};

// Yearly production as a small line, so a candidate's history reads at a glance.
function Spark({ series }: { series: [number, number][] }) {
  if (series.length < 2 || series.every(([, b]) => b <= 0)) return null;
  const w = 180, h = 34;
  const years = series.map(([y]) => y), max = Math.max(...series.map(([, b]) => b), 1);
  const x0 = Math.min(...years), x1 = Math.max(...years);
  const pts = series.map(([y, b]) => `${((y - x0) / Math.max(x1 - x0, 1)) * w},${h - (b / max) * (h - 2) - 1}`).join(" ");
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
         aria-label={`Production ${x0} to ${x1}, peak ${fmtNumber(max)} BOE a year`}>
      <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function CandidateCard({ c, selected, onToggle }: { c: LinkCandidate; selected: boolean; onToggle: () => void }) {
  return (
    <div className="cand-card" data-selected={selected || undefined}>
      <div className="cand-head">
        <div>
          <div className="value">{c.lease_name ?? c.key}</div>
          <div className="small muted">
            {c.key} · {c.operator_name ?? "operator unknown"}{c.county ? ` · ${c.county.charAt(0)}${c.county.slice(1).toLowerCase()}` : ""}
          </div>
        </div>
        {selected ? (
          <div className="cand-picked">
            <span className="done">✓ Included</span>
            <button onClick={onToggle} aria-label={`Remove ${c.lease_name ?? c.key}`}>Remove</button>
          </div>
        ) : (
          <button onClick={onToggle}>This one</button>
        )}
      </div>
      <div className="cand-body">
        <Spark series={c.annual_boe} />
        <div className="small">
          {c.first_prod_ym ? `Produced ${fmtYm(c.first_prod_ym)} to ${fmtYm(c.last_prod_ym)}` : "No production on record"}
          {c.score != null && <span className="muted"> · match {Math.round(c.score * 100)}%</span>}
        </div>
      </div>
      <div className="small muted">{c.why}</div>
      {c.linked_to.length > 0 && (
        <div className="note" style={{ color: "var(--high)" }}>
          Already linked to {c.linked_to.join(", ")}. Its production can only count for one lease.
        </div>
      )}
    </div>
  );
}

function LinkScreen() {
  const params = useSearchParams();
  const [leases, setLeases] = useState<LinkLease[] | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(params.get("doc"));
  const [detail, setDetail] = useState<LinkDetail | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LinkCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadLeases = useCallback(() => {
    api.linkLeases().then(setLeases).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(loadLeases, [loadLeases]);

  const current = selectedDoc ?? leases?.find((l) => l.status === "open")?.document_id ?? leases?.[0]?.document_id ?? null;

  useEffect(() => {
    if (!current) return;
    setDetail(null); setResults(null); setQuery(""); setMessage(null);
    api.linkLease(current)
      .then((d) => { setDetail(d); setPicked(new Set(d.linked)); })
      .catch((e: Error) => setError(e.message));
  }, [current]);

  const counts = useMemo(() => {
    const c = { linked: 0, no_match: 0, open: 0 };
    for (const l of leases ?? []) c[l.status] += 1;
    return c;
  }, [leases]);

  const toggle = (key: string) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const save = async (noMatch: boolean) => {
    if (!current) return;
    setBusy(true); setError(null);
    try {
      const res = await api.linkSave(current, { rrc_leases: noMatch ? [] : [...picked], no_match: noMatch });
      setSaved((n) => n + 1);
      setMessage(noMatch ? "Saved: nothing on this land produces." : `Saved: linked to ${res.linked.length} RRC lease${res.linked.length === 1 ? "" : "s"}.`);
      setDetail((d) => d && { ...d, linked: res.linked, decision: noMatch ? "no_match" : res.linked.length ? "linked" : "cleared" });
      loadLeases();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim().length < 2) return;
    try { setResults(await api.linkSearch(query.trim(), detail?.lease.county)); }
    catch (err) { setError((err as Error).message); }
  };

  const refresh = async () => {
    setBusy(true); setError(null);
    try { await api.refresh(); setSaved(0); setMessage("Dashboard updated with the new links."); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  if (error && !leases) return <div className="notice error"><p>{error}</p></div>;
  if (!leases) return <p className="muted">Loading leases…</p>;

  const shown = new Set(detail?.candidates.map((c) => c.key));
  const unchanged = detail && picked.size === detail.linked.length && detail.linked.every((k) => picked.has(k));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Link leases to RRC production</h1>
          <p className="muted">
            {counts.linked} linked, {counts.no_match} with no match, {counts.open} open. Find the RRC lease (or leases)
            whose wells sit on each lease&apos;s land, using the land description and the lease PDF.
          </p>
        </div>
        <button className="primary" onClick={refresh} disabled={busy || saved === 0}>
          {busy && saved ? "Updating…" : `Update dashboard${saved ? ` (${saved})` : ""}`}
        </button>
      </div>
      {message && <div className="notice"><p>{message}</p></div>}
      {error && <div className="notice error"><p>{error}</p></div>}

      <div className="review-layout">
        <nav aria-label="Leases">
          <ul className="doc-list">
            {leases.map((l) => (
              <li key={l.document_id}>
                <button aria-current={l.document_id === current} onClick={() => setSelectedDoc(l.document_id)}>
                  <span>{l.document_id}</span>
                  <span className={l.status === "open" ? "muted" : undefined}>{STATUS_TEXT[l.status](l.linked_count)}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        {current && <iframe className="pdf-frame" title={`Lease document ${current}`} src={api.pdfUrl(current)} />}

        <div>
          {!detail ? <p className="muted">Loading candidates…</p> : (
            <>
              <div className="field-card">
                <div className="small muted">Lease</div>
                <div className="value">{detail.lease.lessor ?? detail.lease.document_id}</div>
                <div className="small">
                  To {detail.lease.lessee ?? "unknown lessee"} · {detail.lease.county ?? "county unknown"} County
                  {detail.lease.effective_date ? ` · effective ${fmtDate(detail.lease.effective_date)}` : ""}
                </div>
                <div className="small muted" style={{ marginTop: "0.6rem" }}>Land description</div>
                {detail.land.text ? <blockquote>{detail.land.text}</blockquote>
                  : <p className="small muted">Not found in the text; check the PDF.</p>}
                {(detail.land.abstracts.length > 0 || detail.land.surveys.length > 0 || detail.land.acres) && (
                  <div className="chips">
                    {detail.land.abstracts.map((a) => <span key={a} className="chip-static">{a}</span>)}
                    {detail.land.surveys.map((s) => <span key={s} className="chip-static">{s}</span>)}
                    {detail.land.acres && <span className="chip-static">{fmtNumber(Number(detail.land.acres), 2)} acres</span>}
                  </div>
                )}
                <p className="small muted" style={{ marginTop: "0.5rem" }}>
                  To check a match, find these on the RRC GIS Viewer and compare the wells there with the candidates below.
                </p>
              </div>

              <h2 className="small-heading">
                Candidates in {detail.lease.county ?? "this"} County
                <span className="small muted"> · best of {fmtNumber(detail.county_rrc_leases)} RRC leases</span>
              </h2>
              {detail.candidates.length === 0 && <p className="small muted">No RRC leases loaded for this county.</p>}
              {detail.candidates.map((c) => (
                <CandidateCard key={c.key} c={c} selected={picked.has(c.key)} onToggle={() => toggle(c.key)} />
              ))}

              <form className="link-search" onSubmit={search}>
                <label className="small muted" htmlFor="rrc-search">Not listed? Search RRC leases by name or number</label>
                <div className="row">
                  <input id="rrc-search" type="text" value={query} onChange={(e) => setQuery(e.target.value)}
                         placeholder="e.g. Garner or 18751" />
                  <button type="submit" disabled={query.trim().length < 2}>Search</button>
                </div>
              </form>
              {results && results.length === 0 && <p className="small muted">No RRC leases match.</p>}
              {results?.filter((c) => !shown.has(c.key)).map((c) => (
                <CandidateCard key={c.key} c={c} selected={picked.has(c.key)} onToggle={() => toggle(c.key)} />
              ))}

              <div className="link-actions">
                <button className="primary" onClick={() => save(false)} disabled={busy || picked.size === 0 || !!unchanged}>
                  {picked.size ? `Save ${picked.size} link${picked.size === 1 ? "" : "s"}` : "Select an RRC lease"}
                </button>
                <button onClick={() => save(true)} disabled={busy || detail.decision === "no_match"}>No match</button>
                {detail.decision && (
                  <span className="small muted">
                    {detail.decision === "no_match" ? "Marked: no match" : detail.linked.length ? `Linked to ${detail.linked.length}` : ""}
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

export default function LinkPage() {
  return (
    <Suspense fallback={<p className="muted">Loading leases…</p>}>
      <LinkScreen />
    </Suspense>
  );
}
