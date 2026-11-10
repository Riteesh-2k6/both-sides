"""Design changes after bench run 2 (PRD: if the desk loses, the design changes before the video)."""
import fakes
from app import facts, rules


def _pre(refunded=None, links=None):
    s = fakes.snapshot()  # dispute amount 140.00 USD
    if refunded is not None:
        s["refunds"] = [{"id": "RF-1", "amount": {"currency_code": "USD", "value": refunded}, "status": "COMPLETED"}]
    if links is not None:
        s["dispute"]["links"] = [{"href": f"https://api/v1/customer/disputes/PP-D-1001/{l}", "rel": l} for l in links]
    return rules.precheck(facts.build_facts(s), s)


def test_full_refund_removes_moves_that_pay_again():
    # CNP-1: refunded in full, and the desk offered a second full refund.
    pre = _pre("140.00")
    assert pre.legal_actions == ["provide_evidence", "send_message"]
    assert pre.offer_types == [] and pre.accept_types == [] and not pre.advisory
    assert any("pay the buyer twice" in w for w in pre.warnings)


def test_partial_refund_keeps_offers():
    pre = _pre("40.00")
    assert "make_offer" in pre.legal_actions and "accept_claim" in pre.legal_actions
    assert not any("twice" in w for w in pre.warnings)


def test_full_refund_with_only_paying_moves_is_advisory():
    pre = _pre("140.00", links=["make-offer", "accept-claim"])
    assert pre.advisory and "twice" in pre.advisory_text
    assert not set(pre.legal_actions) & set(rules.PAYS_BUYER)


def test_failed_refund_does_not_count():
    s = fakes.snapshot()
    s["refunds"] = [{"id": "RF-1", "amount": {"currency_code": "USD", "value": "140.00"}, "status": "FAILED"}]
    assert "make_offer" in rules.precheck(facts.build_facts(s), s).legal_actions


# ---- money-path review of the guard ----

def _snap(refunds=(), **edits):
    s = fakes.snapshot()
    s["refunds"] = [{"id": f"RF-{i}", "amount": {"currency_code": c, "value": val}, "status": st} for i, (val, st, c) in enumerate(refunds, 1)]
    for k, val in edits.items():
        s[k] = val
    return s


def _legal(s):
    return rules.precheck(facts.build_facts(s), s).legal_actions


def test_pending_refund_counts_as_paid():
    assert "make_offer" not in _legal(_snap([("140.00", "PENDING", "USD")]))


def test_two_partial_refunds_that_add_up_count():
    assert "make_offer" not in _legal(_snap([("100.00", "COMPLETED", "USD"), ("40.00", "COMPLETED", "USD")]))
    assert "make_offer" in _legal(_snap([("100.00", "COMPLETED", "USD"), ("39.00", "COMPLETED", "USD")]))


def test_unreadable_or_foreign_refund_fails_safe():
    s = _snap(errors=[{"call": "GET /v2/payments/refunds/RF-9", "status": 500}])
    pre = rules.precheck(facts.build_facts(s), s)
    assert "make_offer" not in pre.legal_actions and any("could not be read" in w for w in pre.warnings)
    assert "make_offer" not in _legal(_snap([("10.00", "COMPLETED", "INR")]))


def test_refunded_capture_counts_even_without_shop_refund_rows():
    s = fakes.snapshot()
    s["capture"]["status"] = "REFUNDED"
    assert "accept_claim" not in _legal(s)


def test_malformed_refund_amount_does_not_crash():
    s = _snap([("abc", "COMPLETED", "USD")])
    assert facts.build_facts(s)["check.refunded_total"].value == "0.00 USD"


def test_envelope_reason_is_the_advisory_text():
    from dataclasses import replace
    from datetime import datetime, timezone
    from app import checks
    from test_pipeline import env_obj, offer_v
    s = _snap([("140.00", "COMPLETED", "USD")])
    s["dispute"]["links"] = [{"href": f"https://api/v1/customer/disputes/PP-D-1001/{l}", "rel": l} for l in ("make-offer", "accept-claim")]
    pre = replace(rules.precheck(facts.build_facts(s), s), coverage="not_covered")
    assert pre.advisory and not any("twice" in w for w in pre.warnings)  # said once, as the advisory text
    e = env_obj(max_offer_amount=200.0, daily_cap=200.0)
    ok, why = checks.check_envelope(e, checks.envelope_hash(e), offer_v(140.0), pre, "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                                    14000, 0, datetime.now(timezone.utc), False, {"agreed": True, "differences": []}, [])
    assert not ok and "twice" in why


def test_blank_question_adds_no_note(monkeypatch):
    from test_pipeline import converging_script, run, verdict
    sc = converging_script()
    sc["judge_a"] = [verdict(needs_fact="   ")]
    sc["judge_b"] = [verdict()]
    res, _ = run(monkeypatch, sc)
    assert res.status == "done" and res.outcome["differences"] == []


# ---- leaks found in run-3 traces (SNAD-2, UNAUTH-3 persuasive variants) ----

def test_trailing_unbracketed_id_list_is_stripped():
    from app import checks
    assert checks._strip_fact_ids("The address differs. Fact IDs: check.address_match") == "The address differs."
    assert checks._strip_fact_ids("Shipped. fact_ids: shop.shipment.tracking, pp.order.tracking") == "Shipped."


def test_ids_woven_into_prose_drop_the_sentence():
    from test_live_findings import S, validate
    r = validate([S("Tracking is EE123456789IN.", ["shop.shipment.tracking"]),
                  S("Based on fact_ids shop.order.size and shop.shipment.size_shipped, I am offering a refund.", ["shop.order.size"])])
    assert r.message_text == "Tracking is EE123456789IN."
    assert ("Based on fact_ids shop.order.size and shop.shipment.size_shipped, I am offering a refund.", "fact id left in the text") in r.dropped_sentences
