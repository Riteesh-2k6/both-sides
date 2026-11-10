"""Regressions found by the first live hearing on a real sandbox claim (10 Nov 2026)."""
from test_review5 import S, validate


def test_courtesy_flag_on_a_fact_citing_sentence_is_ignored_not_dropped():
    # Live: the judge flagged "...shipped on 2026-10-29 with tracking EE123456789IN..." as courtesy;
    # the digit rule dropped the most useful sentence. A sentence that cites facts is checked as a fact sentence.
    r = validate([S("Hello, the item shipped on 2026-10-29 with tracking EE123456789IN.",
                    ["shop.shipment.shipped_at", "shop.shipment.tracking"], courtesy=True),
                  S("Thank you for your patience.", [], courtesy=True)])
    texts = [k["text"] for k in r.kept_sentences]
    assert texts[0].startswith("Hello, the item shipped") and r.kept_sentences[0]["courtesy"] is False
    assert r.dropped_sentences == []


def test_fact_citing_courtesy_sentence_still_fails_fact_checks():
    r = validate([S("We shipped it on 2026-12-25.", ["shop.shipment.shipped_at"], courtesy=True),
                  S("Tracking is EE123456789IN.", ["shop.shipment.tracking"])])
    assert r.dropped_sentences and "not in cited facts" in r.dropped_sentences[0][1]


def test_fact_ids_written_into_the_text_are_stripped_before_sending():
    # Live: "The item was shipped ... EE123456789IN (shop.shipment.shipped_at, shop.shipment.carrier)."
    r = validate([S("Tracking is EE123456789IN (shop.shipment.tracking, shop.shipment.carrier).", ["shop.shipment.tracking"]),
                  S("We reviewed the case [pp.dispute.id].", ["pp.dispute.id"])])
    assert r.message_text == "Tracking is EE123456789IN. We reviewed the case."
    assert "shop.shipment" not in r.message_text and "pp.dispute" not in r.message_text


def test_inference_about_the_buyers_belief_may_differ_from_the_record():
    # Bench trial INR-1: the buyer's advocate's "why they filed" ("the buyer believes it was never received")
    # cited the delivered record and was dropped as a contradiction, silencing the buyer's side.
    from app import checks, facts
    from app.agents import Claim
    import fakes
    fs = facts.build_facts(fakes.snapshot("inr_delivered"))
    belief = Claim(id="W1", text="The buyer likely believes the quilt was never received, perhaps left with a neighbour.",
                   kind="inference", evidence_ids=["shop.shipment.delivered_at"], responds_to=[])
    fact = Claim(id="B1.1", text="The quilt was never received.", kind="fact", evidence_ids=["shop.shipment.delivered_at"], responds_to=[])
    kept, dropped = checks.verify_claims([belief, fact], fs)
    assert [c.id for c in kept] == ["W1"] and dropped[0][0].id == "B1.1"
