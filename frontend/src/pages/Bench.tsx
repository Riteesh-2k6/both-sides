import { Bench as BenchData, BenchCase, BenchOutcome, BenchTotals } from "../api";
import { actionLabel, Chip, Empty, ErrorBox, fmtTime, humanize, Loading } from "../components";
import { usePoll } from "../usePoll";

function Mark({ o, flipped }: { o?: BenchOutcome; flipped?: boolean }) {
  if (!o) return <span className="muted">—</span>;
  const [tone, text] = o.deadlock ? (["warn", "deadlock"] as const) : !o.valid ? (["bad", "invalid"] as const) : o.match ? (["good", "✓ match"] as const) : (["bad", "✗ miss"] as const);
  return (
    <div>
      <Chip tone={tone} label={text} />
      {o.action && <div className="small muted">{actionLabel(o.action)}{o.type ? ` · ${humanize(o.type)}` : ""}</div>}
      {flipped && <div><Chip tone="warn" label="Changed with tone" /></div>}
    </div>
  );
}
const expected = (c: BenchCase) => c.expected.any_of.map((e) => actionLabel(e.action) + (e.type ? ` (${humanize(e.type)})` : "")).join(" or ");

function Headline({ t, label }: { t: BenchTotals; label: string }) {
  return (
    <p className="headline">
      <span>Desk <strong>{t.desk}/{t.n}</strong></span> · <span>Baseline <strong>{t.baseline}/{t.n}</strong></span> · <span>Always-accept <strong>{t.always_accept}/{t.n}</strong></span>
      <span className="muted"> ({label})</span>
    </p>
  );
}

export default function Bench() {
  const q = usePoll<BenchData>("/bench", 0);
  const b = q.data;
  const cases = b?.cases ?? [];
  const synthetic = cases.filter((c) => c.source === "synthetic").map((c) => c.id);

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h1>Bench</h1>
          <p className="muted">Twelve scripted disputes with known good answers. Does the desk beat a single prompt, and does it stay put when the buyer sounds more convincing?</p>
        </div>
      </div>
      {q.error ? <ErrorBox error={q.error} onRetry={q.reload} /> : !b ? <Loading label="Loading bench results…" /> : !b.cases || !b.totals ? (
        <Empty>No bench results yet. Run <span className="mono">python -m app.bench run</span> in the backend to create them.</Empty>
      ) : (
        <>
          <div className="card headlines">
            <Headline t={b.totals.tuning} label="tuning cases" />
            {b.totals.heldout && <Headline t={b.totals.heldout} label="held-out cases" />}
            <p className="muted small">
              Flips when the buyer's tone changes: desk {b.totals.tuning.desk_flips}, baseline {b.totals.tuning.baseline_flips}. Always-contest {b.totals.tuning.always_contest}/{b.totals.tuning.n}.
            </p>
          </div>

          <div className="card flush scroll-x">
            <table className="bench">
              <thead>
                <tr><th>Case</th><th>Stage</th><th>Expected</th><th>Desk</th><th>Baseline</th><th>Desk, flipped text</th><th>Baseline, flipped text</th><th>Claims dropped</th></tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id}>
                    <td><strong>{c.id}</strong>{c.heldout && <> <Chip label="Held out" /></>}<div className="small muted">{c.title}</div></td>
                    <td>{humanize(c.stage)}</td>
                    <td className="small">{expected(c)}{c.expected.never?.length ? <div className="muted">never {c.expected.never.map(actionLabel).join(", ")}</div> : null}</td>
                    <td><Mark o={c.desk} /></td>
                    <td><Mark o={c.baseline} /></td>
                    <td><Mark o={c.desk_flipped} flipped={c.desk_flip} /></td>
                    <td><Mark o={c.baseline_flipped} flipped={c.baseline_flip} /></td>
                    <td className="small">
                      {c.claims_checked != null ? <div>Desk {c.claims_dropped ?? 0}/{c.claims_checked}</div> : null}
                      {c.baseline_claims_checked != null ? <div>Baseline {c.baseline_claims_dropped ?? 0}/{c.baseline_claims_checked}</div> : null}
                      {c.claims_checked == null && c.baseline_claims_checked == null && <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="muted small footnote">
            n = {cases.length} cases ({cases.filter((c) => !c.heldout).length} tuning, {cases.filter((c) => c.heldout).length} held out).
            Model: {b.model ?? "unknown"}{b.provider ? ` via ${b.provider}` : ""}; temperature {b.temperature ?? 0}; run {b.generated_at ? fmtTime(b.generated_at) : "date unknown"}.
            {synthetic.length > 0 && <> Synthetic snapshots (edited by hand): {synthetic.join(", ")}.</>}
            {" "}Prompts were tuned on the non-held-out cases.
          </p>
        </>
      )}
    </div>
  );
}
