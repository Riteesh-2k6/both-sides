"""Harness fixes found by the first full bench run on Qwen3-4B (10 Nov). The held-out INR-3 result
motivated the inline-id stripping, so the re-run's held-out numbers are not fully blind (README says so)."""
import fakes
from app import checks, facts, pipeline, rules
from test_live_findings import S, validate


def _claim_snapshot():
    return fakes.snapshot("real_inr_claim")


def test_supporting_info_evidence_types_are_not_enforced():
    # UNAUTH-1/2: judge picked provide_supporting_info and listed facts typed PROOF_OF_DELIVERY_SIGNATURE;
    # PayPal's supporting info is notes only, so the type is meaningless there - only the fact ids must exist.
    s = _claim_snapshot(); fs = facts.build_facts(s)
    v = fakes.verdict(action="provide_supporting_info",
                      evidence=[{"fact_id": "check.address_match", "evidence_type": "PROOF_OF_DELIVERY_SIGNATURE"}],
                      message=[{"text": "The parcel was posted by India Post with tracking on file.", "fact_ids": ["shop.shipment.tracking", "shop.shipment.carrier"], "courtesy": False}])
    val = checks.validate_verdict(v, rules.precheck(fs, s), fs, s)
    assert val.ok, val.errors
    bad = fakes.verdict(action="provide_supporting_info", evidence=[{"fact_id": "pp.nope", "evidence_type": "OTHER"}],
                        message=v.model_dump()["message"])
    assert "evidence cites unknown fact pp.nope" in checks.validate_verdict(bad, rules.precheck(fs, s), fs, s).errors


def test_inline_fact_reference_formats_are_stripped():
    # INR-3: "(fact_id: check.address_match)" survived the stripper, so every sentence was dropped.
    r = validate([S("Tracking is EE123456789IN (fact_id: shop.shipment.tracking).", ["shop.shipment.tracking"]),
                  S("It was posted by India Post (facts: shop.shipment.carrier, shop.shipment.tracking).", ["shop.shipment.carrier"]),
                  S("We checked the address [fact: check.address_match].", ["check.address_match"])])
    assert r.message_text == "Tracking is EE123456789IN. It was posted by India Post. We checked the address."


def test_agreeing_readings_with_one_question_are_a_verdict_not_a_deadlock(app_env):
    # INR-2: both readings chose make_offer; one also asked a question. Agreement on the action stands;
    # the question is kept as a note. Only different actions deadlock.
    from test_api import setup, start
    v = fakes.verdict()
    script = fakes.converging_script(v=v)
    script["judge_b"] = [v.model_copy(update={"needs_fact": "Was the parcel signed for?"}, deep=True)]
    env = setup(app_env, script=script)
    run = env.client.get(f"/api/runs/{start(env).json()['run_id']}").json()
    assert run["status"] == "done" and run["outcome"]["agreed"] is True
    assert any("Was the parcel signed for?" in d for d in run["outcome"]["differences"])


def test_both_readings_asking_with_the_same_action_is_a_verdict(app_env):
    # superseded after bench run 2: only different actions deadlock (see test_pipeline)
    from test_api import setup, start
    v = fakes.verdict(needs_fact="Was the parcel signed for?")
    env = setup(app_env, script=fakes.converging_script(v=v))
    run = env.client.get(f"/api/runs/{start(env).json()['run_id']}").json()
    assert run["status"] == "done" and len(run["outcome"]["differences"]) == 2
