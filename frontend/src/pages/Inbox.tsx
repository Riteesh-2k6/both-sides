import { useEffect, useState } from "react";
import { errText, post, Replay, Summary } from "../api";
import { actionLabel, Chip, Countdown, Empty, ErrorBox, humanize, Loading, reasonLabel, useConfig } from "../components";
import { usePoll } from "../usePoll";

const DAY = 24 * 3600 * 1000;

export default function Inbox() {
  const cfg = useConfig();
  const list = usePoll<Summary[]>("/disputes", 60_000);
  const health = usePoll<{ paypal: string }>(list.error ? "/health" : null, 0);
  const replays = usePoll<Replay[]>("/replays", 0);
  const [rows, setRows] = useState<Summary[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (list.data) setRows(list.data); }, [list.data]);

  const refresh = async () => {
    setBusy(true); setErr(null);
    try { setRows(await post<Summary[]>("/disputes/refresh")); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const newDispute = async () => {
    setBusy(true); setErr(null); setNote(null);
    try { setNote((await post<{ message: string }>("/demo/new-dispute")).message); } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };

  const autoCount = (rows ?? []).filter((r) => r.auto_action && Date.now() - new Date(r.auto_action.created_at).getTime() < DAY).length;
  const canNew = !!cfg?.hosted && !!cfg.chargeback_enabled && cfg.shopper_paths.includes("card");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Disputes</h1>
          <p className="muted">Open PayPal disputes for Rangsaaz Prints (Jaipur), soonest deadline first.</p>
        </div>
        <div className="toolbar">
          {canNew && <button type="button" className="btn" disabled={busy} onClick={newDispute}>New sandbox dispute</button>}
          <button type="button" className="btn" disabled={busy} onClick={refresh}>{busy ? "Refreshing…" : "Refresh"}</button>
        </div>
      </div>

      {note && <div className="banner banner-good" role="status">{note}</div>}
      {err && <div className="banner banner-bad" role="alert">{err}</div>}
      {autoCount > 0 && (
        <div className="banner banner-info" role="status">
          <div>{autoCount} {autoCount === 1 ? "action was" : "actions were"} sent automatically within your settlement limits. <a href="#/limits">Review.</a></div>
        </div>
      )}

      {!rows && list.error && health.data?.paypal === "missing credentials" ? (
        <div className="banner banner-bad" role="alert"><div>PayPal sandbox credentials are not set (see README → Run it locally)</div><button type="button" className="btn sm" onClick={list.reload}>Try again</button></div>
      ) : !rows && list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : !rows ? <Loading label="Loading disputes…" /> : rows.length === 0 ? (
        <Empty>No disputes yet. Buy something in the Shop, then open a dispute as the sandbox buyer.</Empty>
      ) : (
        <div className="card flush">
          <table className="rtable">
            <thead>
              <tr><th>Reason</th><th>Amount</th><th>Status</th><th>Stage</th><th>Respond in</th><th>Last run</th><th><span className="sr-only">Open</span></th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td data-label="Reason">
                    <div className="chip-row">
                      <Chip label={reasonLabel(r.reason)} title={humanize(r.reason)} />
                      {r.auto_action && Date.now() - new Date(r.auto_action.created_at).getTime() < DAY && <Chip tone="good" label="Auto-settled" />}
                      {cfg?.hosted && <Chip tone={r.owned ? "good" : "neutral"} label={r.owned ? "Your case" : "Shared demo case"} />}
                    </div>
                    <div className="mono small muted">{r.id}</div>
                  </td>
                  <td data-label="Amount">{r.amount ?? "—"}</td>
                  <td data-label="Status">{humanize(r.status)}</td>
                  <td data-label="Stage">{humanize(r.stage)}</td>
                  <td data-label="Respond in"><Countdown due={r.due} /></td>
                  <td data-label="Last run">
                    {r.last_run ? (
                      <span>{humanize(r.last_run.mode)} · {humanize(r.last_run.status)}{r.last_run.action ? <><br /><span className="muted small">{actionLabel(r.last_run.action)}</span></> : null}</span>
                    ) : <span className="muted">No hearing yet</span>}
                  </td>
                  <td className="cell-action"><a className="btn primary sm" href={`#/case/${encodeURIComponent(r.id)}`}>Open<span className="sr-only"> dispute {r.id}</span></a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section className="section">
        <h2>Recorded hearings</h2>
        <p className="muted">Watch a finished hearing without calling PayPal or a model.</p>
        {replays.error ? <ErrorBox error={replays.error} onRetry={replays.reload} /> : !replays.data ? <Loading /> : replays.data.length === 0 ? (
          <Empty>No recorded hearings yet.</Empty>
        ) : (
          <ul className="list card">
            {replays.data.map((r) => (
              <li key={r.run_id}>
                <a href={`#/run/${r.run_id}`}><strong>{r.replay_name}</strong></a>
                <span className="muted small mono">{r.dispute_id}</span>
                <span className="muted">{humanize(r.status)}{r.action ? ` · ${actionLabel(r.action)}` : ""}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
