"""The first real sandbox dispute: a buyer-opened INR claim that arrives UNDER_REVIEW, stage CHARGEBACK, channel INTERNAL,
with only a provide-supporting-info seller action (tests/fixtures/real_inr_claim.json: real masked dispute, hand-built capture/order/shop)."""
import copy
import hashlib
import json
import typing

import httpx
import pytest

import fakes
from app import agents, checks, facts, paypal, pipeline, rules, store
from fakes import converging_script, quarantine_out, snapshot, verdict

REAL = "PP-R-IIX-10197974"
NOTE = "“I ordered on Nov 5 and the tracking hasn’t moved since it left India. I’m worried it’s lost. I’d like a full refund.”"
MSG = [{"text": "Thank you for your patience.", "fact_ids": [], "courtesy": True},
       {"text": "The order shipped by India Post with a tracking number on file.", "fact_ids": ["shop.shipment.carrier", "shop.shipment.tracking"], "courtesy": False}]
EVIDENCE = [{"fact_id": "shop.shipment.tracking", "evidence_type": "PROOF_OF_FULFILLMENT"}, {"fact_id": "shop.shipment.delivered_at", "evidence_type": "OTHER"}]
EXPECTED_NOTES = ("Thank you for your patience. The order shipped by India Post with a tracking number on file."
                  "\n\nTracking number: EE123456789IN\nDelivered at: not delivered")


def real():
    return fakes.snapshot("real_inr_claim")


def supporting(**kw):
    d = dict(action="provide_supporting_info", evidence=EVIDENCE, message=MSG)
    d.update(kw)
    return verdict(**d)


# ---------- the fixture ----------

def test_fixture_is_the_masked_real_dispute():
    s = real()
    assert "_handwritten" not in s
    d = s["dispute"]
    assert (d["dispute_id"], d["reason"], d["status"], d["dispute_life_cycle_stage"], d["dispute_channel"], d["dispute_state"]) == \
        (REAL, "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "UNDER_REVIEW", "CHARGEBACK", "INTERNAL", "UNDER_PAYPAL_REVIEW")
    assert [l["rel"] for l in d["links"]] == ["self", "provide_supporting_info"] and "allowed_response_options" not in d and "messages" not in d
    assert d["disputed_transactions"][0]["seller_protection_eligible"] is True
    blob = json.dumps(s, ensure_ascii=False)
    for leak in ("personal.example.com", "business.example.com", "John Doe", "Test Store"):
        assert leak not in blob, leak
    assert d["disputed_transactions"][0]["buyer"]["name"] == "J."


# ---------- facts ----------

def test_real_claim_facts():
    s = real()
    fs = facts.build_facts(s)
    v = {k: f.value for k, f in fs.items()}
    assert s["buyer_text"] == NOTE and "CREATE" == s["dispute"]["evidences"][0]["evidence_type"]
    assert v["pp.dispute.channel"] == "INTERNAL" and v["pp.dispute.protection"] == "eligible (EXPANDED_SELLER_PROTECTION)"
    assert (v["pp.dispute.stage"], v["pp.dispute.status"], v["pp.dispute.amount"], v["pp.dispute.buyer_requested"]) == ("CHARGEBACK", "UNDER_REVIEW", "38.00 USD", "38.00 USD")
    assert v["shop.shipment.delivered_at"] == "not delivered" and v["shop.shipment.carrier"] == "INDIA_POST"
    assert "pp.dispute.requested_evidence" not in v and "check.hours_left" not in v  # no seller response due date on this dispute
    assert list(v).index("pp.dispute.channel") < list(v).index("pp.dispute.amount") and list(v).index("pp.dispute.protection") < list(v).index("pp.capture.id")


def test_buyer_text_comes_from_the_create_evidence_note_only():
    s = real()
    d = s["dispute"]
    d["evidences"].append({"evidence_type": "PROOF_OF_FULFILLMENT", "notes": "seller words", "source": "SUBMITTED_BY_SELLER", "date": "2026-11-10T09:00:00.000Z"})
    assert facts._buyer_text(d) == NOTE


@pytest.mark.parametrize("tx,expected", [({"seller_protection_eligible": True, "seller_protection_type": "EXPANDED_SELLER_PROTECTION"}, "eligible (EXPANDED_SELLER_PROTECTION)"),
                                         ({"seller_protection_eligible": True}, "eligible"),
                                         ({"seller_protection_eligible": False, "seller_protection_type": "X"}, "not eligible"),
                                         ({}, None)])
def test_protection_fact_variants(tx, expected):
    s = real()
    t = s["dispute"]["disputed_transactions"][0]
    t.pop("seller_protection_eligible")
    t.pop("seller_protection_type")
    t.update(tx)
    fs = facts.build_facts(s)
    assert (fs["pp.dispute.protection"].value if "pp.dispute.protection" in fs else None) == expected
    s["dispute"].pop("dispute_channel")
    assert "pp.dispute.channel" not in facts.build_facts(s)


# ---------- rules ----------

def test_supporting_info_only_is_a_legal_non_advisory_case():
    s = real()
    p = rules.precheck(facts.build_facts(s), s)
    assert p.legal_actions == ["provide_supporting_info"] and p.advisory is False and p.advisory_text is None
    assert p.offer_types == [] and p.accept_types == [] and p.sandbox_calls == []
    assert (p.coverage, p.coverage_rule) == ("unknown", "Tracking shows no delivery yet.")


def test_under_review_without_seller_links_keeps_the_advisory_text():
    s = real()
    s["dispute"]["links"] = [l for l in s["dispute"]["links"] if l["rel"] == "self"]
    p = rules.precheck(facts.build_facts(s), s)
    assert p.advisory and p.advisory_text == "PayPal is reviewing this case; no seller action is possible now."
    assert p.legal_actions == ["provide_evidence", "make_offer", "accept_claim", "send_message"]  # advice only; supporting info is not offered without its link


def test_supporting_info_link_sits_beside_the_other_actions():
    s = snapshot()
    s["dispute"]["links"].append({"href": f"https://x/v1/customer/disputes/{s['dispute']['dispute_id']}/provide-supporting-info", "rel": "provide_supporting_info", "method": "POST"})
    p = rules.precheck(facts.build_facts(s), s)
    assert p.legal_actions == ["provide_evidence", "make_offer", "accept_claim", "send_message", "provide_supporting_info"] and not p.advisory


# ---------- agents ----------

LINE = "provide_supporting_info (only if it is in legal_actions): send notes to PayPal while it reviews the claim"


def test_action_literals_and_prompts_know_the_new_action():
    for model in (agents.Proposal, agents.Verdict):
        assert "provide_supporting_info" in typing.get_args(model.model_fields["action"].annotation)
    for name in ("BUYER_SYSTEM", "MERCHANT_SYSTEM", "JUDGE_SYSTEM", "BASELINE_SYSTEM"):
        assert LINE in getattr(agents, name), name


# ---------- validation ----------

def validate(v, s=None):
    s = s or real()
    fs = facts.build_facts(s)
    return checks.validate_verdict(v, rules.precheck(fs, s), fs, s)


def test_supporting_info_validation_rules():
    r = validate(supporting())
    assert r.ok and r.errors == [] and [k["text"] for k in r.kept_sentences] == [m["text"] for m in MSG]
    assert validate(supporting(evidence=[])).ok  # evidence is optional
    bad = validate(supporting(evidence=[{"fact_id": "shop.shipment.delivered_at", "evidence_type": "PROOF_OF_DELIVERY_SIGNATURE"}]))
    assert bad.ok  # supporting info reaches PayPal as notes only, so evidence types are not enforced (bench UNAUTH-1/2)
    unknown = validate(supporting(evidence=[{"fact_id": "shop.nope", "evidence_type": "OTHER"}]))
    assert not unknown.ok and any("unknown fact" in e for e in unknown.errors)
    none = validate(supporting(message=[{"text": "Thanks.", "fact_ids": [], "courtesy": True}]))
    assert not none.ok and "no supported sentences" in none.errors
    # the same sentence rules apply: a link is dropped
    linked = validate(supporting(message=MSG + [{"text": "See https://evil.example now.", "fact_ids": ["shop.shipment.carrier"], "courtesy": False}]))
    assert linked.ok and linked.dropped_sentences[0][1] == "link, email or instruction text"


def test_supporting_info_is_not_legal_when_paypal_does_not_offer_it():
    r = validate(supporting(), snapshot())  # the handwritten INR case has four other links
    assert not r.ok and any("not legal" in e for e in r.errors)


# ---------- request ----------

def test_supporting_info_request_body():
    s = real()
    fs = facts.build_facts(s)
    suffix, kind, body = paypal.build_action_request(supporting(), s, fs, "Hello there.")
    assert (suffix, kind) == ("provide-supporting-info", "multipart")
    assert body == {"notes": "Hello there.\n\nTracking number: EE123456789IN\nDelivered at: not delivered"}
    _, _, body = paypal.build_action_request(supporting(evidence=[]), s, fs, "Hello there.")
    assert body == {"notes": "Hello there."}
    _, _, body = paypal.build_action_request(supporting(), s, fs, "m" * 1950)
    assert body["notes"] == "m" * 1950 + "\n\n" + "Tracking number: EE123456789IN" and len(body["notes"]) <= 2000
    _, _, body = paypal.build_action_request(supporting(evidence=[]), s, fs, "m" * 2500)
    assert len(body["notes"]) == 2000


# ---------- golden-ish run on the real snapshot ----------

def test_execute_on_the_real_snapshot_ends_in_a_valid_supporting_info_verdict(monkeypatch):
    from app import llm
    v = supporting()
    sc = {"quarantine": [quarantine_out(stated_reason="The tracking has not moved", alleged=[{"id": "A1", "text": "The parcel is stuck"}])],
          "buyer_r1": [fakes.turn_buyer(action="provide_supporting_info")], "merchant_r1": [fakes.turn_merchant(action="provide_supporting_info")],
          "judge_a": [v], "judge_b": [v.model_copy(deep=True)]}
    fk = fakes.fake_llm(sc)
    monkeypatch.setattr(llm, "FAKE", fk)
    s = real()
    res = pipeline.execute(s, "desk", max_rounds=2)
    assert res.status == "done" and res.validation["ok"] and res.verdict["action"] == "provide_supporting_info"
    assert res.outcome == {"agreed": True, "differences": [], "second": None, "deadlock": None}
    assert [c[0] for c in fk.calls] == ["quarantine", "buyer_r1", "merchant_r1", "judge_a", "judge_b"]
    assert NOTE.split(".")[0][:20] not in fk.calls[1][2]  # the buyer's words reach the advocates only through the quarantine
    pre_step = {x["step"]: x for x in res.steps}["precheck"]["data"]
    assert pre_step["legal_actions"] == ["provide_supporting_info"] and pre_step["advisory"] is False
    fs = facts.build_facts(s)
    suffix, kind, body = paypal.build_action_request(agents.Verdict.model_validate(res.verdict), s, fs, res.validation["message_text"])
    assert (suffix, kind, body) == ("provide-supporting-info", "multipart", {"notes": EXPECTED_NOTES})
    assert pipeline.facts_key(fs, rules.precheck(fs, s)) == "UNDER_REVIEW|CHARGEBACK|provide_supporting_info"


# ---------- API: approve, unknown result, envelope ----------

def setup(app_env, routes=None, **kw):
    s = real()
    r = {("POST", rf"/v1/customer/disputes/{REAL}/provide-supporting-info"): (200, {})}
    r.update(routes or {})
    return app_env(snapshot=s, script=converging_script("provide_supporting_info", v=supporting()), routes=r, **kw)


def run_and_approve(env):
    rid = env.client.post(f"/api/disputes/{REAL}/runs", json={"mode": "desk"}).json()["run_id"]
    return rid, env.client.post(f"/api/disputes/{REAL}/actions", json={"run_id": rid, "edits": None})


def test_approve_posts_multipart_supporting_info(app_env):
    env = setup(app_env)
    rid, r = run_and_approve(env)
    assert r.status_code == 200 and r.json()["status"] == "sent"
    post = [c for c in env.pp.calls if c["method"] == "POST"][0]
    assert post["path"].endswith("/provide-supporting-info") and post["kind"] == "multipart" and post["json"] == {"notes": EXPECTED_NOTES}
    a = store.one("SELECT * FROM actions")
    assert a["action"] == "provide_supporting_info" and store.jload(a["pre_counts"]) == {"status": "UNDER_REVIEW", "seller_messages": 0, "seller_evidences": 0, "seller_supporting": 0, "seller_offered": None}
    dry = env.client.get(f"/api/runs/{rid}/dry-run").json()
    assert dry["path"].endswith(f"/{REAL}/provide-supporting-info") and dry["kind"] == "multipart"


def unknown(app_env):
    env = setup(app_env, routes={("POST", rf"/v1/customer/disputes/{REAL}/provide-supporting-info"): httpx.ConnectTimeout("slow")})
    _, r = run_and_approve(env)
    assert r.json()["status"] == "unknown"
    return env, r.json()["action_id"]


def serve(env, **changes):
    d = copy.deepcopy(real()["dispute"])
    d.update(changes)
    env.pp.routes[("GET", rf"/v1/customer/disputes/{REAL}")] = (200, d)


def check(env, aid):
    return env.client.post(f"/api/disputes/{REAL}/actions/{aid}/check").json()["status"]


def test_check_needs_the_supporting_info_count_to_go_up(app_env):
    env, aid = unknown(app_env)
    serve(env, status="RESOLVED")  # a status change alone proves nothing for this action
    assert check(env, aid) == "failed"


def test_check_confirms_when_a_seller_supporting_info_entry_appears(app_env):
    env, aid = unknown(app_env)
    serve(env, supporting_info=[{"source": "SUBMITTED_BY_BUYER", "notes": "x"}])  # a buyer entry does not count
    assert check(env, aid) == "failed"


def test_check_confirms_on_a_new_seller_entry(app_env):
    env, aid = unknown(app_env)
    serve(env, supporting_info=[{"source": "SUBMITTED_BY_SELLER", "notes": "x"}])
    assert check(env, aid) == "sent"


def test_missing_supporting_info_key_counts_as_zero_and_old_pre_counts_work():
    assert pipeline._counts(real())["seller_supporting"] == 0
    s = real()
    s["dispute"]["supporting_info"] = [{"source": "SUBMITTED_BY_SELLER"}, {"source": "SUBMITTED_BY_BUYER"}, {"source": "SUBMITTED_BY_SELLER"}]
    assert pipeline._counts(s)["seller_supporting"] == 2


def test_envelope_never_auto_sends_supporting_info(app_env):
    env = setup(app_env)
    env.client.put("/api/envelope", json={"max_offer_amount": 40, "max_refund_pct": 100, "daily_cap": 50, "allowed_actions": ["make_offer", "send_message"],
                                          "allowed_reasons": ["MERCHANDISE_OR_SERVICE_NOT_RECEIVED"], "expires_at": "2030-01-01T00:00:00Z"})
    rid = env.client.post(f"/api/disputes/{REAL}/runs", json={"mode": "desk"}).json()["run_id"]
    e = env.client.get(f"/api/runs/{rid}").json()["envelope"]
    assert e["ok"] is False and "not allowed by the envelope" in e["reason"]
    assert store.q("SELECT * FROM actions") == [] and [c for c in env.pp.calls if c["method"] == "POST"] == []
    with pytest.raises(Exception):
        checks.Envelope.model_validate({"max_offer_amount": 1, "max_refund_pct": 1, "daily_cap": 1, "allowed_actions": ["provide_supporting_info"], "allowed_reasons": [], "expires_at": "2030-01-01T00:00:00Z"})


# sha256 of the real sandbox merchant/payer ids, so the leak check doesn't publish them
_LEAK_HASHES = {"1138b9e011225b37b99a9d3718bc1de017fb89f303daaba30b2a8429cdc29ff9", "0620c4fb8a8f0bb3cdb9b3a20fd1d9b53b8e912ed8326df8b38865045be53115"}


def test_real_sandbox_account_ids_are_not_in_the_fixture():
    import re
    from pathlib import Path
    text = (Path(__file__).parent / "fixtures" / "real_inr_claim.json").read_text(encoding="utf-8")
    found = {hashlib.sha256(t.encode()).hexdigest() for t in re.findall(r"[A-Z0-9]{13}", text)}
    assert not (found & _LEAK_HASHES)
