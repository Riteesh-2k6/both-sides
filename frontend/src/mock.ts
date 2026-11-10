// DEV ONLY: canned responses for `?mock=1`, shaped per docs/design.md §11 and §13. Not used in normal operation.
import type { Bench, BenchOutcome, CaseDetail, Fact, Run, Step, Summary, Order } from "./api";

const f = (id: string, source: Fact["source"], label: string, value: string, receipt: number | null = null, via: string | null = null): Fact => ({ id, source, label, value, receipt, via });
const facts: Fact[] = [
  f("pp.dispute.id", "paypal", "Dispute id", "PP-D-1001", 12, "agent-toolkit"),
  f("pp.dispute.reason", "paypal", "Reason", "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", 12, "agent-toolkit"),
  f("pp.dispute.status", "paypal", "Status", "WAITING_FOR_SELLER_RESPONSE", 12, "agent-toolkit"),
  f("pp.dispute.stage", "paypal", "Stage", "CHARGEBACK", 12, "agent-toolkit"),
  f("pp.dispute.channel", "paypal", "Channel", "INTERNAL", 12, "agent-toolkit"),
  f("pp.dispute.protection", "paypal", "Seller protection on the dispute", "ELIGIBLE", 12, "agent-toolkit"),
  f("pp.dispute.amount", "paypal", "Dispute amount", "140.00 USD", 12, "agent-toolkit"),
  f("pp.dispute.due", "paypal", "Respond by", new Date(Date.now() + 80 * 3600e3).toISOString().replace(/\.\d+Z$/, "Z"), 12, "agent-toolkit"),
  f("pp.capture.amount", "paypal", "Capture amount", "140.00 USD", 13, "rest"),
  f("pp.capture.seller_protection", "paypal", "Seller protection", "ELIGIBLE", 13, "rest"),
  f("pp.order.ship_to", "paypal", "Ship to", "Austin, TX 73301, US", null, null),
  f("shop.product.name", "shop", "Product", "Hand block-printed cotton quilt"),
  f("shop.shipment.carrier", "shop", "Carrier", "India Post"),
  f("shop.shipment.tracking", "shop", "Tracking number", "EE123456789IN"),
  f("shop.shipment.delivered_at", "shop", "Delivered", "2026-11-02T15:20:00Z"),
  f("shop.shipment.signature", "shop", "Signature on delivery", "no"),
  f("shop.policy.returns", "shop", "Returns policy", "Unworn, unwashed textiles can be returned to our Jaipur workshop within 30 days for a full refund; export shipping is not refunded."),
  f("merchant.note.1", "merchant", "Did the buyer contact you?", "No messages received."),
  f("check.address_match", "check", "Shipped to the transaction address", "yes"),
  f("check.hours_left", "check", "Hours left to respond", "80.0"),
];
const precheck = {
  coverage: "covered" as const, coverage_rule: "Delivered to the address on the transaction.", hours_left: 80,
  legal_actions: ["provide_evidence", "make_offer", "accept_claim", "send_message"], advisory: false, advisory_text: null,
  offer_types: ["REFUND"], accept_types: ["REFUND"], sandbox_calls: ["require_evidence", "adjudicate"], warnings: ["Under 48 hours to respond."],
};
const llm = { provider: "local", model: "qwen3-4b", ms: 5200, prompt_tokens: 1800, completion_tokens: 420, attempts: 1 };
const claim = (id: string, text: string, ev: string[], kind: "fact" | "inference" = "fact", responds: string[] = []) => ({ id, text, kind, evidence_ids: ev, responds_to: responds });
const buyerTurn = {
  assessments: [{ alleged_id: "A1", status: "contradicted", evidence_ids: ["shop.shipment.delivered_at"], note: "Tracking shows delivery on 2 Nov." }, { alleged_id: "A2", status: "unverified", evidence_ids: [], note: "No record either way." }],
  why_filed: [claim("W1", "The buyer says the parcel never arrived.", ["pp.dispute.reason"])],
  claims: [claim("B1", "No delivery is recorded for this buyer's door.", ["shop.shipment.delivered_at"]), claim("B2", "A signature was not required.", ["shop.shipment.signature"], "inference")],
  proposal: { action: "accept_claim", type: "REFUND", amount: 140, why: "A refund is simple and keeps the customer." }, deciding_fact: "shop.shipment.delivered_at",
};
const merchTurn = {
  claims: [claim("M1", "The parcel was delivered on 2026-11-02 to the transaction address.", ["shop.shipment.delivered_at", "check.address_match"], "fact", ["B1"]), claim("M2", "The buyer contacted nobody before filing.", ["merchant.note.1"], "fact"), claim("M3", "Delivered in 4 days.", ["shop.shipment.tracking"])],
  proposal: { action: "provide_evidence", type: null, amount: null, why: "Proof of delivery to the right address." }, deciding_fact: "shop.shipment.delivered_at",
};
const verdict = {
  action: "provide_evidence", type: null, amount: null, evidence: [{ fact_id: "shop.shipment.tracking", evidence_type: "PROOF_OF_FULFILLMENT" }, { fact_id: "shop.shipment.delivered_at", evidence_type: "PROOF_OF_FULFILLMENT" }],
  message: [
    { text: "Your order was delivered on 2026-11-02 to the address on the transaction.", fact_ids: ["shop.shipment.delivered_at", "check.address_match"], courtesy: false },
    { text: "Tracking number EE123456789IN is attached.", fact_ids: ["shop.shipment.tracking"], courtesy: false },
    { text: "We're happy to help further.", fact_ids: [], courtesy: true },
  ],
  rationale: [claim("J1", "Delivery is recorded and the address matches.", ["shop.shipment.delivered_at", "check.address_match"])],
  fault: null, needs_fact: null,
};
const step = (s: string, data: any, ms = 800, l: Step["llm"] = null, status: Step["status"] = "ok"): Step => ({ step: s, status, ms, data, llm: l });
const doneSteps: Step[] = [
  step("facts", { count: facts.length, errors: [] }, 900),
  step("precheck", precheck, 4),
  step("quarantine", { stated_reason: "The package never arrived.", requested_outcome: "full_refund", injection_suspected: false, alleged: [{ id: "A1", text: "I never received the parcel." }, { id: "A2", text: "The seller ignored me." }] }, 3100, llm),
  step("buyer_r1", buyerTurn, 6400, llm),
  step("merchant_r1", merchTurn, 6100, llm),
  step("verify_r1", { kept: ["W1", "B1", "M1", "M2"], dropped: [{ id: "B2", reason: "no evidence cited" }, { id: "M3", reason: "number 4 not in cited facts" }] }, 6),
  step("judge_a", { verdict, validation: {} }, 7200, llm),
  step("judge_b", { verdict, validation: {} }, 7000, llm),
  step("validate", { ok: true }, 3),
  step("envelope", { ok: false, reason: "action not allowed" }, 2),
];
const validation = { ok: true, errors: [], warnings: [], dropped_sentences: [["Call us any time at the shop.", "link, email or instruction text"]] as [string, string][], dropped_rationale: [], kept_sentences: verdict.message, message_text: "Your order was delivered on 2026-11-02 to the address on the transaction. Tracking number EE123456789IN is attached. We're happy to help further." };
const doneRun: Run = {
  id: 7, dispute_id: "PP-D-1001", mode: "desk", status: "done", steps: doneSteps, verdict, validation,
  outcome: { agreed: true, differences: ["second reading asked: Was the parcel signed for?"], second: null, deadlock: null }, envelope: { ok: false, reason: "action not allowed", log_id: 3, send: null }, facts, replay_name: null,
};
const deadRun: Run = {
  ...doneRun, id: 8, status: "deadlock", verdict: null, validation: null,
  steps: [...doneSteps.slice(0, 6), step("judge_a", { verdict, validation: {} }, 7200, llm), step("judge_b", { verdict: { ...verdict, action: "accept_claim" }, validation: {} }, 7000, llm)],
  outcome: { agreed: false, differences: [], second: null, deadlock: { questions: ["Did the buyer reach out before filing?", "Is the delivery photo on file?"], a_action: "provide_evidence", b_action: "accept_claim" } },
};
const summary: Summary = {
  id: "PP-D-1001", reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", status: "WAITING_FOR_SELLER_RESPONSE", stage: "CHARGEBACK", amount: "140.00 USD", amount_cents: 14000, currency: "USD",
  created: "2026-11-03T09:00:00Z", due: facts.find((x) => x.id === "pp.dispute.due")!.value, hours_left: 80, last_run: { id: 7, mode: "desk", status: "done", action: "provide_evidence" }, auto_action: null, owned: true,
};
const detail: CaseDetail = {
  summary, facts, precheck, snapshot_errors: [{ call: "GET /v2/payments/refunds/R1", status: 404, debug_id: "abc123" }],
  runs: [{ id: 7, mode: "desk", status: "done", action: "provide_evidence", created_at: "2026-11-04T08:00:00Z" }, { id: 8, mode: "desk", status: "deadlock", action: null, created_at: "2026-11-04T09:00:00Z" }],
  actions: [],
};
const order = (id: string, o: Partial<Order> = {}): Order => ({ id, status: "captured", capture_id: "CAP" + id, sku: "RP-QUILT", name: "Hand block-printed cotton quilt", size: "Queen", amount: "140.00 USD", via: "storefront", created_at: "2026-11-01T10:00:00Z", shipment: null, refunds: [], dispute_id: null, ...o });
const orders: Order[] = [
  order("ORD-1", { shipment: { carrier: "India Post", tracking: "EE123456789IN", shipped_at: "2026-11-01T12:00:00Z", delivered_at: "2026-11-02T15:20:00Z", signature: false, size_shipped: "Queen", return_delivered_at: null }, dispute_id: "PP-D-1001" }),
  order("ORD-2", { sku: "RP-SCARF-IND", name: "Indigo dabu block-print scarf", size: null, amount: "38.00 USD" }), order("ORD-3", { status: "created", capture_id: null }),
];
const oc = (m: boolean, flip = false): BenchOutcome => ({ action: flip ? "accept_claim" : "provide_evidence", type: null, valid: true, deadlock: false, match: m, fault_top: null });
const bench: Bench = {
  generated_at: "2026-11-04T06:00:00Z", provider: "local", model: "qwen3-4b Q5_K_M", temperature: 0, repeats: 1, ms_per_call: 5100,
  cases: [
    { id: "INR-1", title: "Delivered to the transaction address", stage: "INQUIRY", heldout: false, source: "sandbox-ui", expected: { any_of: [{ action: "provide_evidence" }] }, desk: oc(true), baseline: oc(true), desk_flipped: oc(true), baseline_flipped: oc(false, true), desk_flip: false, baseline_flip: true, claims_checked: 9, claims_dropped: 2, baseline_claims_checked: 3, baseline_claims_dropped: 1 },
    { id: "UNAUTH-2", title: "Digital $12 pattern pack", stage: "CHARGEBACK", heldout: false, source: "synthetic", expected: { any_of: [{ action: "accept_claim" }] }, desk: { ...oc(false), deadlock: true }, baseline: oc(false), desk_flipped: oc(false), baseline_flipped: oc(false), desk_flip: false, baseline_flip: false, claims_checked: 7, claims_dropped: 0, baseline_claims_checked: 2, baseline_claims_dropped: 0 },
    { id: "SNAD-2", title: "Shipped a Single quilt for a Queen order", stage: "INQUIRY", heldout: true, source: "synthetic", expected: { any_of: [{ action: "make_offer", type: "REFUND_WITH_REPLACEMENT" }, { action: "accept_claim" }], never: [] } },
  ],
  totals: {
    tuning: { n: 8, desk: 6, baseline: 4, always_accept: 3, always_contest: 3, desk_flips: 1, baseline_flips: 3, claims_dropped: 11 },
    heldout: { n: 4, desk: 3, baseline: 2, always_accept: 1, always_contest: 2, desk_flips: 0, baseline_flips: 1, claims_dropped: 4 },
  },
};

export async function mock(method: string, path: string, body: any): Promise<any> {
  await new Promise((r) => setTimeout(r, 120));
  const err = (status: number, b: any) => Promise.reject(Object.assign(new Error(b.error), { status, body: b }));
  if (path === "/health") return { ok: true, paypal: "ok", llm: ["local:qwen3-4b"], boot_errors: [] };
  if (path === "/config") return { paypal_client_id: "mock", hosted: new URLSearchParams(location.search).has("hosted"), max_rounds: 2, llm_label: "qwen3-4b via localhost", chargeback_enabled: true, shopper_paths: ["card", "human"] };
  if (path === "/disputes" || path === "/disputes/refresh") return [summary, { ...summary, id: "PP-D-1002", reason: "UNAUTHORISED", stage: "CHARGEBACK", amount: "118.00 USD", due: null, last_run: null, auto_action: { action: "make_offer", created_at: new Date().toISOString() } }];
  if (path.startsWith("/disputes/") && method === "GET") return detail;
  if (path.endsWith("/runs") && method === "POST") return { run_id: body?.mode === "baseline" ? 7 : 8 };
  if (path === "/runs/7") return doneRun;
  if (path === "/runs/8") return deadRun;
  if (path === "/runs/9") return { ...doneRun, id: 9, mode: "replay", replay_name: "inr-delivered" };
  if (path.endsWith("/dry-run")) return { path: "/v1/customer/disputes/PP-D-1001/provide-evidence", kind: "multipart", body: { evidences: [{ evidence_type: "PROOF_OF_FULFILLMENT" }] } };
  if (path === "/replays") return [{ run_id: 9, replay_name: "inr-delivered", dispute_id: "PP-D-1001", status: "done", action: "provide_evidence" }];
  if (path.startsWith("/receipts/")) return { id: 12, via: "agent-toolkit", method: "GET", path: "/v1/customer/disputes/PP-D-1001", status: 200, debug_id: "dbg-1", body: { dispute_id: "PP-D-1001", seller: { name: "N." } } };
  if (path === "/envelope" && method === "GET") return { envelope: { max_offer_amount: 50, max_refund_pct: 60, daily_cap: 150, allowed_actions: ["make_offer"], allowed_reasons: ["MERCHANDISE_OR_SERVICE_NOT_RECEIVED"], expires_at: "2026-12-01T00:00:00Z" }, hash: "ab12cd34ef56ab12cd34ef56", source: "local" };
  if (path === "/envelope") return { envelope: body, hash: "ab12cd34ef56ab12cd34ef56", source: "local" };
  if (path === "/envelope/log") return [{ id: 3, dispute_id: "PP-D-1002", run_id: 5, action: "make_offer", amount: 20, ok: 1, reason: "within limits", status: "sent", created_at: "2026-11-04T07:00:00Z" }];
  if (path === "/bench") return bench;
  if (path === "/shop/products") return [
    { sku: "RP-SCARF-IND", name: "Indigo dabu block-print scarf", category: "scarf", kind: "physical", color: "indigo", price: 38, sizes: null },
    { sku: "RP-SCARF-RED", name: "Madder-red bagru scarf", category: "scarf", kind: "physical", color: "red", price: 38, sizes: null },
    { sku: "RP-QUILT", name: "Hand block-printed cotton quilt", category: "quilt", kind: "physical", color: "indigo", price: 140, sizes: ["Single", "Queen", "King"] },
    { sku: "RP-CUSHION", name: "Kantha-stitch cushion covers (set of 2)", category: "cushion", kind: "physical", color: null, price: 24, sizes: ["16 in", "18 in"] },
    { sku: "RP-PATTERN", name: "Block-print pattern pack (PDF)", category: "pattern", kind: "digital", color: null, price: 12, sizes: null },
  ];
  if (path === "/shop/orders") return orders;
  if (path.startsWith("/shop/orders")) return orders[0];
  if (path === "/shopper/run") return { constraints: { category: "quilt", color: "indigo", size: "Queen", max_price: 150 }, candidates: [{ sku: "RP-QUILT", name: "Hand block-printed cotton quilt", price: 140 }], choice: { sku: "RP-QUILT", size: "Queen", reason: "Only quilt in indigo, Queen size, under $150." }, mandate: { id: 1, text: "A queen-size indigo quilt under $150", hash: "9f2c" }, payment_path: "card", order: orders[0] };
  if (/\/actions$/.test(path)) return { action_id: 1, status: "sent", http_status: 200, debug_id: "dbg-2", body: {} };
  if (path.endsWith("/reject") || path.endsWith("/notes")) return { ok: true };
  return err(404, { error: "mock: no such route " + path });
}
