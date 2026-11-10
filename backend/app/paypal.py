"""PayPal client, read_tool (Agent Toolkit with REST fallback), request builders and mask()."""
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from . import config, shop, store

RETRY_SLEEP = 1.0  # seconds before the single GET retry (tests set 0)
OFFER_TYPES_WITH_AMOUNT = ("REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT")


class PayPalAuthError(Exception):
    pass


@dataclass
class Resp:
    status: int
    body: dict
    debug_id: str | None
    receipt: int


# ---------------- mask (by key name only) ----------------

_INITIAL = {"given_name", "surname", "full_name", "alternate_full_name"}
_STREET = {"address_line_1", "address_line_2", "address_line_3"}
_EMAIL_KEYS = {"email_address", "email"}
_SECRET = {"national_number", "payer_id", "account_id", "merchant_id", "access_token", "refresh_token", "client_secret", "authorization"}
_FREE = {"content", "notes", "note", "message"}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")


def _initial(s: str) -> str:
    s = s.strip()
    return s[0] + "." if s else s


def mask(obj, _parent=None):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _INITIAL and isinstance(v, str):
                out[k] = _initial(v)
            elif kl in _STREET and v is not None:
                out[k] = "•••"
            elif kl in _EMAIL_KEYS and isinstance(v, str) and v:
                out[k] = v[0] + "•••@•••"
            elif kl in _SECRET and v is not None:
                out[k] = "•••"
            elif kl == "name" and isinstance(v, str) and _parent in ("buyer", "seller"):
                out[k] = _initial(v)
            elif kl in _FREE and isinstance(v, str):
                out[k] = _PHONE_RE.sub("•••", _EMAIL_RE.sub("•••", v))
            else:
                out[k] = mask(v, str(k).lower())
        return out
    if isinstance(obj, list):
        return [mask(x, _parent) for x in obj]
    return obj


# ---------------- client ----------------

def _receipt(via, method, path, status, debug_id, body) -> int:
    if store._db is None:
        return 0
    dump = store.jdump(mask(body))
    if len(dump) > 200_000:  # keep the stored body valid JSON
        dump = store.jdump({"truncated": True, "head": dump[:200_000]})
    return store.run("INSERT INTO receipts(via,method,path,status,debug_id,body,created_at) VALUES(?,?,?,?,?,?,?)",
                     (via, method, path, status, debug_id, dump, config.iso(config.utcnow())))


class PayPal:
    def __init__(self, client_id, secret, base=None, transport: httpx.BaseTransport | None = None):
        self.client_id, self.secret = client_id, secret
        self.base = (base or config.S.PAYPAL_BASE).rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=20)
        self._tok, self._exp = None, 0.0
        self._tlock = threading.Lock()

    def token(self) -> str:
        with self._tlock:
            if self._tok and time.monotonic() < self._exp:
                return self._tok
            try:
                r = self._http.post(f"{self.base}/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=(self.client_id, self.secret),
                                    headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise PayPalAuthError(str(e)) from e
            if r.status_code != 200:
                raise PayPalAuthError(f"token request failed with {r.status_code}")
            j = r.json()
            self._tok = j["access_token"]
            self._exp = time.monotonic() + float(j.get("expires_in", 300)) - 60
            return self._tok

    def _once(self, method, url, params, json_body, multipart_input, request_id):
        def go():
            h = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
            if request_id:
                h["PayPal-Request-Id"] = request_id
            kw = {"params": params}
            if multipart_input is not None:
                kw["files"] = {"input": ("input.json", json.dumps(multipart_input), "application/json")}
            elif json_body is not None:
                kw["json"] = json_body
            return self._http.request(method, url, headers=h, **kw)
        r = go()
        if r.status_code == 401:
            self._tok = None  # refresh the token once
            r = go()
        return r

    def _call(self, method, path, params=None, json=None, multipart_input=None, request_id=None) -> Resp:
        if "://" in path:
            u, b = urlparse(path), urlparse(self.base)
            if u.scheme != "https" or u.netloc != b.netloc:
                return Resp(0, {"error": "url outside PayPal base"}, None, 0)
            url, short = path, u.path + (f"?{u.query}" if u.query else "")
        else:
            url, short = self.base + path, path
        tries = 2 if method == "GET" else 1  # R1: GETs (not POSTs) retry once on 429, 5xx or a network error
        for i in range(tries):
            try:
                r = self._once(method, url, params, json, multipart_input, request_id)
                status, debug = r.status_code, r.headers.get("Paypal-Debug-Id")
                try:
                    body = r.json() if r.content else {}
                except ValueError:
                    body = {"raw": r.text[:500]}
                if not isinstance(body, dict):
                    body = {"items": body}
            except PayPalAuthError as e:
                return Resp(0, {"error": f"auth: {e}"}, None, 0)  # R10: no receipt row
            except httpx.HTTPError as e:
                status, debug, body = 0, None, {"error": str(e) or type(e).__name__}
            if i + 1 < tries and (status in (0, 429) or status >= 500):
                time.sleep(RETRY_SLEEP)
                continue
            break
        return Resp(status, body, debug, _receipt("rest", method, short, status, debug, body))

    def get(self, path, params=None) -> Resp:
        return self._call("GET", path, params=params)

    def post(self, path, *, json=None, multipart_input=None, request_id=None) -> Resp:
        return self._call("POST", path, json=json, multipart_input=multipart_input, request_id=request_id)

    def read_tool(self, name: Literal["get_dispute", "get_order", "list_disputes"], args: dict) -> Resp:
        if config.S.USE_AGENT_TOOLKIT:
            try:
                d = json.loads(_toolkit_call(name, args))
                ok = isinstance(d, dict) and all(k in d for k in _REQUIRED[name]) and "error" not in d and not ("name" in d and "message" in d)
            except Exception:  # noqa: BLE001 - any toolkit failure means REST
                ok = False
            if ok:
                if name == "list_disputes":
                    d.setdefault("items", [])
                rid = _receipt("agent-toolkit", "TOOL", f"agent-toolkit:{name} {json.dumps(args)}", 200, None, d)
                return Resp(200, d, None, rid)
        if name == "get_dispute":
            return self.get(f"/v1/customer/disputes/{args['dispute_id']}")
        if name == "get_order":
            return self.get(f"/v2/checkout/orders/{args['order_id']}")
        return self.get("/v1/customer/disputes", params={"page_size": 50})


_REQUIRED = {"get_dispute": ("dispute_id", "links"), "get_order": ("id", "purchase_units"), "list_disputes": ()}
# ponytail: paypal-agent-toolkit 1.1.0 ships no dispute tools (only orders, invoices, subscriptions), so get_dispute and
# list_disputes always raise here and fall back to REST. Only get_order maps to a real tool (get_order_details).
_TOOLKIT_METHODS = {"get_order": "get_order_details"}


def _toolkit_call(name: str, args: dict) -> str:
    s = config.S
    if not s.PAYPAL_CLIENT_ID:
        raise RuntimeError("missing credentials")
    from paypal_agent_toolkit.shared.api import PayPalAPI
    from paypal_agent_toolkit.shared.configuration import Context
    api = PayPalAPI(client_id=s.PAYPAL_CLIENT_ID, secret=s.PAYPAL_CLIENT_SECRET,
                    context=Context(merchant_id=s.PAYPAL_MERCHANT_ID or None, sandbox="sandbox" in s.PAYPAL_BASE))
    return api.run(_TOOLKIT_METHODS.get(name, name), args)


PP = PayPal(config.S.PAYPAL_CLIENT_ID, config.S.PAYPAL_CLIENT_SECRET)  # module singleton; tests replace it


# ---------------- request builders ----------------

def _fit(head: str, lines: list[str], cap: int = 2000) -> str:
    text = head[:cap]
    for i, ln in enumerate(lines):
        add = ln if i == 0 else "\n" + ln
        if len(text) + len(add) > cap:
            break
        text += add
    return text


def build_action_request(verdict, snapshot: dict, facts: dict, message_text: str) -> tuple[str, str, dict]:
    cur = facts["pp.dispute.amount"].value.split()[-1] if "pp.dispute.amount" in facts else "USD"

    def money(a):
        return {"currency_code": cur, "value": "%.2f" % a}

    act, t = verdict.action, verdict.type
    if act == "provide_evidence":
        groups: dict[str, list[str]] = {}
        for e in verdict.evidence:
            groups.setdefault(e.evidence_type, []).append(e.fact_id)
        ship = (snapshot.get("shop") or {}).get("shipment")
        entries = []
        for i, (et, ids) in enumerate(groups.items()):
            lines = [f"{facts[f].label}: {facts[f].value}" for f in ids if f in facts]
            ent = {"evidence_type": et, "notes": _fit(message_text + "\n\n" if i == 0 else "", lines)}
            if et == "PROOF_OF_FULFILLMENT" and ship:
                ent["evidence_info"] = {"tracking_info": [{"carrier_name": shop.CARRIERS.get(ship["carrier"], {}).get("dispute", ship["carrier"]), "tracking_number": ship["tracking"]}]}
            elif et == "PROOF_OF_REFUND":
                ent["evidence_info"] = {"refund_ids": [f.value for k, f in facts.items() if re.fullmatch(r"shop\.refund\.\d+\.id", k)]}
            entries.append(ent)
        return "provide-evidence", "multipart", {"evidences": entries}
    if act == "provide_supporting_info":
        lines, seen = [], set()
        for e in verdict.evidence:  # backing fact lines, as in provide_evidence
            if e.fact_id in facts and e.fact_id not in seen:
                seen.add(e.fact_id)
                lines.append(f"{facts[e.fact_id].label}: {facts[e.fact_id].value}")
        return "provide-supporting-info", "multipart", {"notes": _fit(message_text + "\n\n" if lines else message_text, lines)}
    if act == "make_offer":
        body = {"note": message_text, "offer_type": t}
        if t in OFFER_TYPES_WITH_AMOUNT:
            body["offer_amount"] = money(verdict.amount)
        if t == "REFUND_WITH_RETURN":
            body["return_shipping_address_info"] = {"address": shop.SHOP_RETURN_ADDRESS}
        return "make-offer", "json", body
    if act == "accept_claim":
        body = {"note": message_text, "accept_claim_type": t}
        if t == "PARTIAL_REFUND":
            body["refund_amount"] = money(verdict.amount)
        return "accept-claim", "json", body
    return "send-message", "multipart", {"message": message_text}


def build_sandbox_request(call: str, value: str) -> tuple[str, str, dict]:
    if call == "require_evidence":
        return "require-evidence", "json", {"action": value}
    return "adjudicate", "json", {"adjudication_outcome": value}
