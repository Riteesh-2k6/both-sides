"""Pre-push review round 6 regressions."""
import json

import httpx

import fakes
from app import llm, shop
from test_live_findings import S, validate
from test_llm import GOOD, T, ok, serve, settings


def test_truncation_handles_long_list_of_long_strings(monkeypatch):
    settings(monkeypatch)
    d = fakes.turn_buyer().model_dump(mode="json")
    c = dict(d["claims"][0]); c["text"] = "y" * 400
    d["claims"] = [dict(c) for _ in range(8)]
    seen = serve(monkeypatch, lambda r, n: ok(json.dumps(d)))
    parsed, _ = llm.chat_json(T, "S", "U", step="buyer_r1")
    assert len(parsed.claims) == 6 and all(len(x.text) == 300 for x in parsed.claims) and len(seen) == 1


def test_sentence_that_is_only_fact_ids_is_dropped():
    r = validate([S("(pp.dispute.id)", ["pp.dispute.id"]), S("Tracking is EE123456789IN.", ["shop.shipment.tracking"])])
    assert r.message_text == "Tracking is EE123456789IN." and any("empty" in w for _, w in r.dropped_sentences)


def test_match_size_prefers_the_most_specific_size():
    assert shop.match_size("twin xl", ["Twin", "Twin XL"]) == "Twin XL"
    assert shop.match_size("twin", ["Twin", "Twin XL"]) == "Twin"
    assert shop.match_size("queen", ["", "Queen"]) == "Queen"


def test_tracker_parts_from_id_only_tracker():
    assert shop.tracker_parts({"id": "7EK39033M22602452-EE264875961IN", "status": "SHIPPED"}) == (shop.DEFAULT_CARRIER, "EE264875961IN")
    assert shop.tracker_parts({"carrier": "DHL", "tracking_number": "1234567890"}) == ("DHL", "1234567890")


def test_local_object_mode_keeps_limits(monkeypatch):
    settings(monkeypatch, LLM_BASE_URL="http://localhost:8080/v1", LLM_JSON_MODE="object")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "S", "U", step="buyer_r1")
    assert "maxLength" in seen[0][1]["messages"][0]["content"]
