import { useEffect, useRef, useState } from "react";
import { errText, Order, post, Product } from "../api";
import { Chip, Empty, ErrorBox, fmtTime, humanize, Loading, parseMoney, PayPalButton, useConfig } from "../components";
import { usePoll } from "../usePoll";

function ProductCard({ p, onCaptured, onError }: { p: Product; onCaptured: () => void; onError: (m: string) => void }) {
  const sizes = p.sizes && p.sizes.length ? p.sizes : null;
  const [size, setSize] = useState<string>(sizes ? sizes[0] : "");
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const sel = `size-${p.sku}`;
  return (
    <article className="card product">
      <div className="product-top">
        <h3>{p.name}</h3>
        <Chip label={p.kind === "digital" ? "Digital" : humanize(p.category)} />
      </div>
      <p className="price">${typeof p.price === "number" ? p.price.toFixed(2) : p.price}</p>
      <p className="muted small">{[p.color, p.kind === "digital" ? "Delivered by email" : "Ships from Jaipur with tracking"].filter(Boolean).join(" · ")}</p>
      {sizes && (
        <label className="inline" htmlFor={sel}>Size
          <select id={sel} value={size} onChange={(e) => setSize(e.target.value)}>
            {sizes.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      )}
      <PayPalButton
        createOrder={async () => (await post<{ id: string }>("/shop/orders", { sku: p.sku, size: sizes ? sizeRef.current : null })).id}
        onApprove={async (orderId) => { try { await post(`/shop/orders/${orderId}/capture`); onCaptured(); } catch (e) { onError(errText(e)); } }}
        onError={onError}
      />
    </article>
  );
}

function shipText(o: Order): string {
  const s = o.shipment;
  if (!s) return o.status === "captured" ? "Not shipped" : "—";
  if (s.return_delivered_at) return "Return delivered";
  if (s.delivered_at) return "Delivered";
  return "Shipped";
}

function OrderRow({ o, hosted, chargeback, onChange, onError }: { o: Order; hosted: boolean; chargeback: boolean; onChange: (o: Order) => void; onError: (m: string | null) => void }) {
  const [busy, setBusy] = useState(false);
  const [refunding, setRefunding] = useState(false);
  const [amt, setAmt] = useState(String(parseMoney(o.amount)?.value ?? ""));
  const run = async (fn: () => Promise<Order>) => {
    setBusy(true); onError(null);
    try { onChange(await fn()); } catch (e) { onError(errText(e)); } finally { setBusy(false); }
  };
  const act = (path: string, body: unknown = {}) => run(() => post<Order>(`/shop/orders/${o.id}/${path}`, body));
  const s = o.shipment;
  const captured = o.status === "captured";
  return (
    <tr>
      <td data-label="Order"><span className="mono small">{o.id}</span><div className="muted small">{fmtTime(o.created_at)}</div></td>
      <td data-label="Item">{o.name}{o.size ? ` · size ${o.size}` : ""}<div className="muted small">via {o.via}{s?.size_shipped && s.size_shipped !== o.size ? ` · shipped size ${s.size_shipped}` : ""}</div></td>
      <td data-label="Amount">{o.amount}{o.refunds.length > 0 && <div className="muted small">Refunded {o.refunds.map((r) => r.amount).join(", ")}</div>}</td>
      <td data-label="Status"><Chip tone={captured ? "good" : "neutral"} label={humanize(o.status)} /> <span className="small">{shipText(o)}</span></td>
      <td data-label="Dispute">{o.dispute_id ? <a href={`#/case/${encodeURIComponent(o.dispute_id)}`}>Open dispute</a> : <span className="muted">—</span>}</td>
      <td className="cell-wide">
        {hosted ? <span className="muted small">Fulfilment is automatic on the hosted demo.</span> : (
          <div className="chip-row">
            <button type="button" className="btn sm" disabled={busy || !captured || !!s} onClick={() => act("ship", {})}>Ship</button>
            <button type="button" className="btn sm" disabled={busy || !s || !!s.delivered_at} onClick={() => act("deliver", { elsewhere: false, signature: false })}>Mark delivered</button>
            <button type="button" className="btn sm" disabled={busy || !s || !!s.delivered_at} onClick={() => act("deliver", { elsewhere: true, signature: false })}>Delivered elsewhere</button>
            <button type="button" className="btn sm" disabled={busy || !s || !!s.return_delivered_at} onClick={() => act("return-delivered")}>Return delivered</button>
            <button type="button" className="btn sm" disabled={busy || !captured} onClick={() => setRefunding(!refunding)} aria-expanded={refunding}>Refund</button>
            {chargeback && (
              <button type="button" className="btn sm" disabled={busy || !captured} onClick={() => run(async () => (await post<{ order: Order }>(`/shop/orders/${o.id}/chargeback`, {})).order)}>Open chargeback</button>
            )}
            {refunding && (
              <form className="inline-form" onSubmit={(e) => { e.preventDefault(); act("refund", { amount: Number(amt) }).then(() => setRefunding(false)); }}>
                <label className="sr-only" htmlFor={`rf-${o.id}`}>Refund amount</label>
                <input id={`rf-${o.id}`} type="number" min="0.01" step="0.01" value={amt} onChange={(e) => setAmt(e.target.value)} />
                <button type="submit" className="btn sm primary" disabled={busy || !(Number(amt) > 0)}>Send refund</button>
              </form>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

export default function Shop() {
  const cfg = useConfig();
  const products = usePoll<Product[]>("/shop/products", 0);
  const orders = usePoll<Order[]>("/shop/orders", 0);
  const [rows, setRows] = useState<Order[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (orders.data) setRows(orders.data); }, [orders.data]);

  const patch = (o: Order) => setRows((r) => (r ?? []).map((x) => (x.id === o.id ? o : x)));
  const hosted = !!cfg?.hosted;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Rangsaaz Prints</h1>
          <p className="muted">Hand block-printed textiles from Jaipur, shipped worldwide. Pay with a PayPal sandbox buyer account, then open a dispute from that account to see it in the Inbox.</p>
        </div>
      </div>
      {err && <div className="banner banner-bad" role="alert"><div>{err}</div><button type="button" className="btn sm" onClick={() => setErr(null)}>Dismiss</button></div>}

      {products.error ? <ErrorBox error={products.error} onRetry={products.reload} /> : !products.data ? <Loading label="Loading the catalogue…" /> : products.data.length === 0 ? (
        <Empty>The shop has no products yet.</Empty>
      ) : (
        <div className="grid">{products.data.map((p) => <ProductCard key={p.sku} p={p} onError={setErr} onCaptured={() => orders.reload()} />)}</div>
      )}

      <section className="section">
        <h2>Orders</h2>
        {orders.error && !rows ? <ErrorBox error={orders.error} onRetry={orders.reload} /> : !rows ? <Loading label="Loading orders…" /> : rows.length === 0 ? (
          <Empty>No orders yet. Buy something above.</Empty>
        ) : (
          <div className="card flush">
            <table className="rtable">
              <thead><tr><th>Order</th><th>Item</th><th>Amount</th><th>Status</th><th>Dispute</th><th>{hosted ? <span className="sr-only">Actions</span> : "Actions"}</th></tr></thead>
              <tbody>{rows.map((o) => <OrderRow key={o.id} o={o} hosted={hosted} chargeback={!!cfg?.chargeback_enabled} onChange={patch} onError={setErr} />)}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

