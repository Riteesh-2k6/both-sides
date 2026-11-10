import { useEffect, useState } from "react";
import { Envelope, EnvelopeLogRow, EnvelopeState, errText, put } from "../api";
import { actionLabel, Chip, Empty, ErrorBox, fmtTime, humanize, Loading, REASON_ENUMS, reasonLabel, useConfig } from "../components";
import { usePoll } from "../usePoll";

const ACTIONS = ["make_offer", "send_message"];
const toLocalInput = (iso: string) => {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const blank = (): Envelope => ({ max_offer_amount: 0, max_refund_pct: 0, daily_cap: 0, allowed_actions: [], allowed_reasons: [], expires_at: new Date(Date.now() + 7 * 864e5).toISOString() });

export default function Limits() {
  const cfg = useConfig();
  const state = usePoll<EnvelopeState>("/envelope", 0);
  const log = usePoll<EnvelopeLogRow[]>("/envelope/log", 0);
  const [form, setForm] = useState<Envelope>(blank());
  const [hash, setHash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!state.data) return;
    setForm(state.data.envelope ?? blank());
    setHash(state.data.hash);
  }, [state.data]);

  const readOnly = !!cfg?.hosted || state.data?.source === "hosted";
  const set = <K extends keyof Envelope>(k: K, v: Envelope[K]) => { setForm({ ...form, [k]: v }); setSaved(false); };
  const toggle = (k: "allowed_actions" | "allowed_reasons", v: string) => set(k, form[k].includes(v) ? form[k].filter((x) => x !== v) : [...form[k], v]);
  const reasons = [...REASON_ENUMS, ...form.allowed_reasons.filter((r) => !REASON_ENUMS.includes(r))];

  const save = async () => {
    setBusy(true); setErr(null); setSaved(false);
    try {
      const r = await put<EnvelopeState>("/envelope", { ...form, expires_at: new Date(form.expires_at).toISOString().replace(/\.\d+Z$/, "Z") });
      setHash(r.hash); setSaved(true); log.reload();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Settlement limits</h1>
          <p className="muted">When a hearing's verdict falls inside these limits and both judge readings agree, Both Sides sends it without waiting for you. At most one automatic action per dispute. Anything else waits for your approval.</p>
        </div>
      </div>

      {state.error && !state.data ? <ErrorBox error={state.error} onRetry={state.reload} /> : !state.data ? <Loading /> : (
        <form className="card limits-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          {readOnly && <div className="banner banner-info" role="note">These limits are set by the host and can't be changed here.</div>}
          {!state.data.envelope && !readOnly && <div className="banner banner-info" role="note">No limits are set, so every verdict waits for your approval.</div>}
          <fieldset disabled={readOnly || busy}>
            <div className="form-grid">
              <label>Largest offer (USD)<input type="number" min="0" step="0.01" value={form.max_offer_amount} onChange={(e) => set("max_offer_amount", Number(e.target.value))} /></label>
              <label>Largest share of the dispute amount (%)<input type="number" min="0" max="100" step="1" value={form.max_refund_pct} onChange={(e) => set("max_refund_pct", Number(e.target.value))} /></label>
              <label>Daily cap (USD)<input type="number" min="0" step="0.01" value={form.daily_cap} onChange={(e) => set("daily_cap", Number(e.target.value))} /></label>
              <label>Expires (your local time)<input type="datetime-local" value={toLocalInput(form.expires_at)} onChange={(e) => e.target.value && set("expires_at", new Date(e.target.value).toISOString())} /></label>
            </div>
            <div className="form-grid">
              <fieldset>
                <legend>Allowed actions</legend>
                {ACTIONS.map((a) => <label key={a} className="check"><input type="checkbox" checked={form.allowed_actions.includes(a)} onChange={() => toggle("allowed_actions", a)} />{actionLabel(a)}</label>)}
              </fieldset>
              <fieldset>
                <legend>Allowed reasons</legend>
                {reasons.map((r) => <label key={r} className="check"><input type="checkbox" checked={form.allowed_reasons.includes(r)} onChange={() => toggle("allowed_reasons", r)} /><strong>{reasonLabel(r)}</strong> <span className="muted small">{humanize(r)}</span></label>)}
              </fieldset>
            </div>
          </fieldset>
          {err && <div className="banner banner-bad" role="alert">{err}</div>}
          <div className="form-foot">
            {!readOnly && <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>}
            {saved && <Chip tone="good" label="Saved" />}
            {hash && <span className="muted small">Limits fingerprint <span className="mono">{hash.slice(0, 16)}</span></span>}
          </div>
        </form>
      )}

      <section className="section">
        <h2>Automatic action log</h2>
        {log.error && !log.data ? <ErrorBox error={log.error} onRetry={log.reload} /> : !log.data ? <Loading /> : log.data.length === 0 ? (
          <Empty>Nothing has been checked against your limits yet.</Empty>
        ) : (
          <div className="card flush">
            <table className="rtable">
              <thead><tr><th>When</th><th>Dispute</th><th>Action</th><th>Amount</th><th>Result</th><th>Reason</th></tr></thead>
              <tbody>
                {log.data.map((r) => (
                  <tr key={r.id}>
                    <td data-label="When">{fmtTime(r.created_at)}</td>
                    <td data-label="Dispute"><a href={`#/case/${encodeURIComponent(r.dispute_id)}`} className="mono small">{r.dispute_id}</a></td>
                    <td data-label="Action">{actionLabel(r.action)}</td>
                    <td data-label="Amount">{r.amount != null ? r.amount.toFixed(2) : "—"}</td>
                    <td data-label="Result"><Chip tone={r.status === "sent" ? "good" : r.status === "rejected" ? "neutral" : r.status === "failed" ? "bad" : "warn"} label={humanize(r.status)} /></td>
                    <td data-label="Reason">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
