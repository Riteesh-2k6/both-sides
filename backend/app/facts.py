"""Snapshot fetching (live), fact building (§4.3) and the snapshot / dispute-list caches."""
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass

from . import agents, config, paypal, shop, store

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Fact:
    id: str
    source: str            # paypal | shop | merchant | check
    label: str
    value: str
    receipt: int | None
    via: str | None


def fact_json(f: Fact) -> dict:
    return {"id": f.id, "source": f.source, "label": f.label, "value": f.value, "receipt": f.receipt, "via": f.via}


def address_key(a: dict) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())  # noqa: E731
    return hashlib.sha256((norm(a.get("address_line_1")) + "|" + norm(a.get("postal_code"))).encode()).hexdigest()[:16]


REFUND_COUNTED = ("COMPLETED", "PENDING")


def money(m) -> str | None:
    if not isinstance(m, dict) or m.get("value") in (None, ""):
        return None
    try:
        return f"{float(m['value']):.2f} {m.get('currency_code', 'USD')}"
    except (TypeError, ValueError):  # a malformed amount is a missing fact, not a 500 on every view
        return None


def cents_money(c, cur="USD") -> str:
    return f"{c / 100:.2f} {cur}"


def incomplete(errors) -> bool:
    """R1: a failed dispute, capture or order read (refund read failures don't count)."""
    return any(str(e.get("call", "")).startswith(("GET /v1/customer/disputes/", "GET /v2/payments/captures/", "GET /v2/checkout/orders/")) for e in errors or [])


# ---------------- facts ----------------

def _jcompact(s):
    try:
        return json.dumps(json.loads(s), separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return s


def _tracker_text(t: dict) -> str:
    """Orders v2 GET returns trackers as {"id": "<capture_id>-<tracking_number>", "status": ...}; carrier fields may be absent."""
    tid = str(t.get("id") or "")
    number = t.get("tracking_number") or (tid.split("-", 1)[1] if "-" in tid else "")  # same rule as shop.tracker_parts
    head = " ".join(x for x in (t.get("carrier"), number) if x)
    return f"{head} ({t['status']})" if t.get("status") else head


def build_facts(snap: dict) -> dict:
    F: dict[str, Fact] = {}
    d, cap, order = snap.get("dispute") or {}, snap.get("capture") or {}, snap.get("order") or {}
    meta, rc = snap.get("meta") or {}, snap.get("receipts") or {}
    now = config.parse_ts(meta.get("now")) or config.utcnow()
    sh = snap.get("shop") or {}

    def add(id, source, label, value, rec=None):
        if value is None or value == "" or value == []:
            return
        F[id] = Fact(id, source, label, str(value), (rec or {}).get("id") if rec else None, (rec or {}).get("via") if rec else None)

    def pp(id, label, value, rec):
        add(id, "paypal", label, value, rec)

    r = rc.get("dispute")
    pp("pp.dispute.id", "Dispute id", d.get("dispute_id"), r)
    pp("pp.dispute.reason", "Dispute reason", d.get("reason"), r)
    pp("pp.dispute.status", "Dispute status", d.get("status"), r)
    pp("pp.dispute.stage", "Dispute stage", d.get("dispute_life_cycle_stage"), r)
    pp("pp.dispute.channel", "Dispute channel", d.get("dispute_channel"), r)
    pp("pp.dispute.amount", "Dispute amount", money(d.get("dispute_amount")), r)
    pp("pp.dispute.created", "Dispute opened", config.iso(d.get("create_time")), r)
    pp("pp.dispute.due", "Response due", config.iso(d.get("seller_response_due_date")), r)
    req = [e.get("evidence_type") for e in d.get("evidences") or [] if e.get("source") == "REQUESTED_FROM_SELLER" and e.get("evidence_type")]
    pp("pp.dispute.requested_evidence", "Evidence requested from seller", ", ".join(req), r)
    pp("pp.dispute.buyer_requested", "Buyer requested amount", money((d.get("offer") or {}).get("buyer_requested_amount")), r)
    tx = (d.get("disputed_transactions") or [{}])[0] or {}
    elig = tx.get("seller_protection_eligible")
    if elig is True:
        pp("pp.dispute.protection", "Seller protection on the dispute", f"eligible ({tx['seller_protection_type']})" if tx.get("seller_protection_type") else "eligible", r)
    elif elig is False:
        pp("pp.dispute.protection", "Seller protection on the dispute", "not eligible", r)

    r = rc.get("capture")
    pp("pp.capture.id", "Capture id", cap.get("id"), r)
    pp("pp.capture.amount", "Capture amount", money(cap.get("amount")), r)
    pp("pp.capture.status", "Capture status", cap.get("status"), r)
    pp("pp.capture.time", "Capture time", config.iso(cap.get("create_time")), r)
    pp("pp.capture.seller_protection", "Seller protection", (cap.get("seller_protection") or {}).get("status"), r)

    r = rc.get("order")
    pu = (order.get("purchase_units") or [{}])[0]
    shipping = pu.get("shipping") or {}
    a = shipping.get("address") or {}
    if a:
        ship_to = f"{a.get('admin_area_2', '')}, {a.get('admin_area_1', '')} {a.get('postal_code', '')}, {a.get('country_code', '')}"
        pp("pp.order.ship_to", "Ship to (city, region)", agents.clean_untrusted(ship_to), r)
    pp("pp.order.items", "Order items", ", ".join(f"{i.get('name')} x{i.get('quantity')}" for i in pu.get("items") or []), r)
    pp("pp.order.tracking", "PayPal tracking", ", ".join(_tracker_text(t) for t in shipping.get("trackers") or []), r)

    for n, rf in enumerate(snap.get("refunds") or [], 1):
        rr = (rc.get("refunds") or [])
        rr = rr[n - 1] if n - 1 < len(rr) else None
        pp(f"pp.refund.{n}.id", f"Refund {n} id", rf.get("id"), rr)
        pp(f"pp.refund.{n}.amount", f"Refund {n} amount", money(rf.get("amount")), rr)
        pp(f"pp.refund.{n}.status", f"Refund {n} status", rf.get("status"), rr)

    p, o, s = sh.get("product"), sh.get("order"), sh.get("shipment")
    if p:
        add("shop.product.name", "shop", "Product name", p.get("name"))
        add("shop.product.category", "shop", "Product category", p.get("category"))
        add("shop.product.kind", "shop", "Product kind", p.get("kind"))
        add("shop.product.color", "shop", "Product color", p.get("color"))
        add("shop.product.price", "shop", "Product price", cents_money(p["price_cents"]) if p.get("price_cents") is not None else None)
        add("shop.product.description", "shop", "Product description", p.get("description"))
    if o:
        add("shop.order.size", "shop", "Size ordered", o.get("size"))
        add("shop.order.via", "shop", "Order channel", o.get("via"))
    if s:
        add("shop.shipment.carrier", "shop", "Carrier", s.get("carrier"))
        add("shop.shipment.tracking", "shop", "Tracking number", s.get("tracking"))
        add("shop.shipment.shipped_at", "shop", "Shipped at", config.iso(s.get("shipped_at")))
        add("shop.shipment.delivered_at", "shop", "Delivered at", config.iso(s.get("delivered_at")) or "not delivered")
        add("shop.shipment.signature", "shop", "Signature on delivery", "yes" if s.get("signature") else "no")
        add("shop.shipment.size_shipped", "shop", "Size shipped", s.get("size_shipped"))
        add("shop.shipment.return_delivered_at", "shop", "Return delivered at", config.iso(s.get("return_delivered_at")))
    for n, rf in enumerate(sh.get("refunds") or [], 1):
        add(f"shop.refund.{n}.id", "shop", f"Refund {n} id", rf.get("paypal_refund_id"))
        add(f"shop.refund.{n}.amount", "shop", f"Refund {n} amount", cents_money(rf["amount_cents"]))
        add(f"shop.refund.{n}.at", "shop", f"Refund {n} date", config.iso(rf.get("created_at")))
    add("shop.policy.returns", "shop", "Returns policy", shop.POLICY_RETURNS)
    add("shop.policy.shipping", "shop", "Shipping policy", shop.POLICY_SHIPPING)
    m = sh.get("mandate")
    if m:
        add("shop.mandate.text", "shop", "Shopper mandate", agents.clean_untrusted(m.get("text") or ""))
        add("shop.mandate.constraints", "shop", "Mandate constraints", agents.clean_untrusted(_jcompact(m.get("constraints")) or "", 300))
        add("shop.mandate.choice", "shop", "Agent choice", agents.clean_untrusted(_jcompact(m.get("choice")) or "", 300))
        add("shop.mandate.reason", "shop", "Agent reason", agents.clean_untrusted(m.get("reason") or "", 300))
        add("shop.mandate.hash", "shop", "Mandate hash", m.get("hash"))
    for n, note in enumerate(sh.get("notes") or [], 1):
        add(f"merchant.note.{n}", "merchant", agents.clean_untrusted(note.get("question") or "", 200), agents.clean_untrusted(note.get("answer") or "", 300))

    add("check.address_match", "check", "Shipped to transaction address", (snap.get("derived") or {}).get("address_match"))
    due = config.parse_ts(d.get("seller_response_due_date"))
    if due:
        add("check.hours_left", "check", "Hours left to respond", f"{(due - now).total_seconds() / 3600:.1f}")
    cur = ((d.get("dispute_amount") or {}).get("currency_code")) or "USD"
    total = 0.0  # a PENDING refund is money already committed; other currencies are flagged by the precheck, not summed
    for rf in snap.get("refunds") or []:
        m = money(rf.get("amount")) if rf.get("status") in REFUND_COUNTED else None
        if m and m.split()[1] == cur:
            total += float(m.split()[0])
    add("check.refunded_total", "check", "Total refunded", f"{total:.2f} {cur}")
    return F


# ---------------- live snapshot ----------------

def _rc(resp):
    if not resp.receipt:
        return None
    row = store.one("SELECT via FROM receipts WHERE id=?", (resp.receipt,))
    return {"id": resp.receipt, "via": row["via"] if row else "rest"}


def _buyer_text(d: dict) -> str:
    items = []
    for m in d.get("messages") or []:
        if m.get("posted_by") == "BUYER" and m.get("content"):
            items.append((m.get("time_posted"), m["content"]))
    for key in ("evidences", "supporting_info"):
        for e in d.get(key) or []:
            if e.get("source") == "SUBMITTED_BY_BUYER" and isinstance(e.get("notes"), str) and e["notes"]:
                items.append((e.get("date") or e.get("create_time") or e.get("time_posted"), e["notes"]))
    far = "9999"
    items.sort(key=lambda t: config.iso(t[0]) or far)
    return "\n".join(t for _, t in items)


def fetch_snapshot(dispute_id: str, now=None) -> dict:
    pp = paypal.PP
    errors, receipts = [], {}

    def bad(call, r):
        errors.append({"call": call, "status": r.status, "debug_id": r.debug_id})

    def ok(r):
        return 0 < r.status < 400

    r = pp.read_tool("get_dispute", {"dispute_id": dispute_id})
    dispute = r.body if ok(r) else {}
    if ok(r):
        receipts["dispute"] = _rc(r)
    else:
        bad(f"GET /v1/customer/disputes/{dispute_id}", r)
    capture_id = ((dispute.get("disputed_transactions") or [{}])[0] or {}).get("seller_transaction_id")
    capture = None
    if capture_id:
        r = pp.get(f"/v2/payments/captures/{capture_id}")
        if ok(r):
            capture, receipts["capture"] = r.body, _rc(r)
        else:
            bad(f"GET /v2/payments/captures/{capture_id}", r)
    order_id = (((capture or {}).get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
    order = None
    if order_id:
        r = pp.read_tool("get_order", {"order_id": order_id})
        if ok(r):
            order, receipts["order"] = r.body, _rc(r)
        else:
            bad(f"GET /v2/checkout/orders/{order_id}", r)

    def find_order():
        row = store.one("SELECT * FROM orders WHERE id=?", (order_id,)) if order_id else None
        if not row and capture_id:
            row = store.one("SELECT * FROM orders WHERE capture_id=?", (capture_id,))
        return row

    orow = find_order()
    pu = ((order or {}).get("purchase_units") or [{}])[0]
    if not orow and order and str(pu.get("custom_id", "")).startswith("np1|"):
        shop.rehydrate(order)  # unmasked order: it needs the street to compute the address key
        orow = find_order()
    shop_part = {"product": None, "order": None, "shipment": None, "refunds": [], "mandate": None, "notes": []}
    refunds = []
    receipts["refunds"] = []
    derived = {}
    if orow:
        if orow.get("dispute_id") != dispute_id:
            store.run("UPDATE orders SET dispute_id=? WHERE id=?", (dispute_id, orow["id"]))
            orow["dispute_id"] = dispute_id
        shop_part["order"] = {k: v for k, v in orow.items() if k != "visitor"}  # the visitor cookie never enters a snapshot
        shop_part["product"] = store.one("SELECT * FROM products WHERE sku=?", (orow["sku"],))
        shop_part["shipment"] = store.one("SELECT * FROM shipments WHERE order_id=?", (orow["id"],))
        shop_part["refunds"] = store.q("SELECT * FROM refunds WHERE order_id=? ORDER BY id", (orow["id"],))
        if orow.get("mandate_id"):
            shop_part["mandate"] = store.one("SELECT * FROM mandates WHERE id=?", (orow["mandate_id"],))
        for sr in shop_part["refunds"]:
            r = pp.get(f"/v2/payments/refunds/{sr['paypal_refund_id']}")
            if ok(r):
                refunds.append(r.body)
                receipts["refunds"].append(_rc(r))
            else:
                bad(f"GET /v2/payments/refunds/{sr['paypal_refund_id']}", r)
        addr = (pu.get("shipping") or {}).get("address")
        if shop_part["shipment"] and addr:
            if shop_part["shipment"]["shipped_to_key"] is not None:  # a failed lookup at ship time is "unknown", not "elsewhere"
                derived["address_match"] = "yes" if shop_part["shipment"]["shipped_to_key"] == address_key(addr) else "no"
    shop_part["notes"] = store.q("SELECT * FROM notes WHERE dispute_id=? ORDER BY id", (dispute_id,))
    dispute, capture, order, refunds = (paypal.mask(x) for x in (dispute, capture, order, refunds))
    return {"dispute": dispute, "capture": capture, "order": order, "refunds": refunds, "shop": shop_part, "derived": derived,
            "buyer_text": _buyer_text(dispute), "receipts": receipts, "errors": errors,
            "meta": {"source": "live", "now": config.iso(now) if now else config.iso(config.utcnow())}}


# ---------------- caches ----------------

SNAP_CACHE: dict[str, tuple[float, dict]] = {}
LIST_CACHE: dict = {"at": 0.0, "items": None}
TTL = 60.0
SNAP_CACHE_MAX = 200
_CACHE_LOCK = threading.Lock()  # separate from store.LOCK


def get_snapshot(dispute_id: str) -> dict:
    with _CACHE_LOCK:
        hit = SNAP_CACHE.get(dispute_id)
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
    s = fetch_snapshot(dispute_id)
    if not any(str(e.get("call", "")).startswith("GET /v1/customer/disputes/") for e in s.get("errors", [])):
        with _CACHE_LOCK:
            SNAP_CACHE.pop(dispute_id, None)
            while len(SNAP_CACHE) >= SNAP_CACHE_MAX:  # oldest first (dicts keep insertion order)
                SNAP_CACHE.pop(next(iter(SNAP_CACHE)))
            SNAP_CACHE[dispute_id] = (time.monotonic(), s)
    return s


def invalidate(dispute_id: str | None = None):
    """Clear one dispute's snapshot cache (or all) and always the dispute-list cache."""
    with _CACHE_LOCK:
        if dispute_id is None:
            SNAP_CACHE.clear()
        else:
            SNAP_CACHE.pop(dispute_id, None)
        LIST_CACHE.update(at=0.0, items=None)


class ListError(Exception):
    def __init__(self, status, debug_id, msg):
        super().__init__(msg)
        self.status, self.debug_id = status, debug_id


def list_disputes() -> list[dict]:
    """First page through read_tool, pages 2 to 4 over REST via links[rel=next] (R13). Cached 60 s."""
    if LIST_CACHE["items"] is not None and time.monotonic() - LIST_CACHE["at"] < TTL:
        return LIST_CACHE["items"]
    items, r = [], paypal.PP.read_tool("list_disputes", {})
    for page in range(4):
        if not (0 < r.status < 400):
            raise ListError(r.status, r.debug_id, f"PayPal list failed ({r.status})")
        items += [i.get("dispute_info", i) for i in r.body.get("items", [])]
        nxt = next((l["href"] for l in r.body.get("links", []) if l.get("rel") == "next"), None)
        if not nxt or page == 3:
            break
        r = paypal.PP.get(nxt)
    LIST_CACHE.update(at=time.monotonic(), items=items)
    return items
