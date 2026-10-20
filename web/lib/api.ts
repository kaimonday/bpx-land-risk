// Typed client for the FastAPI service.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Severity = "high" | "medium" | "low" | "none";

export interface LeaseRisk {
  document_id: string;
  lessor: string | null;
  county: string | null;
  tract_id: string | null;
  gross_acres: number | null;
  royalty_rate: number | null;
  effective_date: string | null;
  primary_term_months: number | null;
  primary_term_end: string | null;
  lease_status: "primary_term" | "held_by_production" | "past_primary_term";
  has_produced: boolean;
  last_production_month: string | null;
  months_since_production: number | null;
  latest_reported_month: string | null;
  cumulative_boe: number;
  has_shut_in_clause: boolean;
  shut_in_due_date: string | null;
  shut_in_payment_amount: number | null;
  extraction_status: string;
  fields_needing_review: number;
  risk_flags: string[];
  severity: Severity;
  severity_rank: number;
  recommended_action: string;
  next_deadline: string | null;
}

export interface Summary {
  leases: number;
  latest_reported_month: string | null;
  as_of_date: string | null;
  high_risk_acres: number | null;
  by_severity: Partial<Record<Severity, number>>;
  by_flag: Record<string, number>;
  review_queue: number;
  operators: string[];
  review_threshold: number;
}

export interface FieldRow {
  field_name: string;
  extracted_value: string | null;
  final_value: string | null;
  confidence: number;
  llm_confidence: number;
  evidence: string | null;
  validation_notes: string | null;
  field_status: "reviewed" | "auto_accepted" | "needs_review";
  review_action: string | null;
  reviewer: string | null;
}

export interface LeaseDetail {
  risk: LeaseRisk;
  fields: FieldRow[];
  production: { production_month: string; liquids_bbl: number; gas_mcf: number; boe: number }[];
  wells: { api_number: string; well_no: string; county: string; rrc_lease_key: string }[];
}

export interface QueueRow {
  document_id: string;
  field_name: string;
  extracted_value: string | null;
  confidence: number;
  llm_confidence: number;
  evidence: string | null;
  validation_notes: string | null;
  // present in label mode: the latest decision already saved for this field
  review_action?: "accept" | "correct" | null;
  corrected_value?: string | null;
}

export type ReviewAction = "accept" | "correct" | "absent";

// Fields left out of label mode, e.g. "tract_id" for real leases, which have no tract IDs.
export const LABEL_SKIP = process.env.NEXT_PUBLIC_LABEL_SKIP ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  summary: () => request<Summary>("/api/summary"),
  risks: () => request<LeaseRisk[]>("/api/risks"),
  lease: (id: string) => request<LeaseDetail>(`/api/leases/${encodeURIComponent(id)}`),
  queue: () => request<QueueRow[]>("/api/review-queue"),
  labelFields: () => request<QueueRow[]>(`/api/label-fields?skip=${encodeURIComponent(LABEL_SKIP)}`),
  health: () => request<{ ok: boolean; database: string }>("/api/health"),
  review: (body: { document_id: string; field_name: string; action: ReviewAction; corrected_value?: string }) =>
    request<{ ok: boolean; stored_value: string | null }>("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  refresh: () => request<{ ok: boolean }>("/api/refresh", { method: "POST" }),
  pdfUrl: (id: string) => `${API_URL}/api/documents/${encodeURIComponent(id)}/pdf`,
};

// ---------- formatting ----------

export const FIELD_LABELS: Record<string, string> = {
  lessor: "Lessor",
  lessee: "Lessee",
  effective_date: "Effective date",
  primary_term_months: "Primary term (months)",
  royalty_rate: "Royalty",
  county: "County",
  state: "State",
  tract_id: "Tract",
  gross_acres: "Gross acres",
  depth_limit_ft: "Depth limit (ft)",
  pugh_clause: "Pugh clause",
  shut_in_payment_per_acre: "Shut-in payment ($/acre)",
  shut_in_grace_days: "Shut-in deadline (days)",
  continuous_drilling_days: "Continuous drilling (days)",
};

export const FLAG_LABELS: Record<string, string> = {
  shut_in_payment_overdue: "Shut-in payment overdue",
  expired_undeveloped: "Term ended, never produced",
  hbp_production_gap: "Production stopped",
  expiring_undeveloped: "Term ending, undeveloped",
  shut_in_payment_due_soon: "Shut-in payment due soon",
  unverified_extraction: "Fields awaiting review",
};

export const STATUS_LABELS: Record<LeaseRisk["lease_status"], string> = {
  primary_term: "Primary term",
  held_by_production: "Held by production",
  past_primary_term: "Past primary term",
};

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "";
  const [y, m, day] = d.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  });
}

export function fmtMonth(d: string | null | undefined): string {
  if (!d) return "";
  const [y, m] = d.slice(0, 7).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function fmtRoyalty(r: number | null): string {
  if (r == null) return "";
  const fractions: Record<string, string> = { "0.125": "1/8", "0.1875": "3/16", "0.2": "1/5", "0.25": "1/4" };
  const key = String(Math.round(r * 10000) / 10000);
  return fractions[key] ? `${fractions[key]} (${(r * 100).toFixed(2).replace(/\.?0+$/, "")}%)` : `${(r * 100).toFixed(2).replace(/\.?0+$/, "")}%`;
}

export function fmtNumber(n: number | null | undefined, digits = 0): string {
  if (n == null) return "";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}
