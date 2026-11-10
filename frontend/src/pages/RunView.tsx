import { useEffect, useRef, useState } from "react";
import { Precheck, Run } from "../api";
import {
  BuyerClaims, CaseHeader, Chip, ErrorBox, FactList, hearingFrom, HearingCards, Loading, ReceiptDrawer, StepTimeline,
  summaryFromFacts, useHighlight, VerdictPanel,
} from "../components";
import { usePoll } from "../usePoll";

export default function RunView({ id }: { id: string }) {
  const q = usePoll<Run>(`/runs/${id}`, 700, true, (r) => r.status !== "running");
  const run = q.data;
  const [receipt, setReceipt] = useState<number | null>(null);
  const [shown, setShown] = useState<number | null>(null); // null = everything; n = steps revealed so far while playing
  const timer = useRef<number | undefined>();
  const { highlight, focusFact } = useHighlight();

  useEffect(() => { setShown(null); window.clearTimeout(timer.current); }, [id]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  if (q.error && !run) return <div className="page"><ErrorBox error={q.error} onRetry={q.reload} /><p><a href="#/">Back to the Inbox</a></p></div>;
  if (!run) return <div className="page"><Loading label="Loading the recorded hearing…" /></div>;

  const total = run.steps.length;
  const play = () => {
    window.clearTimeout(timer.current);
    let n = 0;
    setShown(0);
    const next = () => {
      if (n >= total) { setShown(null); return; }
      const ms = Math.min(run.steps[n].ms || 400, 4000);
      timer.current = window.setTimeout(() => { n += 1; setShown(n); next(); }, ms);
    };
    next();
  };

  const steps = shown == null ? run.steps : run.steps.slice(0, shown);
  const playing = shown != null;
  const precheck = (run.steps.find((s) => s.step === "precheck" && s.status === "ok")?.data as Precheck | undefined) ?? null;
  const { quarantine, assessments } = hearingFrom(steps);

  return (
    <div className="page page-wide">
      <p className="crumb"><a href="#/">← Inbox</a></p>
      <CaseHeader
        s={summaryFromFacts(run.facts)} precheck={precheck} facts={run.facts}
        extra={<Chip tone="neutral" label={run.replay_name ? `Recorded hearing · ${run.replay_name}` : `Run #${run.id} · read-only`} />}
      />
      <div className="case-grid">
        <section className="col col-facts" aria-labelledby="h-facts">
          <h2 id="h-facts">Facts</h2>
          <FactList facts={run.facts} highlight={highlight} onReceipt={setReceipt} errors={[]} />
          <BuyerClaims quarantine={quarantine} assessments={assessments} onFact={focusFact} />
        </section>
        <section className="col col-hearing" aria-labelledby="h-hearing">
          <h2 id="h-hearing">Hearing</h2>
          <div className="toolbar toolbar-left">
            <button type="button" className="btn primary" onClick={play} disabled={!total}>{playing ? "Restart" : "Play"}</button>
            {playing && <button type="button" className="btn" onClick={() => { window.clearTimeout(timer.current); setShown(null); }}>Show all</button>}
          </div>
          {!total && <p className="muted">This run recorded no steps.</p>}
          {steps.length > 0 && <StepTimeline steps={steps} running={playing} />}
          <HearingCards steps={steps} onFact={focusFact} />
        </section>
        <section className="col col-verdict" aria-labelledby="h-verdict">
          <h2 id="h-verdict" className="sr-only">Verdict</h2>
          {playing ? (
            <div className="card"><h2>Verdict</h2><p className="muted">The verdict appears when the last step plays.</p></div>
          ) : (
            <VerdictPanel
              key={run.id + ":" + run.status}
              run={run}
              precheck={precheck ?? { coverage: "unknown", coverage_rule: "", hours_left: null, legal_actions: [], advisory: false, advisory_text: null, offer_types: [], accept_types: [], sandbox_calls: [], warnings: [] }}
              hosted={false} readOnly onFact={focusFact}
              onApprove={async () => {}} onReject={async () => {}}
            />
          )}
        </section>
      </div>
      <ReceiptDrawer receiptId={receipt} onClose={() => setReceipt(null)} />
    </div>
  );
}
