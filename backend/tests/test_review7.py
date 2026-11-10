"""Pre-push review round 7 regressions."""
import httpx

from app import llm, shop
from test_live_findings import S, validate


def test_greeting_with_a_stray_fact_id_is_not_a_supported_message():
    r = validate([S("Thanks!", ["pp.dispute.id"], courtesy=True)])
    assert not r.ok and "no supported sentences" in r.errors


def test_long_fact_sentence_mislabelled_courtesy_still_counts():
    r = validate([S("Hello, the item shipped on 2026-10-29 with tracking EE123456789IN.",
                    ["shop.shipment.shipped_at", "shop.shipment.tracking"], courtesy=True)])
    assert r.ok


def test_reset_seconds_rejects_zero_negative_and_non_finite():
    for v in ("0", "-5", "inf", "nan"):
        assert llm._reset_seconds(httpx.Headers({"retry-after": v})) is None


def test_tracker_without_dash_has_no_tracking_number():
    assert shop.tracker_parts({"id": "7EK39033M22602452", "status": "SHIPPED"}) == (shop.DEFAULT_CARRIER, "")


def test_match_size_bare_number_and_ties():
    assert shop.match_size("18", ["16 in", "18 in"]) == "18 in"
    assert shop.match_size("king queen", ["Queen", "King"]) is None
