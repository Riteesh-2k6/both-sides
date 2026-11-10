import dataclasses
import json
import typing

import httpx
import pytest

import fakes
from app import agents, config, facts, paypal, shop, store

ENUMS = json.loads((fakes.FIX / "paypal_enums.json").read_text(encoding="utf-8"))
RETURN_ADDR = shop.SHOP_RETURN_ADDRESS


def build(v, name="inr_delivered", text="Hello."):
    s = fakes.snapshot(name)
    fs = facts.build_facts(s)
    return paypal.build_action_request(v, s, fs, text), fs


def line(fs, fid):
    return f"{fs[fid].label}: {fs[fid].value}"


def test_evidence_with_tracking():
    (suffix, kind, body), fs = build(fakes.verdict())
    assert (suffix, kind) == ("provide-evidence", "multipart")
    assert body == {"evidences": [{
        "evidence_type": "PROOF_OF_FULFILLMENT",
        "notes": "Hello.\n\n" + line(fs, "shop.shipment.tracking"),
        "evidence_info": {"tracking_info": [{"carrier_name": "INDIA_POST", "tracking_number": "EE123456789IN"}]}}]}


def test_evidence_two_types_notes_and_distinct_entries():
    v = fakes.verdict(evidence=[
        {"fact_id": "shop.shipment.tracking", "evidence_type": "PROOF_OF_FULFILLMENT"},
        {"fact_id": "shop.shipment.carrier", "evidence_type": "PROOF_OF_FULFILLMENT"},
        {"fact_id": "shop.policy.returns", "evidence_type": "RETURN_POLICY"}])
    (_, _, body), fs = build(v)
    e = body["evidences"]
    assert [x["evidence_type"] for x in e] == ["PROOF_OF_FULFILLMENT", "RETURN_POLICY"]
    assert e[0]["notes"] == "Hello.\n\n" + line(fs, "shop.shipment.tracking") + "\n" + line(fs, "shop.shipment.carrier")
    assert e[1]["notes"] == line(fs, "shop.policy.returns")  # only the first entry carries the message
    assert "evidence_info" not in e[1]


def test_evidence_with_refund_ids_and_other_notes_only():
    v = fakes.verdict(evidence=[{"fact_id": "shop.refund.1.amount", "evidence_type": "PROOF_OF_REFUND"}, {"fact_id": "pp.capture.id", "evidence_type": "OTHER"}])
    (_, _, body), fs = build(v, "cnp_refunded")
    assert body["evidences"][0]["evidence_info"] == {"refund_ids": ["RF1"]}
    assert body["evidences"][0]["notes"].startswith("Hello.\n\n")
    assert body["evidences"][1] == {"evidence_type": "OTHER", "notes": line(fs, "pp.capture.id")}


def test_evidence_notes_cap_cuts_lines_from_the_end():
    v = fakes.verdict(evidence=[{"fact_id": "shop.shipment.tracking", "evidence_type": "PROOF_OF_FULFILLMENT"}, {"fact_id": "shop.shipment.carrier", "evidence_type": "PROOF_OF_FULFILLMENT"}])
    (_, _, body), fs = build(v, text="m" * 1950)
    n = body["evidences"][0]["notes"]
    assert n == "m" * 1950 + "\n\n" + line(fs, "shop.shipment.tracking") and len(n) <= 2000
    (_, _, body), _ = build(v, text="m" * 2000)
    assert body["evidences"][0]["notes"] == "m" * 2000 or len(body["evidences"][0]["notes"]) <= 2000


@pytest.mark.parametrize("type,amount,extra", [
    ("REFUND", 18.0, {}),
    ("REFUND_WITH_RETURN", 18.0, {"return_shipping_address_info": {"address": RETURN_ADDR}}),
    ("REFUND_WITH_REPLACEMENT", 18.0, {}),
    ("REPLACEMENT_WITHOUT_REFUND", None, None),
])
def test_make_offer_each_type(type, amount, extra):
    (suffix, kind, body), _ = build(fakes.verdict(action="make_offer", type=type, amount=amount, evidence=[]), text="We offer.")
    assert (suffix, kind) == ("make-offer", "json")
    exp = {"note": "We offer.", "offer_type": type}
    if extra is not None:
        exp["offer_amount"] = {"currency_code": "USD", "value": "18.00"}
        exp.update(extra)
    assert body == exp


def test_accept_claim_shapes():
    (suffix, kind, body), _ = build(fakes.verdict(action="accept_claim", type="REFUND", amount=50.0, evidence=[]), text="OK.")
    assert (suffix, kind, body) == ("accept-claim", "json", {"note": "OK.", "accept_claim_type": "REFUND"})
    (_, _, body), _ = build(fakes.verdict(action="accept_claim", type="PARTIAL_REFUND", amount=12.5, evidence=[]), text="OK.")
    assert body == {"note": "OK.", "accept_claim_type": "PARTIAL_REFUND", "refund_amount": {"currency_code": "USD", "value": "12.50"}}


def test_send_message_shape():
    (suffix, kind, body), _ = build(fakes.verdict(action="send_message", evidence=[]), text="Hi there.")
    assert (suffix, kind, body) == ("send-message", "multipart", {"message": "Hi there."})


def test_currency_follows_dispute_amount():
    s = fakes.snapshot()
    s["dispute"]["dispute_amount"]["currency_code"] = "EUR"
    fs = facts.build_facts(s)
    _, _, body = paypal.build_action_request(fakes.verdict(action="make_offer", type="REFUND", amount=5.0, evidence=[]), s, fs, "x")
    assert body["offer_amount"] == {"currency_code": "EUR", "value": "5.00"}


def test_sandbox_requests():
    assert paypal.build_sandbox_request("require_evidence", "SELLER_EVIDENCE") == ("require-evidence", "json", {"action": "SELLER_EVIDENCE"})
    assert paypal.build_sandbox_request("adjudicate", "SELLER_FAVOR") == ("adjudicate", "json", {"adjudication_outcome": "SELLER_FAVOR"})


def test_every_emitted_enum_value_is_in_paypal_enums():
    et = set(typing.get_args(agents.EvidenceItem.model_fields["evidence_type"].annotation))
    assert et and et <= set(ENUMS["evidence_type"])
    for offer in ("REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT", "REPLACEMENT_WITHOUT_REFUND"):
        assert offer in ENUMS["offer_type"]
    assert {"REFUND", "PARTIAL_REFUND"} <= set(ENUMS["accept_claim_type"])
    assert set(shop.CARRIERS) <= set(ENUMS["dispute_carrier_name"]) and set(shop.CARRIERS) <= set(ENUMS["orders_carrier"])
    assert set(paypal.OFFER_TYPES_WITH_AMOUNT) <= set(ENUMS["offer_type"])
    # every type the builder actually emits, end to end
    for t in ("REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT"):
        (_, _, body), _ = build(fakes.verdict(action="make_offer", type=t, amount=1.0, evidence=[]))
        assert body["offer_type"] in ENUMS["offer_type"]
    for t in ("REFUND", "PARTIAL_REFUND"):
        (_, _, body), _ = build(fakes.verdict(action="accept_claim", type=t, amount=1.0, evidence=[]))
        assert body["accept_claim_type"] in ENUMS["accept_claim_type"]
    (_, _, body), _ = build(fakes.verdict())
    assert body["evidences"][0]["evidence_info"]["tracking_info"][0]["carrier_name"] in ENUMS["dispute_carrier_name"]


# ---------- client ----------

def client(handler, **kw):
    return paypal.PayPal("id", "secret", "https://api-m.sandbox.paypal.com", httpx.MockTransport(handler), **kw)


def test_token_cached_and_writes_no_receipt(db):
    n = {"tok": 0}

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            n["tok"] += 1
            return httpx.Response(200, json={"access_token": "t1", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer t1"
        return httpx.Response(200, json={"ok": 1}, headers={"Paypal-Debug-Id": "d1"})
    pp = client(h)
    r1, r2 = pp.get("/v1/x"), pp.get("/v1/y")
    assert (r1.status, r1.body, r1.debug_id) == (200, {"ok": 1}, "d1") and n["tok"] == 1
    rows = store.q("SELECT * FROM receipts")
    assert len(rows) == 2 and all(r["path"] != "/v1/oauth2/token" and r["via"] == "rest" for r in rows)
    assert r1.receipt == rows[0]["id"]


def test_401_refreshes_token_once_and_retries(db):
    st = {"tok": 0, "calls": 0}

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            st["tok"] += 1
            return httpx.Response(200, json={"access_token": f"t{st['tok']}", "expires_in": 3600})
        st["calls"] += 1
        if req.headers["authorization"] == "Bearer t1":
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"})
        return httpx.Response(200, json={"ok": 1})
    r = client(h).get("/v1/x")
    assert r.status == 200 and st == {"tok": 2, "calls": 2}
    assert len(store.q("SELECT * FROM receipts")) == 1


def test_network_error_gives_status_zero(db, monkeypatch):
    monkeypatch.setattr(paypal, "RETRY_SLEEP", 0)

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ConnectTimeout("boom")
    r = client(h).post("/v1/x", json={})
    assert r.status == 0 and "boom" in r.body["error"]


def test_auth_failure_returns_resp_without_receipt(db):
    pp = client(lambda req: httpx.Response(401, json={"error": "invalid_client"}))
    r = pp.get("/v1/x")
    assert r.status == 0 and r.body["error"].startswith("auth:") and r.receipt == 0 and r.debug_id is None
    assert store.q("SELECT * FROM receipts") == []
    with pytest.raises(paypal.PayPalAuthError):
        pp.token()


def test_get_retries_once_post_does_not(db, monkeypatch):
    monkeypatch.setattr(paypal, "RETRY_SLEEP", 0)
    seq = {"get": [500, 200], "post": 0}

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if req.method == "GET":
            return httpx.Response(seq["get"].pop(0), json={"a": 1})
        seq["post"] += 1
        return httpx.Response(500, json={"a": 1})
    pp = client(h)
    assert pp.get("/v1/x").status == 200 and not seq["get"]
    assert pp.post("/v1/x", json={}).status == 500 and seq["post"] == 1


def test_get_accepts_full_url_inside_base_only(db):
    seen = []

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen.append(str(req.url))
        return httpx.Response(200, json={})
    pp = client(h)
    assert pp.get("https://api-m.sandbox.paypal.com/v1/customer/disputes?page=2").status == 200
    assert seen == ["https://api-m.sandbox.paypal.com/v1/customer/disputes?page=2"]
    r = pp.get("https://evil.example/steal")
    assert r.status == 0 and len(seen) == 1


def test_post_multipart_and_request_id_header(db):
    got = {}

    def h(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        got["ct"] = req.headers["content-type"]
        got["rid"] = req.headers.get("paypal-request-id")
        got["raw"] = req.content
        return httpx.Response(200, json={})
    pp = client(h)
    pp.post("/v1/x", multipart_input={"message": "hi"}, request_id="np-create-1")
    assert got["ct"].startswith("multipart/form-data") and got["rid"] == "np-create-1"
    assert b'name="input"; filename="input.json"' in got["raw"] and b"Content-Type: application/json" in got["raw"] and b'{"message": "hi"}' in got["raw"].replace(b'":"', b'": "')
    pp.post("/v1/x", json={"a": 1})
    assert got["ct"].startswith("application/json") and got["rid"] is None


def test_receipt_body_is_masked(db):
    pp = client(lambda req: httpx.Response(200, json={"access_token": "S", "payer": {"email_address": "sam@example.com"}}) if req.url.path != "/v1/oauth2/token"
                else httpx.Response(200, json={"access_token": "t", "expires_in": 3600}))
    pp.get("/v1/x")
    body = store.one("SELECT body FROM receipts")["body"]
    assert "sam@example.com" not in body and '"S"' not in body


# ---------- read_tool ----------

def toolkit_on(monkeypatch, fn):
    monkeypatch.setattr(config, "S", dataclasses.replace(config.S, USE_AGENT_TOOLKIT=True))
    monkeypatch.setattr(paypal, "_toolkit_call", fn)


DISPUTE = {"dispute_id": "D1", "links": [], "reason": "X"}
ORDER = {"id": "O1", "purchase_units": []}


def test_read_tool_uses_toolkit_when_ok(db, monkeypatch):
    calls = []
    toolkit_on(monkeypatch, lambda name, args: calls.append((name, args)) or json.dumps(DISPUTE))
    pp = fakes.fake_paypal()
    r = pp.read_tool("get_dispute", {"dispute_id": "D1"})
    assert r.status == 200 and r.body == DISPUTE and calls == [("get_dispute", {"dispute_id": "D1"})] and pp.calls == []
    row = store.one("SELECT * FROM receipts WHERE id=?", (r.receipt,))
    assert (row["via"], row["method"], row["status"], row["debug_id"]) == ("agent-toolkit", "TOOL", 200, None)
    assert row["path"] == 'agent-toolkit:get_dispute {"dispute_id": "D1"}'


def test_read_tool_get_order_and_list(db, monkeypatch):
    toolkit_on(monkeypatch, lambda name, args: json.dumps(ORDER) if name == "get_order" else json.dumps({"links": []}))
    pp = fakes.fake_paypal()
    assert pp.read_tool("get_order", {"order_id": "O1"}).body == ORDER
    assert pp.read_tool("list_disputes", {}).body == {"links": [], "items": []}


@pytest.mark.parametrize("bad", [
    "RAISE", "not json", json.dumps({"dispute_id": "D1"}), json.dumps({"error": "x", "dispute_id": "D1", "links": []}),
    json.dumps({"name": "N", "message": "m", "dispute_id": "D1", "links": []}), json.dumps([1, 2]),
])
def test_read_tool_falls_back_to_rest(db, monkeypatch, bad):
    def tk(name, args):
        if bad == "RAISE":
            raise RuntimeError("no toolkit")
        return bad
    toolkit_on(monkeypatch, tk)
    pp = fakes.fake_paypal({("GET", r"/v1/customer/disputes/D1"): (200, DISPUTE)})
    r = pp.read_tool("get_dispute", {"dispute_id": "D1"})
    assert r.status == 200 and r.body == DISPUTE and len(pp.calls) == 1  # one REST GET, no second toolkit call
    assert store.one("SELECT via FROM receipts WHERE id=?", (r.receipt,))["via"] == "rest"


def test_read_tool_off_and_rest_paths(db):
    pp = fakes.fake_paypal({("GET", r"/v2/checkout/orders/O1"): (200, ORDER), ("GET", r"/v1/customer/disputes"): (200, {"items": []})})
    assert pp.read_tool("get_order", {"order_id": "O1"}).body == ORDER
    pp.read_tool("list_disputes", {})
    assert pp.calls[-1]["path"] == "/v1/customer/disputes" and pp.calls[-1]["query"] == "page_size=50"


def test_toolkit_missing_dispute_tools_raise():
    with pytest.raises(Exception):
        paypal._toolkit_call("get_dispute", {"dispute_id": "D1"})  # toolkit 1.1.0 has no dispute tools, and no creds here
