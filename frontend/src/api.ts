// Types mirror docs/design.md §4.1, §6, §8.2, §11.2, §13 and §18. get/post prepend /api (R18).

export type Fact = { id: string; source: "paypal" | "shop" | "merchant" | "check"; label: string; value: string; receipt: number | null; via: string | null };
export type Precheck = {
  coverage: "covered" | "not_covered" | "unknown"; coverage_rule: string; hours_left: number | null;
  legal_actions: string[]; advisory: boolean; advisory_text: string | null;
  offer_types: string[]; accept_types: string[]; sandbox_calls: string[]; warnings: string[];
};
export type Validation = { ok: boolean; errors: string[]; warnings: string[]; dropped_sentences: [string, string][]; dropped_rationale?: [string, string][]; kept_sentences?: Sentence[]; message_text: string };
export type Envelope = { max_offer_amount: number; max_refund_pct: number; daily_cap: number; allowed_actions: string[]; allowed_reasons: string[]; expires_at: string };

export type SnapshotError = { call: string; status: number; debug_id: string | null };
export type Claim = { id: string; text: string; kind: "fact" | "inference"; evidence_ids: string[]; responds_to: string[] };
export type Proposal = { action: string; type: string | null; amount: number | null; why: string };
export type Assessment = { alleged_id: string; status: "supported" | "contradicted" | "unverified"; evidence_ids: string[]; note: string };
export type BuyerTurn = { assessments: Assessment[]; why_filed: Claim[]; claims: Claim[]; proposal: Proposal; deciding_fact: string | null };
export type MerchantTurn = { claims: Claim[]; proposal: Proposal; deciding_fact: string | null };
export type Quarantine = { stated_reason: string; requested_outcome: string; alleged: { id: string; text: string }[]; injection_suspected: boolean };
export type Sentence = { text: string; fact_ids: string[]; courtesy: boolean };
export type Verdict = {
  action: string; type: string | null; amount: number | null; evidence: { fact_id: string; evidence_type: string }[];
  message: Sentence[]; rationale: Claim[];
  fault: { agent_misread: number; merchant_error: number; buyer_changed_mind: number } | null; needs_fact: string | null;
};
export type Deadlock = { questions: string[]; a_action: string; b_action: string };
export type Outcome = { agreed: boolean; differences: string[]; second: { action: string; type: string | null; amount: number | null } | null; deadlock: Deadlock | null };
export type Step = {
  step: string; status: "ok" | "skipped" | "error"; ms: number; data: any;
  llm: { provider: string; model: string; ms: number; prompt_tokens: number; completion_tokens: number; attempts: number } | null;
};
export type SendResult = { action_id?: number; status: string; http_status?: number; debug_id?: string | null; body?: any };
export type Run = {
  id: number; dispute_id: string; mode: "desk" | "baseline" | "replay"; status: string; steps: Step[]; verdict: Verdict | null;
  validation: Validation | { a: Validation | null; b: Validation | null } | null; outcome: Outcome | null;
  envelope: { ok: boolean; reason: string; log_id: number; send: any } | null; facts: Fact[]; replay_name: string | null;
};
export type Edits = { message?: string; amount?: number; evidence_fact_ids?: string[] };

export type Summary = {
  id: string; reason: string; status: string; stage: string; amount: string | null; amount_cents: number | null; currency: string | null;
  created: string | null; due: string | null; hours_left: number | null;
  last_run: { id: number; mode: string; status: string; action: string | null } | null;
  auto_action: { action: string; created_at: string } | null;
  owned?: boolean;
};
export type ActionRow = { id: number; run_id: number; action: string; status: string; http_status: number | null; debug_id: string | null; approved_by: string; edited: boolean | number; created_at: string };
export type CaseDetail = {
  summary: Summary; facts: Fact[]; precheck: Precheck; snapshot_errors: SnapshotError[];
  runs: { id: number; mode: string; status: string; action: string | null; created_at: string }[]; actions: ActionRow[];
};
export type Config = { paypal_client_id: string; hosted: boolean; max_rounds: number; llm_label: string; chargeback_enabled: boolean; shopper_paths: string[] };
export type Product = { sku: string; name: string; category: string; kind: string; color: string | null; price: number | string; sizes: string[] | null };
export type Order = {
  id: string; status: string; capture_id: string | null; sku: string; name: string; size: string | null; amount: string; via: string; created_at: string;
  shipment: { carrier: string; tracking: string; shipped_at: string | null; delivered_at: string | null; signature: boolean | number | null; size_shipped: string | null; return_delivered_at: string | null } | null;
  refunds: { paypal_refund_id: string; amount: string; created_at: string }[]; dispute_id: string | null;
};
export type ShopperResult = {
  constraints: { category: string | null; color: string | null; size: string | null; max_price: number | null };
  candidates: any[]; choice: { sku: string; size: string | null; reason: string };
  mandate: { id: number; text: string; hash: string }; payment_path: "vault" | "card" | "human"; order: Order;
};
export type EnvelopeState = { envelope: Envelope | null; hash: string | null; source: "hosted" | "local" | null };
export type EnvelopeLogRow = { id: number; dispute_id: string; run_id: number; action: string; amount: number | null; ok: boolean | number; reason: string; status: string; created_at: string };
export type Replay = { run_id: number; replay_name: string; dispute_id: string; status: string; action: string | null };
export type DryRun = { path: string; kind: string; body: any };
export type BenchOutcome = { action: string | null; type: string | null; valid: boolean; deadlock: boolean; match: boolean; fault_top: string | null };
export type BenchTotals = { n: number; desk: number; baseline: number; always_accept: number; always_contest: number; desk_flips: number; baseline_flips: number; claims_dropped: number };
export type BenchCase = {
  id: string; title: string; stage: string; heldout: boolean; source: string; expected: { any_of: { action: string; type?: string }[]; never?: string[]; fault_top?: string };
  desk?: BenchOutcome; baseline?: BenchOutcome; desk_flipped?: BenchOutcome; baseline_flipped?: BenchOutcome;
  always_accept?: BenchOutcome; always_contest?: BenchOutcome; desk_flip?: boolean; baseline_flip?: boolean;
  claims_checked?: number; claims_dropped?: number; baseline_claims_checked?: number; baseline_claims_dropped?: number;
};
export type Bench = {
  generated_at?: string; provider?: string; model?: string; temperature?: number; repeats?: number; ms_per_call?: number;
  cases?: BenchCase[]; totals?: { tuning: BenchTotals; heldout: BenchTotals | null };
};

export class ApiError extends Error {
  status: number; body: any;
  constructor(status: number, body: any) {
    const b = body?.error ?? body?.detail;
    super(typeof b === "string" ? b : status === 0 ? "Can't reach the server" : `Request failed (${status})`);
    this.status = status; this.body = body;
  }
}

export const MOCK = new URLSearchParams(location.search).has("mock");

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (MOCK) return (await import("./mock")).mock(method, path, body) as T;
  let res: Response;
  try {
    res = await fetch("/api" + path, {
      method, credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, { error: "Can't reach the server" });
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { error: text.slice(0, 200) }; }
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export const get = <T,>(path: string) => request<T>("GET", path);
export const post = <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body);
export const put = <T,>(path: string, body: unknown) => request<T>("PUT", path, body);

/** Human message for a failed call; §14 copy for 429. */
export function errText(e: unknown, on409?: string): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return "Limit reached; try a recorded run";
    if (e.status === 409 && on409 && !e.body?.error) return on409;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
