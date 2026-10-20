"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, FIELD_LABELS, fmtDate, fmtRoyalty, type Alternative, type QueueRow, type ReviewAction } from "@/lib/api";

type Decision = { action: ReviewAction; value: string | null };

const BOOL_FIELDS = new Set(["pugh_clause"]);

// The same rule the extractor is given (landrisk/fields.py), so labels and model agree on terms.
const FIELD_HELP: Record<string, string> = {
  pugh_clause:
    "Yes only if the lease itself ends, after the primary term, as to acreage or depths that aren't producing " +
    "or in a pooled unit. No if there's no such clause. A voluntary release clause (\"Lessee may at any time… " +
    "deliver a release\") and a shut-in clause are not Pugh clauses.",
};

function decisionText(d: Decision, field: string): string {
  if (d.action === "accept") return "Accepted";
  if (d.action === "absent" || d.value == null) return "Marked not in lease";
  return `Labeled ${displayValue(field, d.value)}`;
}

// A decision already saved for this field (label mode loads these with the fields).
function savedDecision(r: QueueRow): Decision | undefined {
  if (!r.review_action) return undefined;
  if (r.review_action === "accept") return { action: "accept", value: null };
  return { action: r.corrected_value == null ? "absent" : "correct", value: r.corrected_value ?? null };
}

// "1/8", "12.5%" and "0.125" are the same royalty.
function toNumber(v: string): number {
  const frac = v.match(/(\d+)\s*\/\s*(\d+)/);
  if (frac) return Number(frac[1]) / Number(frac[2]);
  const pct = v.match(/([\d.]+)\s*%/);
  if (pct) return Number(pct[1]) / 100;
  return Number(v.replace(/[,$]/g, ""));
}

function displayValue(field: string, raw: Alternative["value"] | string | null | undefined): string {
  if (raw == null || raw === "") return "Not found";
  const v = String(raw);
  if (field === "royalty_rate") return fmtRoyalty(toNumber(v));
  if (field === "effective_date") return fmtDate(v);
  if (field === "pugh_clause") return ["true", "yes"].includes(v.toLowerCase()) ? "Yes" : "No";
  return v;
}

const display = (row: QueueRow) => displayValue(row.field_name, row.extracted_value);

// Blind labeling: record your own answer before seeing the model's, so the
// labels measure the model instead of agreeing with it.
function BlindForm({ row, label, busy, decide, onCancel }: {
  row: QueueRow; label: string; busy: boolean; decide: (d: Decision) => Promise<void>; onCancel?: () => void;
}) {
  const [value, setValue] = useState("");
  const id = `b-${row.document_id}-${row.field_name}`;
  const absent = <button type="button" onClick={() => decide({ action: "absent", value: null })} disabled={busy}>Not in lease</button>;
  const cancel = onCancel && <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>;
  if (BOOL_FIELDS.has(row.field_name)) {
    return (
      <div className="row">
        <button className="primary" onClick={() => decide({ action: "correct", value: "true" })} disabled={busy}>Yes</button>
        <button className="primary" onClick={() => decide({ action: "correct", value: "false" })} disabled={busy}>No</button>
        {cancel}
      </div>
    );
  }
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); if (value.trim()) decide({ action: "correct", value }); }}>
      <label className="visually-hidden" htmlFor={id}>{label}</label>
      <input id={id} type="text" value={value} onChange={(e) => setValue(e.target.value)}
             placeholder={row.field_name === "royalty_rate" ? "e.g. 1/4 or 0.25" : "Value from the lease"} />
      <button type="submit" className="primary" disabled={busy || !value.trim()}>Save</button>
      {absent}{cancel}
    </form>
  );
}

function FieldCard({ row, decision, onDecide, labeling, blind }: {
  row: QueueRow; decision?: Decision; onDecide: (d: Decision) => Promise<void>; labeling: boolean; blind: boolean;
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

  if (blind && (!decision || editing)) {
    return (
      <div className="field-card">
        <div className="small muted">{label}</div>
        {FIELD_HELP[row.field_name] && <div className="note muted">{FIELD_HELP[row.field_name]}</div>}
        <BlindForm row={row} label={label} busy={busy} decide={decide}
                   onCancel={decision ? () => setEditing(false) : undefined} />
        {error && <div className="note" role="alert" style={{ color: "var(--high)" }}>{error}</div>}
      </div>
    );
  }

  return (
    <div className="field-card">
      {blind && <div className="small muted">Model&apos;s answer, shown after you decided:</div>}
      <div className="small muted">{label}, {Math.round(row.confidence * 100)}% confidence</div>
      <div className="value">{display(row)}</div>
      {labeling && FIELD_HELP[row.field_name] && <div className="note muted">{FIELD_HELP[row.field_name]}</div>}
      {row.validation_notes && <div className="note">{row.validation_notes}</div>}
      {row.alternatives?.length ? (
        <div className="alternatives">
          <div className="small muted">The document gives {row.alternatives.length} different values:</div>
          {row.alternatives.map((a, i) => {
            const shown = displayValue(row.field_name, a.value);
            const current = shown === display(row);
            return (
              <div className="alternative" key={i} aria-current={current || undefined}>
                <div className="alt-head">
                  <span className="value">{shown}</span>
                  <span className="small muted">
                    {[a.part ? `part ${a.part}` : null, a.confidence != null ? `${Math.round(a.confidence * 100)}%` : null,
                      current ? "current pick" : null].filter(Boolean).join(" · ")}
                  </span>
                  <button className="chip" disabled={busy}
                          onClick={() => decide(current ? { action: "accept", value: null }
                                                         : { action: "correct", value: String(a.value) })}>
                    Use this
                  </button>
                </div>
                {a.evidence && <blockquote>{a.evidence}</blockquote>}
              </div>
            );
          })}
        </div>
      ) : (
        row.evidence && <blockquote>{row.evidence}</blockquote>
      )}
      {decision ? (
        <div className="row">
          <span className="done">{decisionText(decision, row.field_name)}</span>
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
  const blind = labeling && params.get("blind") === "1";
  const onlyField = labeling ? params.get("field") : null;
  const href = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) if (v == null) next.delete(k); else next.set(k, v);
    return `/review?${next.toString()}`;
  };
  const [queue, setQueue] = useState<QueueRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(params.get("doc"));
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);

  const load = useCallback(() => {
    (labeling ? api.labelFields() : api.queue())
      .then((all) => {
        const q = onlyField ? all.filter((r) => r.field_name === onlyField) : all;
        setQueue(q);
        const saved: Record<string, Decision> = {};
        for (const r of q) {
          const d = savedDecision(r);
          if (d) saved[`${r.document_id}/${r.field_name}`] = d;
        }
        setDecisions(saved);
      })
      .catch((e: Error) => setError(e.message));
  }, [labeling, onlyField]);
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
        {labeling && (
          <Link aria-current={blind ? "page" : undefined} href={href({ blind: blind ? null : "1" })}>
            {blind ? "Model answers hidden" : "Hide model answers"}
          </Link>
        )}
        {onlyField && <Link href={href({ field: null })}>Only {FIELD_LABELS[onlyField] ?? onlyField} ✕</Link>}
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
                blind={blind}
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
