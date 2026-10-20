"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, FIELD_LABELS, fmtDate, fmtRoyalty, type QueueRow, type ReviewAction } from "@/lib/api";

type Decision = { action: ReviewAction; value: string | null };

function decisionText(d: Decision): string {
  if (d.action === "accept") return "Accepted";
  if (d.action === "absent" || d.value == null) return "Marked not in lease";
  return `Corrected to ${d.value}`;
}

// A decision already saved for this field (label mode loads these with the fields).
function savedDecision(r: QueueRow): Decision | undefined {
  if (!r.review_action) return undefined;
  if (r.review_action === "accept") return { action: "accept", value: null };
  return { action: r.corrected_value == null ? "absent" : "correct", value: r.corrected_value ?? null };
}

function display(row: QueueRow): string {
  const v = row.extracted_value;
  if (v == null || v === "") return "Not found";
  if (row.field_name === "royalty_rate") return fmtRoyalty(Number(v));
  if (row.field_name === "effective_date") return fmtDate(v);
  if (row.field_name === "pugh_clause") return v === "true" ? "Yes" : "No";
  return v;
}

function FieldCard({ row, decision, onDecide, labeling }: {
  row: QueueRow; decision?: Decision; onDecide: (d: Decision) => Promise<void>; labeling: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.extracted_value ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = FIELD_LABELS[row.field_name] ?? row.field_name;

  const decide = async (d: Decision) => {
    setBusy(true); setError(null);
    try { await onDecide(d); setEditing(false); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="field-card">
      <div className="small muted">{label}, {Math.round(row.confidence * 100)}% confidence</div>
      <div className="value">{display(row)}</div>
      {row.validation_notes && <div className="note">{row.validation_notes}</div>}
      {row.evidence && <blockquote>{row.evidence}</blockquote>}
      {decision ? (
        <div className="row">
          <span className="done">{decisionText(decision)}</span>
          <button className="chip" onClick={() => setEditing(true)} disabled={busy}>Change</button>
        </div>
      ) : null}
      {(!decision || editing) && (
        editing ? (
          <form className="row" onSubmit={(e) => { e.preventDefault(); decide({ action: "correct", value }); }}>
            <label className="visually-hidden" htmlFor={`v-${row.document_id}-${row.field_name}`}>Correct {label}</label>
            <input id={`v-${row.document_id}-${row.field_name}`} type="text" value={value}
                   onChange={(e) => setValue(e.target.value)} autoFocus
                   placeholder={row.field_name === "royalty_rate" ? "e.g. 1/4 or 0.25" : undefined} />
            <button type="submit" className="primary" disabled={busy}>Save correction</button>
            <button type="button" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
          </form>
        ) : (
          <div className="row">
            <button className="primary" onClick={() => decide({ action: "accept", value: null })} disabled={busy}>Accept</button>
            <button onClick={() => setEditing(true)} disabled={busy}>Correct</button>
            {labeling && (
              <button onClick={() => decide({ action: "absent", value: null })} disabled={busy}>Not in lease</button>
            )}
          </div>
        )
      )}
      {error && <div className="note" role="alert" style={{ color: "var(--high)" }}>{error}</div>}
    </div>
  );
}

function ReviewQueue() {
  const params = useSearchParams();
  const labeling = params.get("mode") === "label";
  const [queue, setQueue] = useState<QueueRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(params.get("doc"));
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);

  const load = useCallback(() => {
    (labeling ? api.labelFields() : api.queue())
      .then((q) => {
        setQueue(q);
        const saved: Record<string, Decision> = {};
        for (const r of q) {
          const d = savedDecision(r);
          if (d) saved[`${r.document_id}/${r.field_name}`] = d;
        }
        setDecisions(saved);
      })
      .catch((e: Error) => setError(e.message));
  }, [labeling]);
  useEffect(load, [load]);

  const docs = useMemo(() => {
    const m = new Map<string, QueueRow[]>();
    for (const r of queue ?? []) m.set(r.document_id, [...(m.get(r.document_id) ?? []), r]);
    return [...m.entries()];
  }, [queue]);

  const current = selected && docs.some(([d]) => d === selected) ? selected : docs[0]?.[0] ?? null;
  const rows = docs.find(([d]) => d === current)?.[1] ?? [];
  const decided = Object.keys(decisions).length;
  const key = (r: QueueRow) => `${r.document_id}/${r.field_name}`;

  const apply = async () => {
    setApplying(true); setApplied(null);
    try { await api.refresh(); setApplied(`Applied ${decided} reviews. Risk flags are up to date.`); load(); }
    catch (e) { setError((e as Error).message); }
    finally { setApplying(false); }
  };

  if (error) return <div className="notice error"><p>{error}</p></div>;
  if (!queue) return <p className="muted">Loading review queue…</p>;

  const finished = docs.filter(([, items]) => items.every((r) => decisions[key(r)])).length;

  return (
    <>
      <nav className="mode-toggle" aria-label="Review mode">
        <Link aria-current={labeling ? undefined : "page"} href="/review">Needs review</Link>
        <Link aria-current={labeling ? "page" : undefined} href="/review?mode=label">Label all fields</Link>
      </nav>
      <div className="page-head">
        {labeling ? (
          <div>
            <h1>{decided} of {queue.length} fields labeled</h1>
            <p className="muted">
              Check every field against the lease, including confident ones, so the labels can score the extractor.
              Use Not in lease when the document doesn&apos;t state a field. Labels save as you go; {finished} of{" "}
              {docs.length} leases are complete. When you&apos;re done, run <code>export-labels</code>.
            </p>
          </div>
        ) : (
          <div>
            <h1>{queue.length === 0 ? "Review queue is clear" : `${queue.length} fields need a second look`}</h1>
            <p className="muted">
              These fields scored below the confidence threshold, or failed a check against the document.
              Compare each with the lease, then accept or correct it.
            </p>
          </div>
        )}
        {!labeling && (
          <button className="primary" onClick={apply} disabled={decided === 0 || applying}>
            {applying ? "Applying reviews…" : `Apply ${decided} review${decided === 1 ? "" : "s"}`}
          </button>
        )}
      </div>
      {applied && <div className="notice"><p>{applied}</p></div>}

      {queue.length === 0 ? (
        <p>Every extracted field is either confident or reviewed. New leases will show up here after extraction.</p>
      ) : (
        <div className="review-layout">
          <nav aria-label="Documents">
            <ul className="doc-list">
              {docs.map(([doc, items]) => {
                const left = items.filter((r) => !decisions[key(r)]).length;
                return (
                  <li key={doc}>
                    <button aria-current={doc === current} onClick={() => setSelected(doc)}>
                      <span>{doc}</span><span className="muted">{left === 0 ? "Done" : `${left} left`}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
          {current && (
            <iframe className="pdf-frame" title={`Lease document ${current}`} src={api.pdfUrl(current)} />
          )}
          <div>
            {rows.map((r) => (
              <FieldCard
                key={key(r)}
                row={r}
                labeling={labeling}
                decision={decisions[key(r)]}
                onDecide={async (d) => {
                  const res = await api.review({
                    document_id: r.document_id, field_name: r.field_name, action: d.action,
                    corrected_value: d.value ?? undefined,
                  });
                  setDecisions((prev) => ({ ...prev, [key(r)]: { ...d, value: res.stored_value } }));
                }}
              />
            ))}
          </div>
        </div>
      )}
    </>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<p className="muted">Loading review queue…</p>}>
      <ReviewQueue />
    </Suspense>
  );
}
