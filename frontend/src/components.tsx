import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  ActionRow, MOCK, ApiError, Assessment, BuyerTurn, Claim, Config, Deadlock, DryRun, Edits, errText, Fact, get, MerchantTurn,
  post, Precheck, Quarantine, Run, SendResult, SnapshotError, Step, Summary,
} from "./api";

/* ---------- shared helpers ---------- */

export const ConfigCtx = createContext<Config | null>(null);
export const useConfig = () => useContext(ConfigCtx);

const REASONS: Record<string, string> = {
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "INR",
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "SNAD",
  UNAUTHORISED: "UNAUTH",
  CREDIT_NOT_PROCESSED: "CNP",
};
export const REASON_ENUMS = Object.keys(REASONS);
export const reasonLabel = (r: string) => REASONS[r] ?? humanize(r);
export function humanize(s: string | null | undefined): string {
  if (!s) return "—";
  const t = s.replace(/_/g, " ").toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
const ACTION_LABELS: Record<string, string> = {
  provide_evidence: "Contest with evidence", make_offer: "Make an offer", accept_claim: "Accept the claim", send_message: "Send a message",
  provide_supporting_info: "Send notes to PayPal (claim under review)",
  require_evidence: "Require evidence", adjudicate: "Adjudicate",
};
export const actionLabel = (a: string | null | undefined) => (a ? ACTION_LABELS[a] ?? humanize(a) : "—");
export function money(v: number | string | null | undefined, cur = "USD"): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return `${v.toFixed(2)} ${cur}`;
}
export function parseMoney(s: string | null | undefined): { value: number; cur: string } | null {
  const m = s?.match(/^(-?[\d.]+)\s*([A-Z]{3})?/);
  return m ? { value: parseFloat(m[1]), cur: m[2] ?? "USD" } : null;
}
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
const BUYER_ID = /^[WB]/;
export const sideOf = (claimId: string): "buyer" | "merchant" => (BUYER_ID.test(claimId) ? "buyer" : "merchant");
const STEP_NAMES: Record<string, string> = {
  facts: "Read facts", precheck: "Rules pre-check", quarantine: "Quarantine buyer text",
  buyer_r1: "Buyer's advocate · round 1", merchant_r1: "Merchant's advocate · round 1", verify_r1: "Check claims · round 1",
  buyer_r2: "Buyer's advocate · round 2", merchant_r2: "Merchant's advocate · round 2", verify_r2: "Check claims · round 2",
  judge_a: "Judge · first reading", judge_b: "Judge · second reading", validate: "Validate verdict",
  baseline: "Single-prompt baseline", envelope: "Settlement limits check", send: "Send to PayPal", restart: "Restart",
};
export const stepName = (s: string) => STEP_NAMES[s] ?? humanize(s);

export function useHighlight() {
  const [highlight, setHighlight] = useState<string | undefined>();
  const timer = useRef<number | undefined>();
  const focusFact = useCallback((id: string) => {
    const el = document.getElementById("fact-" + id);
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el?.scrollIntoView({ block: "center", behavior: calm ? "auto" : "smooth" });
    setHighlight(id);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHighlight(undefined), 2600);
  }, []);
  return { highlight, focusFact };
}

/* ---------- small primitives ---------- */

export function Chip({ label, tone = "neutral", onClick, title }: { label: string; tone?: "neutral" | "good" | "bad" | "warn"; onClick?: () => void; title?: string }) {
  return onClick ? (
    <button type="button" className={`chip chip-${tone} chip-btn`} onClick={onClick} title={title}>{label}</button>
  ) : (
    <span className={`chip chip-${tone}`} title={title}>{label}</span>
  );
}

export function Countdown({ due, now }: { due: string | null; now?: Date }) {
  if (!due) return <span className="muted">—</span>;
  const ms = new Date(due).getTime() - (now ?? new Date()).getTime();
  if (isNaN(ms)) return <span className="muted">—</span>;
  if (ms <= 0) return <span className="countdown urgent"><span aria-hidden="true">▲ </span>overdue</span>;
  const h = ms / 3_600_000;
  const text = h >= 24 ? `${Math.floor(h / 24)}d ${Math.floor(h % 24)}h` : h >= 1 ? `${Math.floor(h)}h ${Math.floor((h % 1) * 60)}m` : `${Math.max(1, Math.floor(ms / 60000))}m`;
  return h < 48 ? (
    <span className="countdown urgent"><span aria-hidden="true">▲ </span>{text}<span className="sr-only"> (under 48 hours)</span></span>
  ) : <span className="countdown">{text}</span>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="state" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}
export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="banner banner-bad" role="alert">
      <div><strong>Something went wrong.</strong> {errText(error)}</div>
      {onRetry && <button type="button" className="btn sm" onClick={onRetry}>Try again</button>}
    </div>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>;
}

/* ---------- facts ---------- */

export function sourceChipText(f: Fact): string {
  if (f.source === "paypal") {
    if (f.receipt == null) return "Recorded snapshot";
    return f.via === "agent-toolkit" ? "PayPal · Agent Toolkit" : "PayPal · REST";
  }
  return f.source === "shop" ? "Shop" : f.source === "merchant" ? "Merchant" : "Check";
}

const GROUPS: { source: Fact["source"]; title: string }[] = [
  { source: "paypal", title: "PayPal" }, { source: "shop", title: "Shop" },
  { source: "merchant", title: "Merchant notes" }, { source: "check", title: "Checks" },
];
function errNoun(call: string): string {
  const m = call.match(/(capture|order|dispute|refund)/i);
  return m ? m[1].toLowerCase() : call;
}

export function FactList({ facts, highlight, onReceipt, errors }: { facts: Fact[]; highlight?: string; onReceipt: (id: number) => void; errors: SnapshotError[] }) {
  return (
    <div className="facts">
      {errors.map((e, i) => (
        <div key={i} className="fact fact-err" role="alert">
          <span className="fact-label">Couldn't read {errNoun(e.call)}: {e.status}{e.debug_id ? `, debug ${e.debug_id}` : ""}</span>
          <Chip label="Read failed" tone="bad" />
        </div>
      ))}
      {GROUPS.map((g) => {
        const rows = facts.filter((f) => f.source === g.source);
        if (!rows.length) return null;
        return (
          <section key={g.source} className="fact-group" aria-label={g.title}>
            <h3>{g.title}</h3>
            {rows.map((f) => (
              <div key={f.id} id={"fact-" + f.id} className={"fact" + (highlight === f.id ? " fact-hl" : "")}>
                <div className="fact-main">
                  <span className="fact-label">{f.label}</span>
                  <span className="fact-value">{f.value}</span>
                  <span className="fact-id mono">{f.id}</span>
                </div>
                <Chip
                  label={sourceChipText(f)}
                  onClick={f.receipt != null ? () => onReceipt(f.receipt as number) : undefined}
                  title={f.receipt != null ? "Open the masked PayPal response" : undefined}
                />
              </div>
            ))}
          </section>
        );
      })}
      {!facts.length && !errors.length && <p className="muted">No facts yet.</p>}
    </div>
  );
}

export function ReceiptDrawer({ receiptId, onClose }: { receiptId: number | null; onClose: () => void }) {
  const [rec, setRec] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const closeCb = useRef(onClose);
  closeCb.current = onClose;
  useEffect(() => {
    if (receiptId == null) return;
    setRec(null); setErr(null);
    get<any>("/receipts/" + receiptId).then(setRec, setErr);
    closeRef.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && closeCb.current();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [receiptId]);
  if (receiptId == null) return null;
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="PayPal receipt">
        <header className="drawer-head">
          <div>
            <h2>Receipt #{receiptId}</h2>
            <p className="muted">Masked PayPal response</p>
          </div>
          <button type="button" className="btn sm" ref={closeRef} onClick={onClose}>Close</button>
        </header>
        {err ? <ErrorBox error={err} /> : !rec ? <Loading /> : (
          <>
            <dl className="kv">
              <dt>Request</dt><dd className="mono">{rec.method} {rec.path}</dd>
              <dt>Status</dt><dd>{rec.status}</dd>
              <dt>Via</dt><dd>{rec.via === "agent-toolkit" ? "PayPal · Agent Toolkit" : "PayPal · REST"}</dd>
              <dt>Debug id</dt><dd className="mono">{rec.debug_id ?? "—"}</dd>
            </dl>
            <pre className="code">{JSON.stringify(rec.body, null, 2)}</pre>
          </>
        )}
      </aside>
    </>
  );
}

/* ---------- buyer claims + hearing ---------- */

const ASSESS_TONE = { supported: "good", contradicted: "bad", unverified: "neutral" } as const;

export function BuyerClaims({ quarantine, assessments, onFact }: { quarantine: Quarantine | null; assessments: Assessment[]; onFact?: (id: string) => void }) {
  return (
    <section className="buyer-box" aria-label="Buyer says (untested)">
      <h3>Buyer says (untested)</h3>
      {!quarantine ? <p className="muted">The buyer's claims appear once the hearing starts.</p> : (
        <>
          {quarantine.injection_suspected && <Chip tone="warn" label="Instruction-like text in the buyer's message was ignored" />}
          <p className="buyer-reason">{quarantine.stated_reason || "No reason stated."}</p>
          <p className="muted small">Wants: {humanize(quarantine.requested_outcome)}</p>
          {quarantine.alleged.length ? (
            <ul className="alleged">
              {quarantine.alleged.map((a) => {
                const as = assessments.find((x) => x.alleged_id === a.id);
                return (
                  <li key={a.id}>
                    <span className="mono alleged-id">{a.id}</span>
                    <span className="alleged-text">{a.text}</span>
                    {as && <Chip tone={ASSESS_TONE[as.status]} label={humanize(as.status)} title={as.note} />}
                    {as && as.evidence_ids.length > 0 && (
                      <span className="chip-row">{as.evidence_ids.map((id) => <Chip key={id} label={id} onClick={onFact ? () => onFact(id) : undefined} />)}</span>
                    )}
                    {as?.note && <span className="muted small alleged-note">{as.note}</span>}
                  </li>
                );
              })}
            </ul>
          ) : <p className="muted small">No specific claims to test.</p>}
        </>
      )}
    </section>
  );
}

function ClaimRow({ c, dropped, onFact }: { c: Claim; dropped?: string; onFact: (id: string) => void }) {
  return (
    <li className={"claim" + (dropped ? " claim-dropped" : "")}>
      <div className="claim-head">
        <span className="mono claim-id">{c.id}</span>
        <Chip label={c.kind === "fact" ? "Fact" : "Inference"} tone={c.kind === "fact" ? "neutral" : "warn"} />
        {dropped && <Chip tone="bad" label={`Dropped: ${dropped}`} />}
      </div>
      <p className="claim-text">{c.text}</p>
      <div className="chip-row">
        {c.evidence_ids.map((id) => <Chip key={id} label={id} onClick={() => onFact(id)} title="Show this fact" />)}
        {c.responds_to.length > 0 && <span className="muted small">responds to {c.responds_to.join(", ")}</span>}
      </div>
    </li>
  );
}

export function ClaimCard({ title, turn, dropped, onFact }: { title: string; turn: BuyerTurn | MerchantTurn; dropped: { id: string; reason: string }[]; onFact: (factId: string) => void }) {
  const why = "why_filed" in turn ? turn.why_filed : [];
  const reason = (id: string) => dropped.find((d) => d.id === id)?.reason;
  const side = turn.claims[0] ? sideOf(turn.claims[0].id) : title.startsWith("Buyer") ? "buyer" : "merchant";
  return (
    <article className={`card claimcard side-${side}`}>
      <h3>{title}</h3>
      {why.length > 0 && (
        <>
          <h4>Why they filed</h4>
          <ul className="claims">{why.map((c) => <ClaimRow key={c.id} c={c} dropped={reason(c.id)} onFact={onFact} />)}</ul>
        </>
      )}
      <h4>Claims</h4>
      {turn.claims.length ? <ul className="claims">{turn.claims.map((c) => <ClaimRow key={c.id} c={c} dropped={reason(c.id)} onFact={onFact} />)}</ul> : <p className="muted">No claims.</p>}
      <h4>Proposes</h4>
      <p className="proposal">
        <strong>{actionLabel(turn.proposal.action)}</strong>
        {turn.proposal.type && <Chip label={humanize(turn.proposal.type)} />}
        {turn.proposal.amount != null && <span className="amount">{turn.proposal.amount.toFixed(2)}</span>}
      </p>
      <p className="muted">{turn.proposal.why}</p>
      {turn.deciding_fact && (
        <p className="small">Deciding fact: <Chip label={turn.deciding_fact} onClick={() => onFact(turn.deciding_fact as string)} /></p>
      )}
    </article>
  );
}

/** Pulls the quarantine box, assessments and claim cards out of a step list. */
export function hearingFrom(steps: Step[]) {
  const ok = (n: string) => steps.find((s) => s.step === n && s.status === "ok")?.data;
  const cards: { key: string; title: string; turn: BuyerTurn | MerchantTurn; dropped: { id: string; reason: string }[] }[] = [];
  let assessments: Assessment[] = [];
  for (let r = 1; r <= 4; r++) {
    const verify = ok("verify_r" + r);
    const dropped: { id: string; reason: string }[] = verify?.dropped ?? [];
    const b = ok("buyer_r" + r) as BuyerTurn | undefined;
    const m = ok("merchant_r" + r) as MerchantTurn | undefined;
    if (b) { cards.push({ key: "b" + r, title: `Buyer's advocate · round ${r}`, turn: b, dropped }); if (b.assessments?.length) assessments = b.assessments; }
    if (m) cards.push({ key: "m" + r, title: `Merchant's advocate · round ${r}`, turn: m, dropped });
  }
  return { quarantine: (ok("quarantine") as Quarantine | undefined) ?? null, assessments, cards };
}

export function HearingCards({ steps, onFact }: { steps: Step[]; onFact: (id: string) => void }) {
  const { cards } = hearingFrom(steps);
  return <>{cards.map((c) => <ClaimCard key={c.key} title={c.title} turn={c.turn} dropped={c.dropped} onFact={onFact} />)}</>;
}

function stepDetail(s: Step): string {
  const d = s.data ?? {};
  if (s.status === "error") return d.message ?? "Failed";
  if (s.step === "facts") return `${d.count ?? "?"} facts${d.errors?.length ? `, ${d.errors.length} read errors` : ""}`;
  if (s.step.startsWith("verify")) return `${d.kept?.length ?? 0} kept, ${d.dropped?.length ?? 0} dropped`;
  if (s.step === "envelope") return d.ok ? "Within limits" : d.reason ?? "";
  if (s.step === "send") return humanize(d.status);
  if (s.step === "judge_a" || s.step === "judge_b") return d.verdict ? `${actionLabel(d.verdict.action)}${d.verdict.type ? " · " + humanize(d.verdict.type) : ""}` : "No verdict";
  return "";
}

export function StepTimeline({ steps, running }: { steps: Step[]; running?: boolean }) {
  return (
    <ol className="timeline" aria-label="Hearing steps">
      {steps.map((s, i) => (
        <li key={i} className={"tl tl-" + s.status}>
          <span className="tl-dot" aria-hidden="true" />
          <div className="tl-body">
            <div className="tl-top">
              <span className="tl-name">{stepName(s.step)}</span>
              <Chip label={s.status === "ok" ? "Done" : s.status === "skipped" ? "Skipped" : "Error"} tone={s.status === "ok" ? "good" : s.status === "error" ? "bad" : "neutral"} />
              <span className="muted small">{s.ms} ms</span>
              {s.llm && <span className="muted small">{s.llm.provider} · {s.llm.model}{s.llm.attempts > 1 ? ` · ${s.llm.attempts} attempts` : ""}</span>}
            </div>
            {stepDetail(s) && <div className={"small " + (s.status === "error" ? "txt-bad" : "muted")}>{stepDetail(s)}</div>}
          </div>
        </li>
      ))}
      {running && <li className="tl tl-run"><span className="tl-dot" aria-hidden="true" /><div className="tl-body"><span className="muted">Working…</span></div></li>}
    </ol>
  );
}

/* ---------- case header ---------- */

export function summaryFromFacts(facts: Fact[]): Pick<Summary, "id" | "reason" | "amount" | "due"> {
  const v = (id: string) => facts.find((f) => f.id === id)?.value ?? null;
  return { id: v("pp.dispute.id") ?? "—", reason: v("pp.dispute.reason") ?? "", amount: v("pp.dispute.amount"), due: v("pp.dispute.due") };
}

const COVERAGE = {
  covered: { text: "Seller Protection: likely covered", tone: "good" },
  not_covered: { text: "Seller Protection: not covered", tone: "warn" },
  unknown: { text: "Seller Protection: unclear", tone: "neutral" },
} as const;

export function CaseHeader({ s, precheck, extra, facts = [], stage }: { s: Pick<Summary, "id" | "reason" | "amount" | "due">; precheck: Precheck | null; extra?: ReactNode; facts?: Fact[]; stage?: string }) {
  const cov = precheck ? COVERAGE[precheck.coverage] : null;
  const fv = (id: string) => facts.find((f) => f.id === id)?.value;
  const channel = fv("pp.dispute.channel");
  const st = stage ?? fv("pp.dispute.stage");
  const kind = channel === "EXTERNAL" ? "Card chargeback" : channel === "INTERNAL" && st === "CHARGEBACK" ? "PayPal claim" : null;
  return (
    <header className="case-head">
      <div className="case-title">
        <h1>Dispute <span className="mono">{s.id}</span></h1>
        <Chip label={reasonLabel(s.reason)} title={humanize(s.reason)} />
        {kind && <Chip label={kind} />}
        <span className="case-amount">{s.amount ?? "—"}</span>
        <span className="case-due"><span className="muted">Respond in </span><Countdown due={s.due} /></span>
        {extra}
      </div>
      {precheck && cov && (
        <div className="case-coverage">
          <Chip label={cov.text} tone={cov.tone} />
          <span className="muted">{precheck.coverage_rule}</span>
        </div>
      )}
      {precheck?.advisory && (
        <div className="banner banner-warn" role="note"><strong>Advisory only.</strong> {precheck.advisory_text ?? "No seller actions are open on this case."} The hearing still runs, as advice.</div>
      )}
      {precheck && precheck.warnings.length > 0 && (
        <ul className="warnings">{precheck.warnings.map((w) => <li key={w}><Chip tone="warn" label="Warning" /> {w}</li>)}</ul>
      )}
    </header>
  );
}

/* ---------- verdict ---------- */

const FAULT_LABELS: Record<string, string> = { agent_misread: "Shopping agent misread the request", merchant_error: "Merchant error", buyer_changed_mind: "Buyer changed their mind" };
const actionOk = (s: string) => s === "sent";

function SendSummary({ r }: { r: SendResult }) {
  const tone = actionOk(r.status) ? "good" : r.status === "failed" ? "bad" : "warn";
  const label = r.status === "sent" ? "Sent to PayPal" : r.status === "failed" ? "PayPal rejected it" : r.status === "unknown" ? "Result unknown" : humanize(r.status);
  return (
    <div className={`banner banner-${tone}`} role="status">
      <div>
        <strong>{label}</strong>
        <dl className="kv inline">
          {r.http_status != null && <><dt>HTTP</dt><dd>{r.http_status}</dd></>}
          {r.debug_id && <><dt>Debug id</dt><dd className="mono">{r.debug_id}</dd></>}
        </dl>
        {r.status === "failed" && r.body && (r.body.name || r.body.message) && (
          <p><strong>{r.body.name}</strong> {r.body.message}</p>
        )}
      </div>
    </div>
  );
}

export function VerdictPanel(props: {
  run: Run; precheck: Precheck; hosted: boolean;
  onApprove: (edits: Edits | null) => Promise<void>; onReject: () => Promise<void>;
  owned?: boolean; actions?: ActionRow[]; sent?: SendResult | null; onCheck?: (actionId: number) => Promise<void>; readOnly?: boolean; onFact?: (id: string) => void;
}) {
  const { run, precheck, hosted, onApprove, onReject, owned = true, actions = [], sent = null, onCheck, readOnly = false, onFact } = props;
  const v = run.verdict;
  const val = run.validation && "ok" in run.validation ? run.validation : null;
  const cur = parseMoney(run.facts.find((f) => f.id === "pp.dispute.amount")?.value)?.cur ?? "USD";
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState(val?.message_text ?? "");
  const [amt, setAmt] = useState(v?.amount != null ? String(v.amount) : "");
  const [ev, setEv] = useState<string[]>(v?.evidence.map((e) => e.fact_id) ?? []);
  const [busy, setBusy] = useState<"" | "approve" | "reject" | "check">("");
  const [err, setErr] = useState<string | null>(null);
  const [errList, setErrList] = useState<string[]>([]);
  const [dry, setDry] = useState<DryRun | null>(null);
  const [dryErr, setDryErr] = useState<string | null>(null);

  const dryRun = hosted && !owned && !!v && run.mode === "desk";
  useEffect(() => {
    if (!dryRun) return;
    setDry(null); setDryErr(null);
    get<DryRun>(`/runs/${run.id}/dry-run`).then(setDry, (e) => setDryErr(errText(e)));
  }, [dryRun, run.id]);

  if (run.status === "running" && !v) return <div className="card"><Loading label="The hearing is running…" /></div>;

  const mine = actions.filter((a) => a.run_id === run.id).slice();
  mine.sort((a, b) => b.id - a.id);
  const latest = mine[0];
  const unknownAction = actions.find((a) => a.status === "unknown");
  const alreadySent = mine.some((a) => a.status === "sent" || a.status === "pending") || (sent?.status === "sent");
  const autoSend = run.envelope?.send as SendResult | null | undefined;

  if (!v) {
    const errs = run.validation && !("ok" in run.validation)
      ? [...(run.validation.a?.errors ?? []), ...(run.validation.b?.errors ?? [])] : val?.errors ?? [];
    const stepErr = run.steps.find((s) => s.status === "error" && s.data?.message);
    return (
      <div className="card verdict">
        <h2>Verdict</h2>
        <div className="banner banner-bad" role="alert">
          <div>
            <strong>{run.status === "error" ? "The hearing didn't produce a valid verdict." : "No verdict."}</strong>
            {stepErr && <p>{stepErr.data.message}</p>}
            {errs.length > 0 && <ul>{[...new Set(errs)].map((e) => <li key={e}>{e}</li>)}</ul>}
          </div>
        </div>
        <p className="muted">Nothing can be approved from this run. Run the hearing again.</p>
      </div>
    );
  }

  let blocked: string | null = null;
  if (run.status === "running") blocked = "Hearing still finishing";
  else if (readOnly || run.mode === "replay") blocked = "Recorded hearing: nothing is sent.";
  else if (run.mode === "baseline") blocked = "Baseline runs are advice only and can't be sent.";
  else if (run.status === "rejected") blocked = "You rejected this verdict.";
  else if (val && !val.ok) blocked = "The verdict failed validation, so it can't be sent.";
  else if (precheck.advisory) blocked = precheck.advisory_text ?? "No seller actions are open on this case.";
  else if (alreadySent) blocked = "Already sent";
  else if (unknownAction) blocked = "A previous send's result is unknown. Check PayPal first.";
  else if (dryRun) blocked = "Shared demo case: dry run only";

  const editError = !editing ? null
    : val && !msg.trim() ? "Message can't be empty"
    : v.amount != null && !(Number.isFinite(Number(amt)) && amt.trim() !== "" && Number(amt) > 0) ? "Enter a valid amount" : null;
  const edits = (): Edits | null => {
    if (!editing) return null;
    const e: Edits = {};
    if (val && msg !== val.message_text) e.message = msg;
    if (v.amount != null && Number(amt) !== v.amount) e.amount = Number(amt);
    const orig = v.evidence.map((x) => x.fact_id);
    if (ev.length !== orig.length || ev.some((x) => !orig.includes(x))) e.evidence_fact_ids = ev;
    return Object.keys(e).length ? e : null;
  };
  const guard = async (kind: "approve" | "reject" | "check", fn: () => Promise<void>) => {
    setBusy(kind); setErr(null); setErrList([]);
    try { await fn(); setEditing(false); } catch (e) {
      setErr(errText(e, kind === "approve" ? "Already sent" : undefined));
      if (e instanceof ApiError && Array.isArray(e.body?.errors)) setErrList(e.body.errors);
    } finally { setBusy(""); }
  };

  const keptSentences = val ? val.kept_sentences ?? [] : v.message;
  const total = v.fault ? Object.values(v.fault).reduce((a, b) => a + b, 0) || 1 : 1;
  const shown: SendResult | null = sent ?? (latest ? { action_id: latest.id, status: latest.status, http_status: latest.http_status ?? undefined, debug_id: latest.debug_id } : null);

  return (
    <div className="card verdict">
      <h2>Verdict</h2>
      <div className="verdict-main">
        <div className="verdict-action">{actionLabel(v.action)}</div>
        <div className="chip-row">
          {v.type && <Chip label={humanize(v.type)} />}
          {v.amount != null && <Chip tone="neutral" label={`${v.amount.toFixed(2)} ${cur}`} />}
          {run.mode === "baseline" && <Chip tone="warn" label="Baseline · advice only" />}
          {run.status === "rejected" && <Chip tone="bad" label="Rejected" />}
        </div>
      </div>

      {run.outcome && (
        <div className="agree">
          {run.outcome.agreed ? <Chip tone="good" label="Both readings agreed" /> : <Chip tone="warn" label="The two readings differed" />}
          {run.outcome.differences.length > 0 && (
            <>
              <p className="small muted">Open points. You decide; settlement limits won't send this automatically.</p>
              <ul className="small muted">{run.outcome.differences.map((d) => <li key={d}>{d}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {v.evidence.length > 0 && (
        <>
          <h4>Evidence plan</h4>
          <ul className="evidence">
            {v.evidence.map((e) => <li key={e.fact_id + e.evidence_type}><Chip label={humanize(e.evidence_type)} /> <span className="mono small">{e.fact_id}</span></li>)}
          </ul>
        </>
      )}

      <h4>Message to the buyer</h4>
      {keptSentences.length ? (
        <p className="message">
          {keptSentences.map((s, i) => (
            <span key={i} className="sentence">
              {s.text}{" "}
              {s.fact_ids.map((id) => <Chip key={id} label={id} onClick={() => onFact?.(id)} />)}{" "}
            </span>
          ))}
        </p>
      ) : val?.message_text ? <p className="message">{val.message_text}</p> : <p className="muted">No message.</p>}
      {val && val.dropped_sentences.length > 0 && (
        <ul className="dropped">{val.dropped_sentences.map(([t, r], i) => <li key={i}><s>{t}</s> <Chip tone="bad" label={`Dropped: ${r}`} /></li>)}</ul>
      )}
      {val && val.warnings.length > 0 && <ul className="warnings">{val.warnings.map((w) => <li key={w}><Chip tone="warn" label="Warning" /> {w}</li>)}</ul>}
      {val && val.errors.length > 0 && (
        <div className="banner banner-bad" role="alert"><div><strong>Validation errors</strong><ul>{val.errors.map((e) => <li key={e}>{e}</li>)}</ul></div></div>
      )}

      {v.fault && (
        <>
          <h4>Who was at fault</h4>
          <ul className="fault">
            {Object.entries(v.fault).map(([k, n]) => {
              const pct = Math.round((n / total) * 100);
              return (
                <li key={k}>
                  <span>{FAULT_LABELS[k] ?? humanize(k)}</span>
                  <span className="bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>
                  <span className="pct">{pct}%</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {v.rationale.length > 0 && (
        <details className="rationale">
          <summary>Rationale ({v.rationale.length})</summary>
          <ul className="claims">{v.rationale.map((c) => <ClaimRow key={c.id} c={c} onFact={(id) => onFact?.(id)} />)}</ul>
        </details>
      )}

      {run.envelope && (
        <div className={"banner " + (autoSend ? (autoSend.status === "sent" ? "banner-good" : autoSend.status === "failed" ? "banner-bad" : "banner-warn") : "banner-info")} role="status">
          <div>
            <strong>{autoSend
              ? autoSend.status === "sent" ? "Sent automatically within your settlement limits"
                : autoSend.status === "failed" ? "Within your settlement limits, but PayPal rejected it"
                : "Within your settlement limits; result unknown, check PayPal"
              : `Not sent automatically: ${run.envelope.reason}`}</strong>
            {autoSend && <SendSummary r={autoSend} />}
          </div>
        </div>
      )}

      {dryRun && (
        <section className="dry" aria-label="Dry run">
          <h4>Dry run: this is exactly what would be sent to PayPal</h4>
          {dryErr ? <p className="txt-bad">{dryErr}</p> : !dry ? <Loading /> : (
            <>
              <p className="mono small">POST {dry.path} <span className="muted">({dry.kind})</span></p>
              <pre className="code">{JSON.stringify(dry.body, null, 2)}</pre>
            </>
          )}
        </section>
      )}

      {shown && (
        <>
          <SendSummary r={shown} />
          {shown.status === "unknown" && (
            <div className="banner banner-warn" role="alert">
              <div>
                <strong>Result unknown.</strong> PayPal didn't answer in time, so the write may or may not have landed.
                If PayPal received the first send late, this may send twice.
              </div>
              {onCheck && (latest || shown.action_id != null) && (
                <button type="button" className="btn sm" disabled={busy === "check"}
                  onClick={() => guard("check", () => onCheck((shown.action_id ?? latest!.id) as number))}>Check PayPal</button>
              )}
            </div>
          )}
        </>
      )}

      {editing && (
        <form className="edit" onSubmit={(e) => e.preventDefault()} aria-label="Edit before sending">
          <label>Message to the buyer
            <textarea rows={5} maxLength={2000} value={msg} onChange={(e) => setMsg(e.target.value)} disabled={!val} />
          </label>
          {v.amount != null && (
            <label>Amount ({cur})
              <input type="number" min="0.01" step="0.01" value={amt} onChange={(e) => setAmt(e.target.value)} />
            </label>
          )}
          {v.evidence.length > 0 && (
            <fieldset>
              <legend>Evidence to attach</legend>
              {v.evidence.map((e) => (
                <label key={e.fact_id} className="check">
                  <input type="checkbox" checked={ev.includes(e.fact_id)}
                    onChange={(x) => setEv(x.target.checked ? [...ev, e.fact_id] : ev.filter((i) => i !== e.fact_id))} />
                  {humanize(e.evidence_type)} <span className="mono small">{e.fact_id}</span>
                </label>
              ))}
            </fieldset>
          )}
          <p className="muted small">Edits are checked again against fresh PayPal facts. A changed message skips the per-sentence support check.</p>
        </form>
      )}

      {err && (
        <div className="banner banner-bad" role="alert">
          <div>{err}{errList.length > 0 && <ul>{errList.map((e) => <li key={e}>{e}</li>)}</ul>}</div>
        </div>
      )}

      {!readOnly && run.mode === "desk" && run.status !== "rejected" && (
        <div className="actions">
          <button type="button" className="btn primary" disabled={!!blocked || !!busy || !!editError} onClick={() => guard("approve", () => onApprove(edits()))}>
            {busy === "approve" ? "Sending…" : "Approve & send to PayPal"}
          </button>
          <button type="button" className="btn" disabled={!!blocked || !!busy || !val} onClick={() => setEditing((x) => !x)} aria-expanded={editing}>
            {editing ? "Cancel edit" : "Edit"}
          </button>
          <button type="button" className="btn danger-ghost" disabled={!!busy || alreadySent || run.status === "running"} onClick={() => guard("reject", onReject)}>Reject</button>
        </div>
      )}
      {editError && <p className="txt-bad small" role="alert">{editError}</p>}
      {blocked && <p className="muted small" role="note">{blocked}</p>}
    </div>
  );
}

/* ---------- deadlock + sandbox ---------- */

export function DeadlockCard({ deadlock, onSubmit }: { deadlock: Deadlock; onSubmit: (answers: { question: string; answer: string }[]) => Promise<void> }) {
  const [answers, setAnswers] = useState<string[]>(deadlock.questions.map(() => ""));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const filled = answers.some((a) => a.trim());
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      await onSubmit(deadlock.questions.map((q, i) => ({ question: q.slice(0, 200), answer: answers[i].trim().slice(0, 300) })).filter((x) => x.answer));
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card deadlock">
      <h2>No verdict yet</h2>
      <Chip tone="warn" label="Deadlock" />
      <p><strong>The advocates can't settle this without:</strong></p>
      <p className="muted small">One reading leaned to {actionLabel(deadlock.a_action).toLowerCase()}, the other to {actionLabel(deadlock.b_action).toLowerCase()}.</p>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {deadlock.questions.slice(0, 3).map((q, i) => (
          <label key={i}>{q}
            <textarea rows={2} maxLength={300} value={answers[i]} onChange={(e) => setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))} />
          </label>
        ))}
        {err && <div className="banner banner-bad" role="alert">{err}</div>}
        <button type="submit" className="btn primary" disabled={busy || !filled}>{busy ? "Saving…" : "Add as merchant notes & re-run"}</button>
      </form>
    </div>
  );
}

const SANDBOX_VALUES: Record<string, string[]> = { require_evidence: ["SELLER_EVIDENCE", "BUYER_EVIDENCE"], adjudicate: ["SELLER_FAVOR", "BUYER_FAVOR"] };

export function SandboxPanel({ disputeId, precheck, onDone }: { disputeId: string; precheck: Precheck; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ status: number; debug_id: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const go = async (call: string, value: string) => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await post(`/disputes/${disputeId}/simulate`, { call, value })); onDone(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <section className="card sandbox">
      <button type="button" className="disclosure" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> Sandbox controls
      </button>
      {open && (
        <div className="sandbox-body">
          <p className="muted small">Acts as PayPal in the sandbox, to move this case along. Status: {precheck.sandbox_calls.length ? "available" : "nothing available right now"}.</p>
          {precheck.sandbox_calls.map((c) => (
            <div key={c} className="sandbox-row">
              <strong>{actionLabel(c)}</strong>
              {(SANDBOX_VALUES[c] ?? []).map((v) => <button key={v} type="button" className="btn sm" disabled={busy} onClick={() => go(c, v)}>{humanize(v)}</button>)}
            </div>
          ))}
          {res && <div className="banner banner-good" role="status">PayPal answered {res.status}{res.debug_id ? `, debug id ${res.debug_id}` : ""}.</div>}
          {err && <div className="banner banner-bad" role="alert">{err}</div>}
        </div>
      )}
    </section>
  );
}

/* ---------- PayPal Buttons (Shop + Shopper) ---------- */

type PPButtons = { render: (el: HTMLElement) => Promise<void>; close?: () => void };
declare global {
  interface Window {
    paypal?: { Buttons: (o: { createOrder: () => Promise<string>; onApprove: (d: { orderID: string }) => Promise<void>; onError?: (e: unknown) => void; style?: object }) => PPButtons };
  }
}

let sdkPromise: Promise<void> | null = null;
function loadSdk(clientId: string): Promise<void> {
  if (window.paypal) return Promise.resolve();
  sdkPromise ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=USD`;
    s.onload = () => resolve();
    s.onerror = () => { sdkPromise = null; reject(new Error("Couldn't load the PayPal button script.")); };
    document.head.appendChild(s);
  });
  return sdkPromise;
}

export function PayPalButton({ createOrder, onApprove, onError }: { createOrder: () => Promise<string>; onApprove: (orderId: string) => Promise<void>; onError: (msg: string) => void }) {
  const cfg = useConfig();
  const host = useRef<HTMLDivElement>(null);
  const cb = useRef({ createOrder, onApprove, onError });
  cb.current = { createOrder, onApprove, onError };
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  useEffect(() => {
    if (MOCK) { setState("ready"); return; }
    if (!cfg?.paypal_client_id) { setState("failed"); return; }
    let btn: PPButtons | null = null;
    loadSdk(cfg.paypal_client_id).then(() => {
      if (!host.current || !window.paypal) return;
      btn = window.paypal.Buttons({
        style: { layout: "horizontal", height: 38, tagline: false, label: "pay" } as object,
        createOrder: () => cb.current.createOrder(),
        onApprove: (d) => cb.current.onApprove(d.orderID),
        onError: (e) => cb.current.onError(e instanceof Error ? e.message : "PayPal reported an error."),
      });
      btn.render(host.current).then(() => setState("ready"), () => setState("failed"));
    }, () => setState("failed"));
    return () => { btn?.close?.(); };
  }, [cfg?.paypal_client_id]);
  if (MOCK) return <button type="button" className="btn paypal-mock" onClick={() => cb.current.createOrder().then(cb.current.onApprove)}>PayPal (mock)</button>;
  return (
    <div>
      <div ref={host} className="pp-host" />
      {state === "loading" && <span className="muted small">Loading PayPal…</span>}
      {state === "failed" && <span className="txt-bad small">PayPal buttons are unavailable (check the client id and your connection).</span>}
    </div>
  );
}
