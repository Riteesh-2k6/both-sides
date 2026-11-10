"""Catalogue, storefront operations, rehydrate, shopper agent and boot seeding."""
import hashlib
import json
import math
import random
import re
import threading
import uuid
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from . import config, facts, llm, paypal, store

ApiErr = config.ApiErr

POLICY_RETURNS = "Unused items can be returned within 30 days. International return postage is paid by the buyer unless the item is faulty or not as described."
POLICY_SHIPPING = ("Ships from Jaipur in 2–4 business days by India Post (registered/EMS) or DHL, with tracking. International delivery takes 7–21 days; "
                   "tracking can pause after a parcel leaves India. Signature on orders over $750.")
SHOP_RETURN_ADDRESS = {"address_line_1": "21 Block Printers Lane", "admin_area_2": "Jaipur", "admin_area_1": "RJ", "postal_code": "302001", "country_code": "IN"}
SHOP_BILLING_ADDRESS = dict(SHOP_RETURN_ADDRESS)
SHOPPER_SHIP_ADDRESS = {"address_line_1": "12 Sample Lane", "admin_area_2": "Austin", "admin_area_1": "TX", "postal_code": "73301", "country_code": "US"}
SHOPPER_NAME = "Sam Rivera"
SHOP_NAME = "Rangsaaz Prints"  # the shop may name itself in outgoing messages (bare-domain filter ignores it)
# The one place carriers are mapped. INDIA_POST and DHL exist in BOTH PayPal enums (Orders /track `carrier` and Disputes
# `tracking_info.carrier_name`, see tests/fixtures/paypal_enums.json), so the two values are identical; a carrier that
# differed would be listed here with its two spellings.
DEFAULT_CARRIER = "INDIA_POST"
CARRIERS = {"INDIA_POST": {"label": "India Post", "orders": "INDIA_POST", "dispute": "INDIA_POST"},
            "DHL": {"label": "DHL", "orders": "DHL", "dispute": "DHL"}}


def tracking_number(carrier: str) -> str:
    """India Post EMS style EE#########IN; DHL is ten digits."""
    return "EE" + _digits(9) + "IN" if carrier == "INDIA_POST" else _digits(10)


# PayPal's published sandbox Visa test card: https://developer.paypal.com/credit-card-number-generator
TEST_CARD = {"number": "4012888888881881", "expiry": "2030-12", "security_code": "123", "name": SHOPPER_NAME, "billing_address": SHOP_BILLING_ADDRESS}
SEED_TABLES = ("products", "orders", "shipments", "refunds", "mandates")
# ponytail: on Render's free tier shop rows for orders placed since the last restart are lost on restart while their PayPal
# records remain. Pool cases are rebuilt from seed/shop.json and hosted auto-ship orders by rehydrate(); envelope logs and
# actions are lost too, PayPal's own status gate is the backstop. To fix: an external database.


def now_iso() -> str:
    return config.iso(config.utcnow())


def _digits(n: int) -> str:
    return "".join(random.choices("0123456789", k=n))


def money(cents: int) -> str:
    return facts.cents_money(cents)


def _paypal_fail(what: str, r) -> ApiErr:
    return ApiErr(502, f"PayPal {what} failed ({r.status})", paypal=paypal.mask(r.body), debug_id=r.debug_id)


def _post(path, body, request_id):
    """Orders and Payments writes carry PayPal-Request-Id; on status 0 retry once with the same id (R6)."""
    r = paypal.PP.post(path, json=body, request_id=request_id)
    return paypal.PP.post(path, json=body, request_id=request_id) if r.status == 0 else r


# ---------------- boot seeding ----------------

def load_seed(path=None) -> None:
    d = json.loads((path or config.SEED_PATH).read_text(encoding="utf-8"))
    for table in SEED_TABLES:
        for row in d.get(table, []):
            cols = list(row)
            store.run(f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(row[c] for c in cols))


# ---------------- reads ----------------

def products() -> list[dict]:
    return [{"sku": p["sku"], "name": p["name"], "category": p["category"], "kind": p["kind"], "color": p["color"],
             "price": p["price_cents"] / 100, "sizes": json.loads(p["sizes"] or "[]")} for p in store.q("SELECT * FROM products ORDER BY rowid")]


def order_rows(order_id: str | None = None, visitor: str | None = None) -> list[dict]:
    where, args = ("WHERE o.id=?", (order_id,)) if order_id else ("WHERE o.visitor=?", (visitor,)) if visitor else ("", ())
    out = []
    for o in store.q(f"SELECT o.*, p.name FROM orders o LEFT JOIN products p ON p.sku=o.sku {where} ORDER BY o.created_at DESC, o.rowid DESC", args):
        s = store.one("SELECT * FROM shipments WHERE order_id=?", (o["id"],))
        ship = None if not s else {"carrier": s["carrier"], "tracking": s["tracking"], "shipped_at": s["shipped_at"], "delivered_at": s["delivered_at"],
                                   "signature": bool(s["signature"]), "size_shipped": s["size_shipped"], "return_delivered_at": s["return_delivered_at"]}
        refunds = [{"paypal_refund_id": r["paypal_refund_id"], "amount": money(r["amount_cents"]), "created_at": r["created_at"]}
                   for r in store.q("SELECT * FROM refunds WHERE order_id=? ORDER BY id", (o["id"],))]
        out.append({"id": o["id"], "status": o["status"], "capture_id": o["capture_id"], "sku": o["sku"], "name": o["name"], "size": o["size"],
                    "amount": money(o["amount_cents"]), "via": o["via"], "created_at": o["created_at"], "shipment": ship, "refunds": refunds, "dispute_id": o["dispute_id"]})
    return out


def _order(order_id: str) -> dict:
    o = store.one("SELECT * FROM orders WHERE id=?", (order_id,))
    if not o:
        raise ApiErr(404, "Order not found")
    return o


def order_row(order_id: str) -> dict:
    return order_rows(order_id)[0]


# ---------------- create / capture ----------------

def order_body(prod: dict, size, custom_id: str, payment_source: dict | None = None, shipping: dict | None = None) -> dict:
    price = "%.2f" % (prod["price_cents"] / 100)
    physical = prod["kind"] == "physical"
    unit = {"currency_code": "USD", "value": price}
    pu = {"custom_id": custom_id, "amount": {"currency_code": "USD", "value": price, "breakdown": {"item_total": dict(unit)}},
          "items": [{"name": f"{prod['name']} (size {size})" if size else prod["name"], "sku": prod["sku"], "quantity": "1", "unit_amount": dict(unit),
                     "category": "PHYSICAL_GOODS" if physical else "DIGITAL_GOODS"}]}
    if shipping:
        pu["shipping"] = shipping
    src = payment_source or {"paypal": {"experience_context": {"shipping_preference": "GET_FROM_FILE" if physical else "NO_SHIPPING"}}}
    return {"intent": "CAPTURE", "purchase_units": [pu], "payment_source": src}


def create_order(sku: str, size, visitor: str | None, *, via="storefront", mandate_id=None, payment_source=None, shipping=None, flags=None) -> tuple[str, dict]:
    prod = store.one("SELECT * FROM products WHERE sku=?", (sku,))
    if not prod:
        raise ApiErr(404, "Unknown product")
    sizes = json.loads(prod["sizes"] or "[]")
    if (sizes and size not in sizes) or (not sizes and size is not None):
        raise ApiErr(400, "Invalid size for this product")
    if flags is None:
        flags = "sd" if config.S.HOSTED else ""
    custom_id = f"np1|{sku}|{size or '-'}|{via}|{flags}"[:127]
    r = _post("/v2/checkout/orders", order_body(prod, size, custom_id, payment_source, shipping), f"np-create-{uuid.uuid4()}")
    if not (200 <= r.status < 300) or not r.body.get("id"):
        raise _paypal_fail("order creation", r)
    oid = r.body["id"]
    store.run("INSERT OR REPLACE INTO orders(id,status,sku,size,amount_cents,via,mandate_id,created_at,visitor) VALUES(?,?,?,?,?,?,?,?,?)",
              (oid, "created", sku, size, prod["price_cents"], via, mandate_id, now_iso(), visitor))
    return oid, r.body


def _capture_from(body: dict):
    for c in ((((body.get("purchase_units") or [{}])[0]).get("payments") or {}).get("captures")) or []:
        if c.get("status", "COMPLETED") == "COMPLETED":
            return c["id"]
    return None


def mark_captured(order_id: str, body: dict) -> bool:
    cid = _capture_from(body)
    if cid:
        store.run("UPDATE orders SET status='captured', capture_id=? WHERE id=?", (cid, order_id))
    return bool(cid)


def capture(order_id: str) -> dict:
    o = _order(order_id)
    if o["status"] == "captured":
        raise ApiErr(409, "Order is already captured")
    r = _post(f"/v2/checkout/orders/{order_id}/capture", None, f"np-capture-{order_id}")
    if not (200 <= r.status < 300 and mark_captured(order_id, r.body)):
        # R6: after ORDER_ALREADY_CAPTURED, or a network error even after the retry, the order itself says what happened
        if r.status == 0 or (r.status == 422 and "ORDER_ALREADY_CAPTURED" in json.dumps(r.body)):
            g = paypal.PP.get(f"/v2/checkout/orders/{order_id}")
            if 200 <= g.status < 300 and mark_captured(order_id, g.body):
                r = None
        if r is not None:
            raise _paypal_fail("capture", r)
    if config.S.HOSTED:
        autoship(order_id)
    return order_row(order_id)


def autoship(order_id: str) -> None:
    if not store.one("SELECT 1 AS x FROM shipments WHERE order_id=?", (order_id,)):
        ship(order_id)
        deliver(order_id)


VAULT_MISSING = ("SHOPPER_VAULT_TOKEN is not set. Run POST /api/shopper/vault/setup (local only), approve the wallet link as the sandbox buyer, "
                 "and put the token it shows in .env.")


def place_order(sku, size, visitor, *, pay="vault", via="seed", mandate_id=None, flags="") -> str:
    """Order paid from the buyer's vaulted PayPal wallet (default; this merchant cannot take cards) or with the test card,
    shipped to SHOPPER_SHIP_ADDRESS, and captured at once or by an explicit capture."""
    if pay == "vault":
        if not config.S.SHOPPER_VAULT_TOKEN:
            raise ApiErr(400, VAULT_MISSING)
        src = {"paypal": {"vault_id": config.S.SHOPPER_VAULT_TOKEN}}
    elif pay == "card":
        src = {"card": dict(TEST_CARD)}
    else:
        raise ApiErr(400, "pay must be vault or card")
    oid, body = create_order(sku, size, visitor, via=via, mandate_id=mandate_id, flags=flags, payment_source=src,
                             shipping={"name": {"full_name": SHOPPER_NAME}, "address": SHOPPER_SHIP_ADDRESS})
    if not mark_captured(oid, body):
        cap = _post(f"/v2/checkout/orders/{oid}/capture", None, f"np-capture-{oid}")
        if not (200 <= cap.status < 300 and mark_captured(oid, cap.body)):
            raise _paypal_fail("capture", cap)
    return oid


# ---------------- fulfilment simulation ----------------

def ship(order_id: str, carrier: str = DEFAULT_CARRIER, size_shipped=None) -> dict:
    o = _order(order_id)
    if o["status"] != "captured":
        raise ApiErr(409, "Order is not captured")
    if store.one("SELECT 1 AS x FROM shipments WHERE order_id=?", (order_id,)):
        raise ApiErr(409, "Order is already shipped")
    if carrier not in CARRIERS:
        raise ApiErr(400, "Unknown carrier")
    tracking = tracking_number(carrier)
    t = paypal.PP.post(f"/v2/checkout/orders/{order_id}/track", json={"capture_id": o["capture_id"], "tracking_number": tracking, "carrier": CARRIERS[carrier]["orders"], "notify_payer": False})
    # ponytail: a failed /track still logs the shipment locally (the shop's shipping log is the tracking source of record, spike S2)
    g = paypal.PP.get(f"/v2/checkout/orders/{order_id}")
    addr = (((g.body.get("purchase_units") or [{}])[0]).get("shipping") or {}).get("address") if 0 < g.status < 400 else None
    store.run("INSERT INTO shipments(order_id,carrier,tracking,shipped_at,shipped_to_key,delivered_at,signature,size_shipped,return_delivered_at) VALUES(?,?,?,?,?,NULL,0,?,NULL)",
              (order_id, carrier, tracking, now_iso(), facts.address_key(addr) if addr else None, size_shipped or o["size"]))
    return order_row(order_id)


def _shipment(order_id: str) -> dict:
    _order(order_id)
    s = store.one("SELECT * FROM shipments WHERE order_id=?", (order_id,))
    if not s:
        raise ApiErr(409, "Order is not shipped")
    return s


def deliver(order_id: str, elsewhere: bool = False, signature: bool = False) -> dict:
    _shipment(order_id)
    store.run("UPDATE shipments SET delivered_at=?, signature=? WHERE order_id=?", (now_iso(), int(bool(signature)), order_id))
    if elsewhere:
        store.run("UPDATE shipments SET shipped_to_key=? WHERE order_id=?", (facts.address_key(SHOP_RETURN_ADDRESS), order_id))
    return order_row(order_id)


def return_delivered(order_id: str) -> dict:
    _shipment(order_id)
    store.run("UPDATE shipments SET return_delivered_at=? WHERE order_id=?", (now_iso(), order_id))
    return order_row(order_id)


_ORDER_LOCKS: dict[str, threading.Lock] = {}


def refund(order_id: str, amount) -> dict:
    with store.LOCK:
        lock = _ORDER_LOCKS.setdefault(order_id, threading.Lock())
    with lock:  # one refund at a time per order, so two clicks cannot both pass the balance check
        o = _order(order_id)
        if o["status"] != "captured":
            raise ApiErr(409, "Order is not captured")
        try:
            val = float(amount)
            cents = round(val * 100) if math.isfinite(val) else 0
        except (TypeError, ValueError):
            raise ApiErr(400, "Invalid amount")
        prior = store.q("SELECT amount_cents FROM refunds WHERE order_id=?", (order_id,))
        if cents <= 0 or cents > o["amount_cents"] - sum(r["amount_cents"] for r in prior):
            raise ApiErr(400, "Refund amount must be above 0 and within the unrefunded balance")
        r = _post(f"/v2/payments/captures/{o['capture_id']}/refund", {"amount": {"currency_code": "USD", "value": "%.2f" % (cents / 100)}}, f"np-refund-{order_id}-{len(prior) + 1}")
        if not (200 <= r.status < 300) or not r.body.get("id"):
            raise _paypal_fail("refund", r)
        if not store.one("SELECT id FROM refunds WHERE paypal_refund_id=?", (r.body["id"],)):
            store.run("INSERT INTO refunds(order_id,paypal_refund_id,amount_cents,created_at) VALUES(?,?,?,?)", (order_id, r.body["id"], cents, now_iso()))
        facts.invalidate(o.get("dispute_id"))  # the next hearing must see this refund, not a cached snapshot
        return order_row(order_id)


def _find(o, key):
    if isinstance(o, dict):
        if key in o and o[key]:
            return o[key]
        for v in o.values():
            if (f := _find(v, key)):
                return f
    elif isinstance(o, list):
        for v in o:
            if (f := _find(v, key)):
                return f
    return None


def chargeback(order_id: str, reason_code: str | None = None) -> dict:
    o = _order(order_id)
    if o["status"] != "captured":
        raise ApiErr(409, "Order is not captured")
    if o["dispute_id"]:
        raise ApiErr(409, "This order already has a dispute")
    amt = {"currency_code": "USD", "value": "%.2f" % (o["amount_cents"] / 100)}
    now = config.utcnow()
    body = {"adjacency": "PAYPAL", "file_layout": "ATCK_CB", "merchant_id": config.S.PAYPAL_MERCHANT_ID, "financial_institution": {"processor": "FDMS"},
            "transaction": {"id_enc": o["capture_id"], "amount": amt},
            "instrument": {"instrument_type": "CARD", "card_brand": config.S.CHARGEBACK_CARD_BRAND, "credit_card_transaction_id": _digits(16), "external": False},
            "dispute": {"id": None, "stage": "CHARGEBACK", "status": "2", "amount": amt, "receive_date": config.iso(now), "money_movement_date": config.iso(now),
                        "response_date": config.iso(now + timedelta(days=10)), "event_code": "CHARGEBACK_INITIATED", "event_type": "DEBIT", "chargeback_type": "REPORTED_TO_PROCESSOR",
                        "processor_info": {"reference_number": _digits(23), "reason_code": reason_code or config.S.CHARGEBACK_REASON_CODE, "notes": "TEST_MEMO"}}}
    r = paypal.PP.post("/v2/customer-support/process-chargeback", json=body)
    if not (200 <= r.status < 300):
        raise _paypal_fail("chargeback", r)
    did = _find(r.body, "dispute_id")
    if did:
        store.run("UPDATE orders SET dispute_id=? WHERE id=?", (did, order_id))
    return {"order": order_row(order_id), "dispute_id": did, "paypal": paypal.mask(r.body)}


# ---------------- rehydrate (Render restarts) ----------------

def tracker_parts(tr: dict) -> tuple[str, str]:
    """(carrier key, tracking number) from an Orders v2 tracker. Live GETs return only id "<capture>-<number>" + status,
    so the number falls back to the id and the carrier to the shop default (S2, 10 Nov)."""
    tid = str(tr.get("id") or "")
    number = tr.get("tracking_number") or (tid.split("-", 1)[1] if "-" in tid else "")  # no "-": the id is not a tracking number
    carrier = next((k for k, c in CARRIERS.items() if tr.get("carrier") in (k, c["orders"])), DEFAULT_CARRIER)
    return carrier, number


def rehydrate(order: dict) -> None:
    pu = (order.get("purchase_units") or [{}])[0]
    parts = str(pu.get("custom_id", "")).split("|")
    if len(parts) < 5 or parts[0] != "np1":
        return
    _, sku, size, via, flags = parts[:5]
    cap = (((pu.get("payments") or {}).get("captures")) or [{}])[0]
    cents = round(float((pu.get("amount") or {}).get("value", 0)) * 100)
    store.run("INSERT OR IGNORE INTO orders(id,status,capture_id,sku,size,amount_cents,via,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (order["id"], "captured", cap.get("id"), sku, None if size == "-" else size, cents, via, config.iso(cap.get("create_time")) or now_iso()))
    tr = ((pu.get("shipping") or {}).get("trackers") or [None])[0]
    if "s" in flags and tr:
        shipped = config.iso(cap.get("create_time")) or now_iso()
        addr = (pu.get("shipping") or {}).get("address") or {}
        store.run("INSERT OR IGNORE INTO shipments(order_id,carrier,tracking,shipped_at,shipped_to_key,delivered_at,signature,size_shipped,return_delivered_at) VALUES(?,?,?,?,?,?,0,?,NULL)",
                  (order["id"], *tracker_parts(tr), shipped, facts.address_key(addr), shipped if "d" in flags else None,
                   None if size == "-" else size))


# ---------------- shopper agent ----------------

class Constraints(BaseModel):
    category: Literal["scarf", "quilt", "cushion", "pattern"] | None = None
    color: str | None = Field(default=None, max_length=30)
    size: str | None = None
    max_price: float | None = Field(default=None, allow_inf_nan=False)


class Choice(BaseModel):
    sku: str
    size: str | None
    reason: str = Field(max_length=200)


CONSTRAINTS_SYSTEM = ("Extract shopping constraints from the shopper's instruction. category is one of scarf, quilt, cushion, pattern, or null if not stated; "
                      "color, size (as text) and max_price (a number) are null if not stated. Return JSON only.")
CHOICE_SYSTEM = ("You are a shopping agent buying for a shopper. Pick the one product from CANDIDATES that best fits the instruction, and a size from its sizes "
                 "(null if it has none). Explain the choice in one sentence. Return JSON only.")


def shopper_paths() -> list[str]:
    s = config.S
    return (["vault"] if s.SHOPPER_VAULT_TOKEN else []) + (["card"] if s.SHOPPER_TEST_CARD else []) + ["human"]


def _size_tokens(s: str) -> set[str]:
    s = s.lower().replace('"', " in ").replace("inches", "in").replace("inch", "in")
    return set(re.findall(r"\d+(?:\.\d+)?|[a-z0-9]+", s))  # "5.5" stays one token


def match_size(wanted: str | None, sizes: list[str]) -> str | None:
    """Map loose shopper text ("queen bed", "16 inch") to a catalogue size, or None."""
    if not wanted or not sizes:
        return None
    w = _size_tokens(wanted)
    fits = [s for s in sizes if _size_tokens(s) and _size_tokens(s) <= w]
    if not fits:  # "18" for "18 in": match on the numbers alone when exactly one size has them
        nums = {t for t in w if t[0].isdigit()}
        fits = [s for s in sizes if nums and {t for t in _size_tokens(s) if t[0].isdigit()} == nums]
        return fits[0] if len(fits) == 1 else None
    best = max(len(_size_tokens(s)) for s in fits)
    top = [s for s in fits if len(_size_tokens(s)) == best]
    return top[0] if len(top) == 1 else None  # "twin xl" picks "Twin XL"; "king queen" is ambiguous


def run_shopper(instruction: str, visitor: str | None) -> dict:
    text = " ".join("".join(c if c >= " " else " " for c in (instruction or "")).split())[:200]
    block = f"<<INSTRUCTION\n{text}\nINSTRUCTION>>"
    cons, _ = llm.chat_json(Constraints, CONSTRAINTS_SYSTEM, block, step="shopper_constraints")
    cands = [p for p in products() if (cons.color is None or (p["color"] or "").lower() == cons.color.lower())
             and (cons.size is None or not p["sizes"] or match_size(cons.size, p["sizes"]))
             and (cons.max_price is None or p["price"] <= cons.max_price)]
    out = {"constraints": cons.model_dump(), "candidates": cands, "choice": None, "mandate": None, "payment_path": None, "order": None}
    if not cands:
        return {**out, "message": "Nothing in the shop matches"}
    lines = [f"{p['sku']} | {p['name']} | {p['category']} | {p['color']} | {p['price']:.2f} | {', '.join(p['sizes'])}" for p in cands]
    choice, _ = llm.chat_json(Choice, CHOICE_SYSTEM, block + "\n\nCANDIDATES\n" + "\n".join(lines), step="shopper_choice")
    pick = next((p for p in cands if p["sku"] == choice.sku), None)
    if not pick:
        raise ApiErr(422, "The shopping agent picked a product that is not among the candidates")
    size = match_size(choice.size, pick["sizes"]) if pick["sizes"] else None
    if pick["sizes"] and size is None:
        raise ApiErr(422, "The shopping agent picked a size the product does not have")
    cj = json.dumps({"sku": choice.sku, "size": size, "reason": choice.reason}, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256((text + "|" + cj).encode()).hexdigest()
    mid = store.run("INSERT INTO mandates(text,constraints,choice,reason,hash,created_at) VALUES(?,?,?,?,?,?)",
                    (text, json.dumps(cons.model_dump(), sort_keys=True), cj, choice.reason, h, now_iso()))
    path = shopper_paths()[0]
    flags = "sd" if config.S.HOSTED else ""
    if path in ("vault", "card"):
        oid = place_order(pick["sku"], size, visitor, pay=path, via="agent", mandate_id=mid, flags=flags)
        if config.S.HOSTED:
            autoship(oid)
    else:
        oid, _ = create_order(pick["sku"], size, visitor, via="agent", mandate_id=mid, flags=flags)
    return {**out, "choice": {"sku": choice.sku, "size": size, "reason": choice.reason}, "mandate": {"id": mid, "text": text, "hash": h},
            "payment_path": {"vault": "vault", "card": "card", "human": "human"}[path], "order": order_row(oid)}


# ---------------- vault (local only) ----------------

def vault_setup() -> dict:
    base = config.S.APP_BASE_URL.rstrip("/")
    r = paypal.PP.post("/v3/vault/setup-tokens", json={"payment_source": {"paypal": {"usage_type": "MERCHANT", "customer_type": "CONSUMER", "experience_context": {
        "return_url": base + "/api/shopper/vault/return", "cancel_url": base + "/#/shopper"}}}})
    if not (200 <= r.status < 300):
        raise _paypal_fail("vault setup", r)
    link = next((l["href"] for l in r.body.get("links", []) if l.get("rel") == "approve"), None)
    return {"approve_url": link, "setup_token_id": r.body.get("id")}


def vault_return(approval_token_id: str) -> str:
    r = paypal.PP.post("/v3/vault/payment-tokens", json={"payment_source": {"token": {"id": approval_token_id, "type": "SETUP_TOKEN"}}})
    if not (200 <= r.status < 300) or not r.body.get("id"):
        raise _paypal_fail("vault token", r)
    return r.body["id"]
