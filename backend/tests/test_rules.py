import pytest

import fakes
from app import facts, rules

ALL4 = ["provide-evidence", "make-offer", "accept-claim", "send-message"]


def pre_for(name="inr_delivered", mut=None):
    s = fakes.snapshot(name)
    if mut:
        mut(s)
    return rules.precheck(facts.build_facts(s), s), s


def test_links_to_legal_actions_and_sandbox_calls():
    def m(s):
        s["dispute"]["links"] = fakes.snapshot()["dispute"]["links"] + [
            {"href": "https://x/v1/customer/disputes/PP-D-1001/require-evidence", "rel": "r", "method": "POST"},
            {"href": "https://x/v1/customer/disputes/PP-D-1001/adjudicate", "rel": "a", "method": "POST"}]
    p, _ = pre_for(mut=m)
    assert p.legal_actions == ["provide_evidence", "make_offer", "accept_claim", "send_message"] and not p.advisory
    assert p.sandbox_calls == ["require_evidence", "adjudicate"]
    assert p.advisory_text is None


def test_chargeback_links_only_two():
    p, _ = pre_for("chargeback_no_messages")
    assert p.legal_actions == ["provide_evidence", "accept_claim"]
    assert p.offer_types == [] and p.accept_types == ["REFUND"]


@pytest.mark.parametrize("status,text", [
    ("WAITING_FOR_SELLER_RESPONSE", "PayPal hasn't opened seller actions on this case yet. They usually appear a few minutes after a dispute opens."),
    ("UNDER_REVIEW", "PayPal is reviewing this case; no seller action is possible now."),
    ("RESOLVED", "This case is resolved."),
    ("OPEN", "No seller actions are available in status OPEN."),
])
def test_advisory_text_per_status(status, text):
    def m(s):
        s["dispute"]["links"] = [l for l in s["dispute"]["links"] if l["rel"] == "self"]
        s["dispute"]["status"] = status
    p, _ = pre_for(mut=m)
    assert p.advisory and p.advisory_text == text
    assert p.legal_actions == ["provide_evidence", "make_offer", "accept_claim", "send_message"]


def test_offer_and_accept_types():
    p, _ = pre_for("snad_mandate")
    assert p.offer_types == ["REFUND", "REFUND_WITH_RETURN"]
    assert p.accept_types == ["REFUND", "PARTIAL_REFUND"]

    def absent(s):
        s["dispute"].pop("allowed_response_options")
    p, _ = pre_for(mut=absent)
    assert p.offer_types == ["REFUND"] and p.accept_types == ["REFUND"]

    def narrow(s):
        s["dispute"]["allowed_response_options"] = {"accept_claim": {"accept_claim_types": ["REFUND_WITH_RETURN", "PARTIAL_REFUND"]}}
    p, _ = pre_for(mut=narrow)
    assert p.accept_types == ["PARTIAL_REFUND"]


def test_types_empty_when_action_not_legal():
    def m(s):
        s["dispute"]["links"] = [l for l in s["dispute"]["links"] if l["rel"] in ("self", "provide-evidence", "send-message")]
    p, _ = pre_for(mut=m)
    assert p.offer_types == [] and p.accept_types == []


def cov(name="inr_delivered", **mut):
    def m(s):
        for k, f in mut.items():
            f(s)
    p, _ = pre_for(name, m)
    return p.coverage, p.coverage_rule


def reason(r):
    return lambda s: s["dispute"].__setitem__("reason", r)


def test_coverage_rule1_snad():
    assert cov("snad_mandate") == ("not_covered", "Seller Protection does not cover not-as-described claims.")


def test_coverage_rule2_cnp():
    assert cov("cnp_refunded") == ("not_covered", "Refund-not-processed is not a Seller Protection claim type (our reading).")


def test_coverage_rule3_digital():
    c = cov(k=lambda s: s["shop"]["product"].__setitem__("kind", "digital"))
    assert c == ("not_covered", "Intangible items are not covered by Seller Protection.")


def test_coverage_rule4_not_eligible():
    c = cov(k=lambda s: s["capture"].__setitem__("seller_protection", {"status": "NOT_ELIGIBLE"}))
    assert c == ("not_covered", "PayPal marks this payment not eligible.")


def test_coverage_rule5_other_address():
    c = cov(k=lambda s: s["derived"].__setitem__("address_match", "no"))
    assert c == ("not_covered", "Shipped to a different address than the one on the transaction.")


def test_coverage_rule6_inr_variants():
    assert cov() == ("covered", "Delivered to the address on the transaction (international shipments need proof of delivery).")
    assert cov(k=lambda s: s["shop"].__setitem__("shipment", None)) == ("not_covered", "No proof of shipment.")
    assert cov(k=lambda s: s["shop"]["shipment"].__setitem__("delivered_at", None)) == ("unknown", "Tracking shows no delivery yet.")
    assert cov(k=lambda s: s["derived"].pop("address_match")) == ("unknown", "Delivered, but the delivery address can't be checked.")


def internal(s):
    s["dispute"]["dispute_channel"] = "INTERNAL"


def test_coverage_rule0_only_external_card_chargebacks_are_never_covered():
    text = "Card chargebacks are not covered by PayPal Seller Protection."
    assert cov("chargeback_no_messages") == ("not_covered", text)  # stage CHARGEBACK, channel EXTERNAL
    external = lambda s: s["dispute"].__setitem__("dispute_channel", "EXTERNAL")  # noqa: E731
    # the channel rule comes first, ahead of every reason-based rule
    assert cov("inr_delivered", k=external) == ("not_covered", text)
    assert cov("snad_mandate", k=external) == ("not_covered", text)
    # a CHARGEBACK stage on PayPal's own (INTERNAL) channel is not a card chargeback; no channel at all is not one either
    assert cov("chargeback_no_messages", k=internal)[0] == "covered"
    assert cov("inr_delivered", k=lambda s: s["dispute"].__setitem__("dispute_life_cycle_stage", "CHARGEBACK"))[0] == "covered"
    assert cov("inr_delivered")[0] == "covered"


def test_coverage_not_eligible_transaction():
    def m(s):
        s["dispute"]["disputed_transactions"][0]["seller_protection_eligible"] = False
    assert cov("inr_delivered", k=m) == ("not_covered", "PayPal marks this transaction not eligible for Seller Protection.")
    # the channel rule still comes first
    assert cov("inr_delivered", k=lambda s: (m(s), s["dispute"].__setitem__("dispute_channel", "EXTERNAL")))[1].startswith("Card chargebacks")


def test_coverage_rule7_unauthorised():
    def at(shipped):
        def m(s):
            internal(s)
            s["shop"]["shipment"]["shipped_at"] = shipped
        return m
    assert cov("chargeback_no_messages", k=internal) == ("covered", "Shipped to the transaction address with proof of shipment.")
    # shipped after the dispute window
    assert cov("chargeback_no_messages", k=at("2026-11-05T09:00:00Z")) == ("unknown", "Can't confirm shipment to the transaction address in time.")
    assert cov("chargeback_no_messages", k=lambda s: (internal(s), s["derived"].pop("address_match")))[0] == "unknown"
    assert cov("chargeback_no_messages", k=lambda s: (internal(s), s["shop"].__setitem__("shipment", None)))[0] == "unknown"
    # exactly dispute created + 2 days still counts
    assert cov("chargeback_no_messages", k=at("2026-11-04T10:00:00Z"))[0] == "covered"


def test_coverage_rule8_other():
    assert cov(k=reason("ITEM_NOT_AS_PROMISED_OTHER")) == ("unknown", "No rule matched.")


def test_coverage_first_match_wins():
    # digital + not-eligible: rule 3 text wins over rule 4
    def m(s):
        s["shop"]["product"]["kind"] = "digital"
        s["capture"]["seller_protection"] = {"status": "NOT_ELIGIBLE"}
    assert cov(k=m)[1] == "Intangible items are not covered by Seller Protection."


def test_warnings():
    def m(s):
        s["dispute"]["dispute_amount"]["value"] = "800.00"
    p, _ = pre_for(mut=m)
    assert "Over $750 without signature confirmation." in p.warnings

    def signed(s):
        m(s)
        s["shop"]["shipment"]["signature"] = 1
    assert "Over $750 without signature confirmation." not in pre_for(mut=signed)[0].warnings

    def soon(s):
        s["meta"]["now"] = "2026-11-11T10:00:00Z"
    p, _ = pre_for(mut=soon)
    assert p.hours_left == 24.0 and "Under 48 hours to respond." in p.warnings

    def late(s):
        s["meta"]["now"] = "2026-11-13T10:00:00Z"
    p, _ = pre_for(mut=late)
    assert "The response deadline has passed." in p.warnings and "Under 48 hours to respond." not in p.warnings
    assert pre_for()[0].warnings == []


def test_incomplete_and_missing_shop_warnings():
    def err(s):
        s["errors"] = [{"call": "GET /v2/payments/captures/CAP1", "status": 404, "debug_id": "x"}]
    assert "Incomplete PayPal facts" in pre_for(mut=err)[0].warnings

    def refund_err(s):
        s["errors"] = [{"call": "GET /v2/payments/refunds/RF1", "status": 404, "debug_id": "x"}]
    assert "Incomplete PayPal facts" not in pre_for(mut=refund_err)[0].warnings

    def noshop(s):
        s["shop"] = {"product": None, "order": None, "shipment": None, "refunds": [], "mandate": None, "notes": []}
    assert "No shop record for this order (shipping and listing facts missing)" in pre_for(mut=noshop)[0].warnings
