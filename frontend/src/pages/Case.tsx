import { useEffect, useState } from "react";
import { CaseDetail, errText, Replay, Run, SendResult, post, Edits } from "../api";
import {
  BuyerClaims, CaseHeader, Chip, DeadlockCard, Empty, ErrorBox, FactList, hearingFrom, HearingCards, humanize, Loading,
  ReceiptDrawer, SandboxPanel, StepTimeline, useConfig, useHighlight, VerdictPanel,
} from "../components";
import { usePoll } from "../usePoll";

export default function Case({ id }: { id: string }) {
  const cfg = useConfig();
  const detail = usePoll<CaseDetail>(`/disputes/${encodeURIComponent(id)}`, 60_000);
  const replays = usePoll<Replay[]>("/replays", 0);
  const [picked, setPicked] = useState<number | null>(null);
  const [receipt, setReceipt] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [sent, setSent] = useState<SendResult | null>(null);
  const { highlight, focusFact } = useHighlight();

  useEffect(() => { setPicked(null); setSent(null); setRunErr(null); }, [id]);

  const d = detail.data;
  const latest = d?.runs.length ? Math.max(...d.runs.map((r) => r.id)) : null;
  const activeId = picked ?? latest;
  const runQ = usePoll<Run>(activeId != null ? `/runs/${activeId}` : null, 700, true, (r) => r.status !== "running");
  const run = runQ.data && runQ.data.id === activeId ? runQ.data : null;
  const running = run?.status === "running" || (activeId != null && !run && !runQ.error && picked != null);

  const status = run?.status;
  useEffect(() => { if (status && status !== "running") detail.reload(); /* eslint-disable-next-line */ }, [status]);

  const base = `/disputes/${encodeURIComponent(id)}`;
  const start = async (mode: "desk" | "baseline") => {
    setStarting(true); setRunErr(null); setSent(null);
    try { setPicked((await post<{ run_id: number }>(`${base}/runs`, { mode })).run_id); }
    catch (e) { setRunErr(errText(e, "A hearing is already running")); }
    finally { setStarting(false); }
  };

  if (detail.error && !d) return <div className="page"><ErrorBox error={detail.error} onRetry={detail.reload} /><p><a href="#/">Back to the Inbox</a></p></div>;
  if (!d) return <div className="page"><Loading label="Reading the dispute from PayPal…" /></div>;

  const facts = run?.facts.length ? run.facts : d.facts;
  const steps = run?.steps ?? [];
  const { quarantine, assessments } = hearingFrom(steps);
  const owned = d.summary.owned ?? true;
  const errStep = steps.find((s) => s.status === "error");
  const myReplay = replays.data?.find((r) => r.dispute_id === id);
  const unknownAction = d.actions.find((a) => a.status === "unknown");

  return (
    <div className="page page-wide">
      <p className="crumb"><a href="#/">← Inbox</a></p>
      <CaseHeader s={d.summary} precheck={d.precheck} facts={d.facts} stage={d.summary.stage} />

      <div className="case-grid">
        {/* 1. Facts */}
        <section className="col col-facts" aria-labelledby="h-facts">
          <h2 id="h-facts">Facts</h2>
          {run?.facts.length ? <p className="muted small">As the hearing saw them (run #{run.id}).</p> : null}
          <FactList facts={facts} highlight={highlight} onReceipt={setReceipt} errors={d.snapshot_errors} />
          <BuyerClaims quarantine={quarantine} assessments={assessments} onFact={focusFact} />
        </section>

        {/* 2. Hearing */}
        <section className="col col-hearing" aria-labelledby="h-hearing">
          <h2 id="h-hearing">Hearing</h2>
          <div className="toolbar toolbar-left">
            <button type="button" className="btn primary" disabled={starting || running} onClick={() => start("desk")}>
              {starting || running ? "Hearing in progress…" : "Run hearing"}
            </button>
            <button type="button" className="btn" disabled={starting || running} onClick={() => start("baseline")}>Run single-prompt baseline</button>
          </div>
          {runErr && (
            <div className="banner banner-bad" role="alert">
              <div>{runErr} {runErr.startsWith("Limit") && <a href="#/">Recorded hearings are in the Inbox.</a>}</div>
            </div>
          )}
          {run?.mode === "baseline" && <Chip tone="warn" label="Baseline run · one prompt, no advocates" />}
          {runQ.error && activeId != null && <ErrorBox error={runQ.error} onRetry={runQ.reload} />}

          {!run && activeId == null && (
            <Empty>No hearing yet. Run the hearing and the two advocates will argue from the facts on the left.</Empty>
          )}
          {activeId != null && !run && !runQ.error && <Loading label="Loading the hearing…" />}

          {d.runs.length > 1 && (
            <div className="runs" role="group" aria-label="Earlier runs">
              <span className="muted small">Runs:</span>
              {[...d.runs].sort((a, b) => b.id - a.id).map((r) => (
                <button key={r.id} type="button" className={"chip chip-btn chip-neutral" + (r.id === activeId ? " chip-on" : "")} aria-pressed={r.id === activeId}
                  onClick={() => { setPicked(r.id); setSent(null); }}>#{r.id} {humanize(r.mode)} · {humanize(r.status)}</button>
              ))}
            </div>
          )}

          {errStep && (
            <div className="banner banner-warn" role="alert">
              <div>
                <strong>The hearing stopped.</strong> {errStep.data?.message}{" "}
                {myReplay ? <a href={`#/run/${myReplay.run_id}`}>Watch a recorded hearing</a> : <a href="#/">Watch a recorded hearing (see the Inbox)</a>}
              </div>
            </div>
          )}

          {steps.length > 0 && <StepTimeline steps={steps} running={running} />}
          <HearingCards steps={steps} onFact={focusFact} />
        </section>

        {/* 3. Verdict */}
        <section className="col col-verdict" aria-labelledby="h-verdict">
          <h2 id="h-verdict" className="sr-only">Verdict</h2>
          {!run ? (
            <div className="card"><h2>Verdict</h2><p className="muted">The judge's verdict appears here when the hearing finishes. Nothing is sent to PayPal until you approve it.</p></div>
          ) : run.status === "deadlock" && run.outcome?.deadlock ? (
            <DeadlockCard
              deadlock={run.outcome.deadlock}
              onSubmit={async (answers) => {
                for (const a of answers) await post(`${base}/notes`, a);
                detail.reload();
                await start("desk");
              }}
            />
          ) : (
            <VerdictPanel
              key={run.id + ":" + run.status}
              run={run} precheck={d.precheck} hosted={!!cfg?.hosted} owned={owned} actions={d.actions} sent={sent} onFact={focusFact}
              onApprove={async (edits: Edits | null) => {
                setSent(await post<SendResult>(`${base}/actions`, { run_id: run.id, edits }));
                detail.reload();
              }}
              onReject={async () => { await post(`${base}/runs/${run.id}/reject`); runQ.reload(); detail.reload(); }}
              onCheck={async (aid) => { await post(`${base}/actions/${aid}/check`); setSent(null); detail.reload(); }}
            />
          )}
          {unknownAction && !run?.verdict && (
            <div className="banner banner-warn" role="alert">Result unknown for an earlier send. Check PayPal before approving anything else.</div>
          )}
          {d.precheck.sandbox_calls.length > 0 && (!cfg?.hosted || owned) && (
            <SandboxPanel disputeId={id} precheck={d.precheck} onDone={() => { detail.reload(); }} />
          )}
        </section>
      </div>

      <ReceiptDrawer receiptId={receipt} onClose={() => setReceipt(null)} />
    </div>
  );
}
