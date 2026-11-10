"""Test doubles: fake_llm, fake_paypal, fixture loaders and verdict builders. No network."""
import copy
import json
import re
from pathlib import Path

import httpx

from app import llm, paypal

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class FakeLLM:
    """Pops one scripted output per step. repeat=True keeps the last one for further calls."""

    def __init__(self, script, repeat=False):
        self.script = {k: list(v) for k, v in script.items()}
        self.repeat = repeat
        self.calls = []

    def __call__(self, step, model_cls, system, user):
        self.calls.append((step, system, user))
        q = self.script.get(step)
        if not q:
            raise llm.LLMUnavailable(step, "no scripted output")
        o = q[0] if (self.repeat and len(q) == 1) else q.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    def prompts(self, *steps):
        return [(s, sy, u) for s, sy, u in self.calls if not steps or s in steps]


def fake_llm(script, repeat=False):
    return FakeLLM(script, repeat)


class FakePayPal(paypal.PayPal):
    pass


def fake_paypal(routes=None, base="https://api-m.sandbox.paypal.com"):
    """routes: {(METHOD, path_regex): value}. value = (status, json) | [values popped in order, last repeats]
    | callable(request) -> (status, json) | an Exception (raised by the transport)."""
    routes = dict(routes or {})
    calls = []

    def handler(request: httpx.Request):
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        ctype = request.headers.get("content-type", "")
        body = None
        if request.content:
            if ctype.startswith("multipart"):
                m = re.search(rb"\r\n\r\n(.*?)\r\n--", request.content, re.S)
                body = json.loads(m.group(1)) if m else None
            else:
                try:
                    body = json.loads(request.content)
                except ValueError:
                    body = None
        calls.append({"method": request.method, "path": path, "query": request.url.query.decode(), "json": body,
                      "kind": "multipart" if ctype.startswith("multipart") else "json", "headers": dict(request.headers)})
        for (method, rx), val in routes.items():
            if method == request.method and re.fullmatch(rx, path):
                if isinstance(val, list):
                    v = val[0] if len(val) == 1 else val.pop(0)
                else:
                    v = val
                if callable(v) and not isinstance(v, Exception):
                    v = v(request)
                if isinstance(v, Exception):
                    raise v
                status, js = v
                return httpx.Response(status, json=js, headers={"Paypal-Debug-Id": "dbg123"})
        return httpx.Response(404, json={"name": "NOT_FOUND"}, headers={"Paypal-Debug-Id": "dbg404"})

    pp = FakePayPal("id", "secret", base, httpx.MockTransport(handler))
    pp.calls = calls
    pp.routes = routes
    return pp


# ---------- builders ----------

def claim(text, ev, kind="fact", resp=None, id="x"):
    return {"id": id, "text": text, "kind": kind, "evidence_ids": list(ev), "responds_to": resp or []}


def verdict(**kw):
    v = {"action": "provide_evidence", "type": None, "amount": None,
         "evidence": [{"fact_id": "shop.shipment.tracking", "evidence_type": "PROOF_OF_FULFILLMENT"}],
         "message": [{"text": "Thank you for your patience.", "fact_ids": [], "courtesy": True},
                     {"text": "The order shipped by India Post with a tracking number on file.", "fact_ids": ["shop.shipment.carrier", "shop.shipment.tracking"], "courtesy": False}],
         "rationale": [], "fault": None, "needs_fact": None}
    v.update(kw)
    from app import agents
    return agents.Verdict.model_validate(v)


def turn_buyer(action="provide_evidence", type=None, amount=None, **kw):
    from app import agents
    d = {"assessments": [{"alleged_id": "A1", "status": "contradicted", "evidence_ids": ["shop.shipment.delivered_at"], "note": "Tracking shows a delivery."}],
         "why_filed": [claim("The buyer may not have seen the parcel arrive.", ["shop.shipment.delivered_at"])],
         "claims": [claim("The buyer reports the parcel as missing.", ["pp.dispute.reason"])],
         "proposal": {"action": action, "type": type, "amount": amount, "why": "Fair outcome."}, "deciding_fact": "Whether the parcel was delivered."}
    d.update(kw)
    return agents.BuyerTurn.model_validate(d)


def turn_merchant(action="provide_evidence", type=None, amount=None, **kw):
    from app import agents
    d = {"claims": [claim("The shop recorded a delivery on the transaction address.", ["shop.shipment.delivered_at", "check.address_match"], "fact", ["B1.1"])],
         "proposal": {"action": action, "type": type, "amount": amount, "why": "Cheapest defensible response."}, "deciding_fact": "Whether the parcel was delivered."}
    d.update(kw)
    return agents.MerchantTurn.model_validate(d)


def quarantine_out(**kw):
    from app import agents
    d = {"stated_reason": "The parcel did not arrive", "requested_outcome": "full_refund", "alleged": [{"id": "A1", "text": "The package never arrived"}]}
    d.update(kw)
    return agents.QuarantineOut.model_validate(d)


def converging_script(action="provide_evidence", type=None, amount=None, v=None):
    v = v or verdict(action=action, type=type, amount=amount)
    return {"quarantine": [quarantine_out()], "buyer_r1": [turn_buyer(action, type, amount)], "merchant_r1": [turn_merchant(action, type, amount)],
            "judge_a": [v], "judge_b": [v.model_copy(deep=True)]}


def snapshot(name="inr_delivered"):
    return copy.deepcopy(load(name))


def routes_for_snapshot(snap, extra=None):
    """REST routes that serve a (masked) fixture snapshot. The order's street is made real so address_key
    can match the shipments row seeded by seed_shop()."""
    d = snap["dispute"]
    did = d["dispute_id"]
    cap = snap["capture"]["id"]
    order = copy.deepcopy(snap["order"])
    order["purchase_units"][0]["shipping"]["address"]["address_line_1"] = "12 Sample Lane"
    r = {
        ("GET", rf"/v1/customer/disputes/{did}"): (200, d),
        ("GET", rf"/v2/payments/captures/{cap}"): (200, snap["capture"]),
        ("GET", rf"/v2/checkout/orders/{order['id']}"): (200, order),
        ("GET", r"/v1/customer/disputes"): (200, {"items": [{"dispute_id": did, "reason": d["reason"], "status": d["status"],
                                                           "dispute_life_cycle_stage": d["dispute_life_cycle_stage"], "dispute_amount": d["dispute_amount"],
                                                           "create_time": d["create_time"], "seller_response_due_date": d.get("seller_response_due_date")}], "links": []}),
    }
    for rf in snap["refunds"]:
        r[("GET", rf"/v2/payments/refunds/{rf['id']}")] = (200, rf)
    r.update(extra or {})
    return r


def seed_shop(snap):
    """Insert the fixture's shop rows into the DB; shipped_to_key matches routes_for_snapshot's address."""
    from app import facts, store
    sh = snap["shop"]
    p = sh["product"]
    store.run("INSERT OR IGNORE INTO products(sku,name,category,kind,color,price_cents,sizes,description) VALUES(?,?,?,?,?,?,?,?)",
              (p["sku"], p["name"], p["category"], p["kind"], p["color"], p["price_cents"], p["sizes"], p["description"]))
    if sh.get("mandate"):
        m = sh["mandate"]
        store.run("INSERT OR IGNORE INTO mandates(id,text,constraints,choice,reason,hash,created_at) VALUES(?,?,?,?,?,?,?)",
                  (m["id"], m["text"], m["constraints"], m["choice"], m["reason"], m["hash"], m["created_at"]))
    o = sh["order"]
    store.run("INSERT OR IGNORE INTO orders(id,status,capture_id,sku,size,amount_cents,via,mandate_id,dispute_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (o["id"], o["status"], o["capture_id"], o["sku"], o["size"], o["amount_cents"], o["via"], o["mandate_id"], None, o["created_at"]))
    s = sh["shipment"]
    if s:
        key = facts.address_key({"address_line_1": "12 Sample Lane", "postal_code": "73301"})
        store.run("INSERT OR IGNORE INTO shipments(order_id,carrier,tracking,shipped_at,shipped_to_key,delivered_at,signature,size_shipped,return_delivered_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (s["order_id"], s["carrier"], s["tracking"], s["shipped_at"], key, s["delivered_at"], s["signature"], s["size_shipped"], s["return_delivered_at"]))
    for r in sh["refunds"]:
        store.run("INSERT OR IGNORE INTO refunds(id,order_id,paypal_refund_id,amount_cents,created_at) VALUES(?,?,?,?,?)",
                  (r["id"], r["order_id"], r["paypal_refund_id"], r["amount_cents"], r["created_at"]))
