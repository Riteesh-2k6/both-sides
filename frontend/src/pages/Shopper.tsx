import { useState } from "react";
import { ApiError, errText, Order, post, ShopperResult } from "../api";
import { Chip, humanize, PayPalButton, useConfig } from "../components";

const PATHS: Record<string, string> = {
  vault: "Saved PayPal wallet (path a)",
  card: "Sandbox test card (path b)",
  human: "Pay with PayPal buttons (path c)",
};

export default function Shopper() {
  const cfg = useConfig();
  const [instruction, setInstruction] = useState("A queen-size indigo quilt under $150");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ShopperResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [approveUrl, setApproveUrl] = useState<string | null>(null);
  const [vaultErr, setVaultErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setErr(null); setNotice(null); setRes(null); setOrder(null);
    try {
      const r = await post<ShopperResult & { error?: string; message?: string }>("/shopper/run", { instruction: instruction.slice(0, 200) });
      if (!r.choice) setNotice(r.message ?? r.error ?? "Nothing in the shop matches");
      else { setRes(r); setOrder(r.order); }
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403 ? e.message : errText(e));
    } finally { setBusy(false); }
  };

  const connect = async () => {
    setVaultErr(null); setApproveUrl(null);
    try {
      const r = await post<any>("/shopper/vault/setup");
      const link = r.approve ?? r.approve_url ?? r.links?.find?.((l: any) => l.rel === "approve" || l.rel === "payer-action")?.href;
      if (link) setApproveUrl(link); else setVaultErr("PayPal didn't return an approval link.");
    } catch (e) { setVaultErr(errText(e)); }
  };

  const fellBack = res?.payment_path === "human" && !!cfg && !cfg.shopper_paths.includes("vault") && !cfg.shopper_paths.includes("card");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Shopper agent</h1>
          <p className="muted">Give a shopping agent an instruction and watch it choose and pay. Sometimes it picks the wrong product, which is what the Bench's agent case is about.</p>
        </div>
      </div>

      <form className="card shopper-form" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <label htmlFor="instr">Instruction</label>
        <textarea id="instr" rows={3} maxLength={200} value={instruction} onChange={(e) => setInstruction(e.target.value)} />
        <div className="form-foot">
          <span className="muted small">{instruction.length}/200</span>
          <button type="submit" className="btn primary" disabled={busy || !instruction.trim()}>{busy ? "Shopping…" : "Run"}</button>
        </div>
      </form>

      {err && <div className="banner banner-bad" role="alert">{err}</div>}
      {notice && <div className="banner banner-info" role="status">{notice}</div>}

      {res && (
        <div className="shopper-out">
          <section className="card">
            <h2>What it understood</h2>
            <div className="chip-row">
              {Object.entries(res.constraints).filter(([, v]) => v != null && v !== "").map(([k, v]) => <Chip key={k} label={`${humanize(k)}: ${v}`} />)}
            </div>
            <h3>Candidates</h3>
            {res.candidates.length === 0 ? <p className="muted">None.</p> : (
              <ul className="list">
                {res.candidates.map((c, i) => (
                  <li key={i}>
                    <strong>{c?.name ?? c?.sku ?? String(c)}</strong>
                    {c?.sku && <span className="mono small muted">{c.sku}</span>}
                    {c?.price != null && <span className="muted">${typeof c.price === "number" ? c.price.toFixed(2) : c.price}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <h2>What it chose</h2>
            <p><strong className="mono">{res.choice.sku}</strong>{res.choice.size ? ` · size ${res.choice.size}` : ""}</p>
            <p>{res.choice.reason}</p>
            <h3>Mandate</h3>
            <p className="muted">{res.mandate.text}</p>
            <p className="mono small muted">hash {res.mandate.hash}</p>
          </section>

          <section className="card">
            <h2>Payment</h2>
            <Chip label={PATHS[res.payment_path] ?? res.payment_path} tone={res.payment_path === "human" ? "warn" : "good"} />
            {fellBack && <p className="muted">The wallet and card paths aren't available here, so the agent falls back to path (c).</p>}
            {order && (
              <dl className="kv">
                <dt>Order</dt><dd className="mono">{order.id}</dd>
                <dt>Item</dt><dd>{order.name}{order.size ? ` · size ${order.size}` : ""}</dd>
                <dt>Amount</dt><dd>{order.amount}</dd>
                <dt>Status</dt><dd><Chip tone={order.status === "captured" ? "good" : "warn"} label={humanize(order.status)} /></dd>
              </dl>
            )}
            {res.payment_path === "human" && order?.status !== "captured" && (
              <>
                <p className="muted">Approve the payment as the sandbox buyer to finish the order.</p>
                <PayPalButton
                  createOrder={async () => res.order.id}
                  onApprove={async (id) => { try { await post(`/shop/orders/${id}/capture`); setOrder({ ...(order ?? res.order), status: "captured" }); } catch (e) { setErr(errText(e)); } }}
                  onError={setErr}
                />
              </>
            )}
            {order?.dispute_id && <p><a href={`#/case/${encodeURIComponent(order.dispute_id)}`}>Open the dispute</a></p>}
          </section>
        </div>
      )}

      {!cfg?.hosted && (
        <section className="section">
          <h2>Shopper wallet</h2>
          <p className="muted">One-time setup so the agent can pay from a saved PayPal wallet (path a). Local only.</p>
          <button type="button" className="btn" onClick={connect}>Connect shopper wallet (one-time)</button>
          {approveUrl && <p><a href={approveUrl} target="_blank" rel="noreferrer">Approve in PayPal (opens a new tab)</a></p>}
          {vaultErr && <div className="banner banner-bad" role="alert">{vaultErr}</div>}
        </section>
      )}
    </div>
  );
}
