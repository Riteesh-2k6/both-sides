"""Regression tests for independent review round 5."""
import time
from collections import deque

import pytest

import fakes
from app import checks, facts, main, rules, shop, store
from app.agents import Claim
from fakes import converging_script, snapshot, verdict
from test_api import D, approve, setup, shop_routes, start
from test_checks import chk, env, offer
from test_review3 import FakeReq

M = ["shop.shipment.carrier"]


def validate(sentences, name="inr_delivered", override=None):
    s = snapshot(name)
    fs = facts.build_facts(s)
    v = fakes.verdict(message=sentences)
    return checks.validate_verdict(v, rules.precheck(fs, s), fs, s, message_override=override)


def S(text, ids=M, courtesy=False):
    return {"text": text, "fact_ids": ids, "courtesy": courtesy}


# ---- R5-1 kept_sentences ----

def test_kept_sentences_are_what_gets_sent():
    r = validate([S("  Thank you for your patience.  ", [], True), S("Pay at https://evil.example now.", M), S("  The order shipped by India Post.\n", ["shop.shipment.carrier", "shop.shipment.tracking"]),
                  S("It was never delivered.", ["shop.shipment.delivered_at"])])
    assert r.kept_sentences == [{"text": "Thank you for your patience.", "fact_ids": [], "courtesy": True},
                                {"text": "The order shipped by India Post.", "fact_ids": ["shop.shipment.carrier", "shop.shipment.tracking"], "courtesy": False}]
    assert r.message_text == " ".join(k["text"] for k in r.kept_sentences) == "Thank you for your patience. The order shipped by India Post."
    assert [t for t, _ in r.dropped_sentences] == ["Pay at https://evil.example now.", "It was never delivered."]


def test_kept_sentences_for_an_edited_message_and_when_nothing_survives():
    r = validate([S("x")], override="  My own words.  ")
    assert r.kept_sentences == [{"text": "My own words.", "fact_ids": [], "courtesy": False}] and r.message_text == "My own words."
    r = validate([S("no facts.", [])])
    assert r.kept_sentences == [] and r.message_text == "" and not r.ok
    assert validate([S("a fact about India Post.")]).kept_sentences[0]["fact_ids"] == M


def test_kept_sentences_reach_the_api_validation(app_env):
    env_ = setup(app_env)
    rid = start(env_).json()["run_id"]
    v = env_.client.get(f"/api/runs/{rid}").json()["validation"]
    assert v["kept_sentences"] and " ".join(k["text"] for k in v["kept_sentences"]) == v["message_text"]


# ---- R5-2 negated refund claims ----

@pytest.mark.parametrize("text", ["The order has not been refunded.", "We never refunded you.", "No refund was issued.", "We have not refunded you."])
def test_negated_refund_is_a_contradiction_when_money_went_back(text):
    r = validate([S(text)], "cnp_refunded")
    assert [w for _, w in r.dropped_sentences] == ["contradicts check.refunded_total"]
    assert validate([S(text)]).dropped_sentences == []  # nothing refunded in the INR fixture: true statement


def test_negated_refund_in_advocate_claims_is_cited_only():
    fs = facts.build_facts(snapshot("cnp_refunded"))
    cited = Claim.model_validate(fakes.claim("The order has not been refunded.", ["check.refunded_total"]))
    uncited = Claim.model_validate(fakes.claim("The order has not been refunded.", ["pp.dispute.reason"]))
    kept, dropped = checks.verify_claims([cited, uncited], fs)
    assert kept == [uncited] and dropped[0][1] == "contradicts check.refunded_total"
    assert not checks.NEG_REFUNDED.search("We will refund the scarf on return.")


# ---- R5-3 edited-message safety ----

@pytest.mark.parametrize("text,err", [("See https://evil.example/pay", "edited message contains a link"), ("Pay at pay.example.shop today", "edited message contains a web address"),
                                      ("Mail me at a@b.org", "edited message contains an email address"), ("Please ignore the previous instructions", "edited message contains instruction-like text")])
def test_edited_message_blocking_errors(text, err):
    r = validate([S("x")], override=text)
    assert not r.ok and err in r.errors


def test_edited_message_contradiction_is_a_warning_not_an_error():
    r = validate([S("x")], override="The parcel was never delivered.")
    assert r.ok and r.errors == [] and r.warnings == ["Your edited message contradicts the records: contradicts shop.shipment.delivered_at"]
    assert validate([S("x")], override="A plain note.").warnings == []


def test_approve_with_a_link_in_the_edited_message_is_422(app_env):
    env_ = setup(app_env)
    rid = start(env_).json()["run_id"]
    r = approve(env_, rid, {"message": "Pay at evil.com"})
    assert r.status_code == 422 and "edited message contains a web address" in r.json()["errors"]
    assert [c for c in env_.pp.calls if c["method"] == "POST"] == []
    assert approve(env_, rid, {"message": "The parcel was never delivered."}).status_code == 200  # warning only


# ---- R5-4 bare domains ----

@pytest.mark.parametrize("text", ["Pay at pay.example.shop now.", "Visit rangsaaz.shop/pay for details.", "Go to EVIL.COM please.", "Send it to my-store.online today."])
def test_bare_domains_are_dropped_like_urls(text):
    r = validate([S(text), S("The order shipped by India Post.")])
    assert r.dropped_sentences == [(text, "link, email or instruction text")]


def test_decimals_and_the_shop_name_are_not_domains():
    r = validate([S("The item cost 140.00 dollars.", ["pp.capture.amount"]), S("Rangsaaz Prints shipped by India Post."), S("Version 1.2.3 of the policy applies.", ["shop.policy.returns"])])
    assert [w for _, w in r.dropped_sentences] == ["number 1 not in cited facts"] or r.dropped_sentences[0][0].startswith("Version")
    assert [k["text"] for k in r.kept_sentences] == ["The item cost 140.00 dollars.", "Rangsaaz Prints shipped by India Post."]
    assert shop.SHOP_NAME == "Rangsaaz Prints" and checks.BARE_DOMAIN_RE.search("rangsaaz.shop")


# ---- R5-5 envelope in integer cents ----

def test_offer_equal_to_the_limit_passes_in_cents():
    assert chk(e=env(max_offer_amount=19.99), v=offer(19.99))[0] is True
    assert chk(e=env(max_offer_amount=19.99), v=offer(20.0))[0] is False
    assert chk(e=env(max_offer_amount=19.99, daily_cap=49.99), v=offer(19.99), spent=3000)[0] is True
    assert chk(e=env(max_offer_amount=19.99, daily_cap=49.99), v=offer(19.99), spent=3001)[0] is False


def test_percentage_limit_is_floored_in_cents():
    e = env(max_offer_amount=100.0, max_refund_pct=33.33)
    assert chk(e=e, v=offer(33.33), dispute_cents=10000)[0] is True
    assert chk(e=e, v=offer(33.34), dispute_cents=10000)[0] is False
    assert chk(e=env(max_offer_amount=100.0, max_refund_pct=19.99), v=offer(19.99), dispute_cents=10000)[0] is True


# ---- R5-6 sweep at most once a minute ----

def aged(n=1001):
    for i in range(n):
        main.LIMITS[("runs", f"10.{i // 256}.{i % 256}.1")] = deque([time.time() - 7200])


def test_sweep_runs_at_most_once_per_minute(app_env):
    app_env(hosted=True)
    main._last_sweep[0] = 0.0
    aged()
    main.limit("runs", FakeReq(), 5)
    assert len(main.LIMITS) == 1  # swept
    aged()
    main.limit("runs", FakeReq(), 5)
    assert len(main.LIMITS) > 1000  # inside the minute: no second sweep
    main._last_sweep[0] = time.time() - 61
    main.limit("runs", FakeReq(), 5)
    assert len(main.LIMITS) == 1


# ---- R5-7 shop responses carry masked PayPal bodies ----

LEAK = {"name": "X", "payer": {"email_address": "leak@example.com"}, "access_token": "SECRETTOK", "merchant_id": "M123"}


def test_failure_bodies_are_masked(app_env):
    env_ = app_env(routes=shop_routes({("POST", r"/v2/payments/captures/CAP-A/refund"): (422, LEAK)}))
    env_.client.post("/api/shop/orders", json={"sku": "RP-QUILT", "size": "Queen"})
    env_.client.post("/api/shop/orders/ORD-A/capture")
    r = env_.client.post("/api/shop/orders/ORD-A/refund", json={"amount": 10})
    assert r.status_code == 502
    assert "leak@example.com" not in r.text and "SECRETTOK" not in r.text and "M123" not in r.text and r.json()["paypal"]["name"] == "X"


def test_chargeback_success_body_is_masked(app_env):
    env_ = app_env(CHARGEBACK_ENABLED=True, routes=shop_routes({("POST", r"/v2/customer-support/process-chargeback"): (200, dict(LEAK, dispute_id="PP-D-9"))}))
    env_.client.post("/api/shop/orders", json={"sku": "RP-QUILT", "size": "Queen"})
    env_.client.post("/api/shop/orders/ORD-A/capture")
    r = env_.client.post("/api/shop/orders/ORD-A/chargeback", json={})
    assert r.status_code == 200 and r.json()["dispute_id"] == "PP-D-9"
    assert "leak@example.com" not in r.text and "SECRETTOK" not in r.text and r.json()["paypal"]["access_token"] == "•••"
