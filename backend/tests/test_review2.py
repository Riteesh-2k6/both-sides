"""Regression tests for independent review round 2."""
import copy

import httpx
import pytest

import fakes
from app import checks, facts, pipeline, rules, store
from app.agents import Claim
from fakes import converging_script, snapshot, verdict
from test_api import D, approve, setup, start
from test_review1 import undelivered_offer

ENV = {"max_offer_amount": 40, "max_refund_pct": 100, "daily_cap": 50, "allowed_actions": ["make_offer"],
       "allowed_reasons": ["MERCHANDISE_OR_SERVICE_NOT_RECEIVED"], "expires_at": "2030-01-01T00:00:00Z"}
M = ["shop.shipment.carrier"]


def run_row(status="running", mode="desk"):
    return store.run("INSERT INTO runs(dispute_id,mode,status,snapshot,steps,created_at) VALUES(?,?,?,?,?,?)", (D, mode, status, "{}", "[]", "t"))


# ---- R2-2 reject races ----

def test_reject_of_a_running_run_is_409(app_env):
    env = setup(app_env)
    rid = run_row("running")
    r = env.client.post(f"/api/disputes/{D}/runs/{rid}/reject")
    assert r.status_code == 409 and store.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "running"


def test_rejected_while_envelope_pending_sends_nothing_and_stays_rejected(app_env, monkeypatch):
    s, v = undelivered_offer()
    env = setup(app_env, snap=s, script=converging_script("make_offer", "REFUND", 18.0, v=v), routes={("POST", rf"/v1/customer/disputes/{D}/make-offer"): (200, {})})
    env.client.put("/api/envelope", json=ENV)
    real = pipeline._envelope_step

    def reject_first(run_id, *a):
        store.run("UPDATE runs SET status='rejected' WHERE id=?", (run_id,))  # the merchant rejects just before the auto-send
        return real(run_id, *a)
    monkeypatch.setattr(pipeline, "_envelope_step", reject_first)
    rid = start(env).json()["run_id"]
    assert store.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "rejected"
    assert store.q("SELECT * FROM actions") == [] and [c for c in env.pp.calls if c["method"] == "POST"] == []
    assert store.jload(store.one("SELECT envelope FROM runs WHERE id=?", (rid,))["envelope"])["ok"] is False


def test_run_live_final_update_never_overwrites_rejected(app_env, monkeypatch):
    env = setup(app_env)
    real = pipeline.execute

    def execute_then_reject(*a, **k):
        res = real(*a, **k)
        store.run("UPDATE runs SET status='rejected'")
        return res
    monkeypatch.setattr(pipeline, "execute", execute_then_reject)
    rid = start(env).json()["run_id"]
    assert store.one("SELECT status FROM runs WHERE id=?", (rid,))["status"] == "rejected"


# ---- R2-3 delivered / refunded contradictions in outgoing messages ----

def msg_result(text, name="inr_delivered", mut=None):
    s = snapshot(name)
    if mut:
        mut(s)
    fs = facts.build_facts(s)
    v = fakes.verdict(message=[{"text": text, "fact_ids": M, "courtesy": False}, {"text": "The order shipped by India Post.", "fact_ids": M, "courtesy": False}])
    return checks.validate_verdict(v, rules.precheck(fs, s), fs, s)


@pytest.mark.parametrize("text", ["It hasn't been delivered.", "It isn't delivered yet.", "The parcel was not yet delivered.", "The package did not arrive.",
                                  "The parcel has not arrived.", "You never got it.", "The item was not received.", "It was never delivered.", "It is undelivered."])
def test_negated_delivery_in_message_is_dropped_when_delivery_is_recorded(text):
    r = msg_result(text)  # the fixture records a delivery; the sentence cites only the carrier
    assert r.dropped_sentences == [(text, "contradicts shop.shipment.delivered_at")] and r.message_text == "The order shipped by India Post."


@pytest.mark.parametrize("text", ["We refunded you.", "It has been refunded.", "The refund was processed.", "The refund was issued last week.", "We have refunded the order."])
def test_refund_claims_in_message_are_dropped_when_nothing_was_refunded(text):
    r = msg_result(text)
    assert [w for _, w in r.dropped_sentences] == ["contradicts check.refunded_total"]


def test_positive_delivery_in_message_dropped_when_not_delivered():
    r = msg_result("The parcel was delivered.", mut=lambda s: s["shop"]["shipment"].__setitem__("delivered_at", None))
    assert [w for _, w in r.dropped_sentences] == ["contradicts shop.shipment.delivered_at"]


def test_true_statements_survive():
    assert msg_result("The parcel was delivered.").dropped_sentences == []
    assert msg_result("We refunded you.", "cnp_refunded").dropped_sentences == []
    r = msg_result("It has not arrived.", mut=lambda s: s["shop"]["shipment"].__setitem__("delivered_at", None))
    assert r.dropped_sentences == []


def test_advocate_claims_keep_the_cited_only_rule_with_the_wider_regexes():
    s = snapshot()
    fs = facts.build_facts(s)
    uncited = Claim.model_validate(fakes.claim("The parcel has not been delivered.", ["pp.dispute.reason"]))
    cited = Claim.model_validate(fakes.claim("The parcel hasn't been delivered.", ["shop.shipment.delivered_at"]))
    rebut = Claim.model_validate(fakes.claim("The parcel was delivered.", ["shop.shipment.delivered_at"]))
    kept, dropped = checks.verify_claims([uncited, cited, rebut], fs)
    assert kept == [uncited, rebut] and [r for _, r in dropped] == ["contradicts shop.shipment.delivered_at"]
    cr = Claim.model_validate(fakes.claim("It has been refunded already.", ["check.refunded_total"]))
    assert checks.verify_claims([cr], fs)[1][0][1] == "contradicts check.refunded_total"


# ---- R2-4 trailing newline ----

def test_dispute_id_with_trailing_newline_is_rejected(app_env):
    from app import main
    assert main.DISPUTE_ID_RE.fullmatch("PP-D-1001\n") is None and main.DISPUTE_ID_RE.fullmatch("PP-D-1001")
    env = app_env(routes={})
    r = env.client.get("/api/disputes/PP-D-1001%0A")
    assert r.status_code == 400 and env.pp.calls == []


# ---- R2-8 empty edited message ----

def test_empty_edited_message_is_an_error():
    s = snapshot()
    fs = facts.build_facts(s)
    for blank in ("", "   ", "\n\t"):
        r = checks.validate_verdict(fakes.verdict(), rules.precheck(fs, s), fs, s, message_override=blank)
        assert not r.ok and "message is empty" in r.errors


def test_edit_message_blank_or_too_long_is_422(app_env):
    env = setup(app_env)
    rid = start(env).json()["run_id"]
    r = approve(env, rid, {"message": "   "})
    assert r.status_code == 422 and "message is empty" in r.json()["errors"]
    assert approve(env, rid, {"message": "x" * 2001}).status_code == 422
    assert [c for c in env.pp.calls if c["method"] == "POST"] == []
    assert approve(env, rid, {"message": "x" * 2000}).status_code == 200


# ---- R2-9 what counts as evidence of 'sent' ----

def unknown_action(app_env, v, route_suffix):
    env = setup(app_env, script=converging_script(v.action, v.type, v.amount, v=v),
                routes={("POST", rf"/v1/customer/disputes/{D}/{route_suffix}"): httpx.ConnectTimeout("slow")})
    rid = start(env).json()["run_id"]
    aid = approve(env, rid).json()["action_id"]
    assert store.one("SELECT status FROM actions")["status"] == "unknown"
    return env, aid


def serve_dispute(env, **changes):
    d = copy.deepcopy(snapshot()["dispute"])
    d.update(changes)
    env.pp.routes[("GET", rf"/v1/customer/disputes/{D}")] = (200, d)


def check(env, aid):
    return env.client.post(f"/api/disputes/{D}/actions/{aid}/check").json()["status"]


def test_status_change_alone_does_not_confirm_provide_evidence(app_env):
    env, aid = unknown_action(app_env, verdict(), "provide-evidence")
    serve_dispute(env, status="UNDER_REVIEW")
    assert check(env, aid) == "failed"


def test_status_change_alone_does_not_confirm_send_message(app_env):
    env, aid = unknown_action(app_env, verdict(action="send_message", evidence=[]), "send-message")
    serve_dispute(env, status="UNDER_REVIEW")
    assert check(env, aid) == "failed"


def test_seller_message_count_confirms_send_message(app_env):
    env, aid = unknown_action(app_env, verdict(action="send_message", evidence=[]), "send-message")
    serve_dispute(env, messages=snapshot()["dispute"]["messages"] + [{"posted_by": "SELLER", "content": "hi"}])
    assert check(env, aid) == "sent"


def test_seller_evidence_count_confirms_provide_evidence(app_env):
    env, aid = unknown_action(app_env, verdict(), "provide-evidence")
    serve_dispute(env, evidences=[{"source": "SUBMITTED_BY_SELLER", "evidence_type": "PROOF_OF_FULFILLMENT"}])
    assert check(env, aid) == "sent"


def test_status_change_does_not_confirm_make_offer_with_an_amount(app_env):
    offer = verdict(action="make_offer", type="REFUND", amount=10.0, evidence=[], message=[{"text": "We are offering a refund for this order.", "fact_ids": ["shop.product.name"], "courtesy": False}])
    env, aid = unknown_action(app_env, offer, "make-offer")
    serve_dispute(env, status="UNDER_REVIEW")
    assert check(env, aid) == "failed"


def test_make_offer_confirmed_only_by_a_new_matching_seller_offer(app_env):
    offer = verdict(action="make_offer", type="REFUND", amount=10.0, evidence=[], message=[{"text": "We are offering a refund for this order.", "fact_ids": ["shop.product.name"], "courtesy": False}])
    env, aid = unknown_action(app_env, offer, "make-offer")
    serve_dispute(env, offer={"seller_offered_amount": {"currency_code": "USD", "value": "12.00"}})  # an offer, but not ours
    assert check(env, aid) == "failed"


def test_matching_new_seller_offer_confirms_make_offer(app_env):
    offer = verdict(action="make_offer", type="REFUND", amount=10.0, evidence=[], message=[{"text": "We are offering a refund for this order.", "fact_ids": ["shop.product.name"], "courtesy": False}])
    env, aid = unknown_action(app_env, offer, "make-offer")
    serve_dispute(env, offer={"seller_offered_amount": {"currency_code": "USD", "value": "10.00"}})
    assert check(env, aid) == "sent"


def test_status_change_confirms_replacement_without_refund(app_env):
    s = snapshot()
    s["dispute"]["allowed_response_options"]["make_offer"]["offer_types"].append("REPLACEMENT_WITHOUT_REFUND")
    offer = verdict(action="make_offer", type="REPLACEMENT_WITHOUT_REFUND", amount=None, evidence=[],
                    message=[{"text": "We will send a replacement for this order.", "fact_ids": ["shop.product.name"], "courtesy": False}])
    env = setup(app_env, snap=s, script=converging_script("make_offer", "REPLACEMENT_WITHOUT_REFUND", None, v=offer),
                routes={("POST", rf"/v1/customer/disputes/{D}/make-offer"): httpx.ConnectTimeout("slow")})
    aid = approve(env, start(env).json()["run_id"]).json()["action_id"]
    d = copy.deepcopy(s["dispute"])
    d["status"] = "UNDER_REVIEW"
    env.pp.routes[("GET", rf"/v1/customer/disputes/{D}")] = (200, d)
    assert check(env, aid) == "sent"


def test_status_change_confirms_accept_claim(app_env):
    accept = verdict(action="accept_claim", type="REFUND", evidence=[], message=[{"text": "We accept and will refund this order.", "fact_ids": ["shop.product.name"], "courtesy": False}])
    env, aid = unknown_action(app_env, accept, "accept-claim")
    serve_dispute(env, status="UNDER_REVIEW")
    assert check(env, aid) == "sent"
