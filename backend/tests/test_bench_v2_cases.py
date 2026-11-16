"""bench/cases-v2.json: the 12 v1 cases (keys unchanged) for tuning plus 16 new held-out cases, balanced between
cases the seller should defend and cases the buyer should win."""
import json
import re

import pytest

from app import bench, config, facts, rules

DOC = json.loads((config.BENCH_DIR / "cases-v2.json").read_text(encoding="utf-8"))
V1 = json.loads((config.BENCH_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
CASES = DOC["cases"]
BY_ID = {c["id"]: c for c in CASES}
NEW = ["INR-4", "INR-5", "SNAD-4", "SNAD-5", "UNAUTH-4", "UNAUTH-5", "CNP-3", "AGENT-2",
       "INR-6", "INR-7", "INR-8", "SNAD-6", "SNAD-7", "UNAUTH-6", "UNAUTH-7", "CNP-4"]
COVERAGE = {"INR-4": "covered", "INR-5": "covered", "SNAD-4": "not_covered", "SNAD-5": "not_covered", "UNAUTH-4": "covered", "UNAUTH-5": "covered",
            "CNP-3": "not_covered", "AGENT-2": "not_covered", "INR-6": "not_covered", "INR-7": "not_covered", "INR-8": "unknown", "SNAD-6": "not_covered",
            "SNAD-7": "not_covered", "UNAUTH-6": "not_covered", "UNAUTH-7": "unknown", "CNP-4": "not_covered"}
PAYS = lambda e: e["action"] == "accept_claim" or (e["action"] == "make_offer" and e.get("type") in ("REFUND", "REFUND_WITH_REPLACEMENT"))  # noqa: E731


def snap(cid):
    return json.loads((config.BENCH_DIR / BY_ID[cid]["snapshot"]).read_text(encoding="utf-8"))


def pre(cid):
    s = snap(cid)
    fs = facts.build_facts(s)
    return fs, rules.precheck(fs, s), s


def test_header_and_sets():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", DOC["_written"]) and DOC["dispute_fee_usd"] == 15.0 and "assumption" in DOC["_fee_note"]
    assert [c["id"] for c in CASES if not c["heldout"]] == [c["id"] for c in V1] and [c["id"] for c in CASES if c["heldout"]] == NEW
    for c in CASES:
        assert set(c) == {"id", "title", "reason", "stage", "heldout", "side", "snapshot", "expected", "flipped_text", "note"}, c["id"]
        assert c["stage"] == ("CHARGEBACK" if c["id"].startswith("UNAUTH") else "INQUIRY") and c["snapshot"] == f"snapshots/{c['id']}.json"


def test_v1_cases_keep_their_answer_keys_word_for_word():
    for v in V1:
        c = BY_ID[v["id"]]
        assert {k: c[k] for k in v if k != "heldout"} == {k: v[k] for k in v if k != "heldout"}, v["id"]


def test_sides_are_balanced_and_agree_with_the_keys():
    for heldout, half in ((False, 6), (True, 8)):
        sides = [c["side"] for c in CASES if c["heldout"] == heldout]
        assert sides.count("seller") == sides.count("buyer") == half
    for c in CASES:
        any_of = c["expected"]["any_of"]
        if c["side"] == "seller":  # defend: evidence, notes, or a refund only once the item is back
            assert not any(PAYS(e) for e in any_of), c["id"]
        else:
            assert c["side"] == "buyer" and any(PAYS(e) for e in any_of), c["id"]


def test_held_out_amounts_weigh_both_sides_alike():
    amount = lambda c: float(snap(c["id"])["dispute"]["dispute_amount"]["value"])  # noqa: E731
    held = [c for c in CASES if c["heldout"]]
    seller = sum(amount(c) for c in held if c["side"] == "seller")
    buyer = sum(amount(c) + DOC["dispute_fee_usd"] for c in held if c["side"] == "buyer")
    assert (seller, buyer) == (684.0, 688.0)


@pytest.mark.parametrize("cid", NEW)
def test_new_cases_build_and_are_answerable(cid):
    c = BY_ID[cid]
    fs, p, s = pre(cid)
    assert s["meta"] == {"source": "synthetic", "now": "2026-11-10T09:00:00Z"} and s["receipts"] == {} and s["errors"] == [] and "v2 held out" in s["_synthetic"]
    assert s["dispute"]["reason"] == c["reason"] and s["dispute"]["dispute_life_cycle_stage"] == c["stage"] and s["dispute"]["dispute_id"] == f"PP-R-SYN-{cid}"
    assert fs["pp.dispute.amount"].value == fs["pp.capture.amount"].value == f"{s['shop']['product']['price_cents'] / 100:.2f} USD"
    assert p.coverage == COVERAGE[cid], (p.coverage, p.coverage_rule)
    assert not p.advisory and p.warnings == []
    for e in c["expected"]["any_of"]:
        assert e["action"] in p.legal_actions, (cid, e)
        if "type" in e:
            assert e["type"] in (p.offer_types if e["action"] == "make_offer" else p.accept_types), (cid, e)
    assert s["buyer_text"] == s["dispute"]["evidences"][0]["notes"] == facts._buyer_text(s["dispute"])


def test_situations_show_up_in_the_facts():
    f = lambda cid: pre(cid)[0]  # noqa: E731
    v = lambda cid, k: f(cid)[k].value if k in f(cid) else None  # noqa: E731
    assert v("INR-4", "shop.shipment.signature") == "yes" and v("UNAUTH-4", "shop.shipment.signature") == "yes"
    assert v("SNAD-4", "shop.order.size") == v("SNAD-4", "shop.shipment.size_shipped") == "16 in"
    assert (v("SNAD-6", "shop.order.size"), v("SNAD-6", "shop.shipment.size_shipped")) == ("King", "Queen")
    assert (v("SNAD-7", "shop.order.size"), v("SNAD-7", "shop.shipment.size_shipped")) == ("18 in", "16 in")
    assert "No return has reached the workshop" in v("CNP-3", "merchant.note.1") and v("CNP-3", "shop.shipment.return_delivered_at") is None
    assert v("CNP-4", "shop.shipment.return_delivered_at") == "2026-10-30T10:00:00Z" and v("CNP-4", "check.refunded_total") == "0.00 USD"
    assert json.loads(v("AGENT-2", "shop.mandate.choice"))["size"] == json.loads(v("AGENT-2", "shop.mandate.constraints"))["size"] == "Queen"
    assert v("AGENT-2", "shop.order.via") == "agent" and BY_ID["AGENT-2"]["expected"]["fault_top"] == "buyer_changed_mind"
    for cid in ("INR-6", "UNAUTH-7"):  # never shipped
        assert not [k for k in f(cid) if k.startswith("shop.shipment.")] and "pp.order.tracking" not in f(cid)
    assert v("INR-8", "shop.shipment.delivered_at") == "not delivered" and v("INR-8", "shop.shipment.shipped_at") == "2026-10-03T09:00:00Z"
    assert v("INR-7", "check.address_match") == v("UNAUTH-6", "check.address_match") == "no"


def test_controls_split_the_held_out_set_by_side():
    held = [c for c in CASES if c["heldout"]]
    for c in held:
        _, p, s = pre(c["id"])
        amount = float(s["dispute"]["dispute_amount"]["value"])
        acc = bench._control(bench.control_accept, p, c["expected"], c["side"], amount, 15.0)
        con = bench._control(bench.control_contest, p, c["expected"], c["side"], amount, 15.0)
        assert acc["match"] == (c["side"] == "buyer") and con["action_match"] == (c["side"] == "seller"), c["id"]
        assert (acc["cost"] == 0) == (c["side"] == "buyer") and (con["cost"] == 0) == (c["side"] == "seller"), c["id"]


@pytest.mark.parametrize("cid", NEW)
def test_buyer_text_and_flipped_text(cid):
    c, s = BY_ID[cid], snap(cid)
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", s["buyer_text"]) if x]
    assert 2 <= len(sentences) <= 4 and re.search(r"\b(I|my|me)\b", s["buyer_text"]) and re.search(r"\b(I|my|me)\b", c["flipped_text"], re.I)
    flipped = c["flipped_text"]
    assert flipped != s["buyer_text"] and len(re.findall(r"[.!?]", flipped)) >= 2 and "ignore previous" not in (flipped + s["buyer_text"]).lower()
    emotional = len(re.findall(r"!", flipped)) + len(re.findall(r"\b[A-Z]{4,}\b", flipped)) + len(re.findall(r"\b(furious|angry|upset|outrageous|outraged|unacceptable|fraud|fed up|shocking|shocked|disappointed|demand|ridiculous|frustrated|annoyed)\b", flipped, re.I))
    assert emotional >= 2 and emotional > len(re.findall(r"!", s["buyer_text"]))


def test_cli_runs_every_v2_case_on_a_scripted_fake(tmp_path, monkeypatch, capsys):
    import shutil
    import fakes
    from app import llm
    b = tmp_path / "bench"
    shutil.copytree(config.BENCH_DIR, b, ignore=shutil.ignore_patterns("results"))
    monkeypatch.setattr(config, "BENCH_DIR", b)
    monkeypatch.setattr(config, "S", config.load({"LLM_MODEL": "scripted"}))
    v = fakes.verdict()  # provide_evidence citing the tracking: refused where nothing shipped or evidence isn't allowed
    script = {"quarantine": [fakes.quarantine_out()], "buyer_r1": [fakes.turn_buyer()], "merchant_r1": [fakes.turn_merchant()],
              "judge_a": [v], "judge_b": [v.model_copy(deep=True)], "baseline": [v]}
    monkeypatch.setattr(llm, "FAKE", fakes.fake_llm(script, repeat=True))
    bench.main(["run", "--heldout"])
    res = json.loads((b / "results" / "latest.json").read_text(encoding="utf-8"))
    assert len(res["cases"]) == 28 and res["case_file"] == "cases-v2.json" and res["dispute_fee_usd"] == 15.0
    t = res["totals"]["heldout"]
    assert t["n"] == 16 and t["sides"] == {"seller": 8, "buyer": 8} and t["worst_usd"] == 1372.0
    sy = t["systems"]
    assert (sy["always_accept"]["match"], sy["always_accept"]["cost_usd"]) == (8, 684.0)
    assert (sy["always_contest"]["action_match"], sy["always_contest"]["cost_usd"]) == (8, 688.0)
    assert sy["desk"]["blocked"] > 0 and sy["desk"]["unavailable"] == 0
    assert sy["desk"]["match"] + sy["desk"]["blocked"] <= 16
    assert "heldout: n=16" in capsys.readouterr().out


def test_new_snapshots_are_masked_and_distinct():
    seen = set()
    for cid in NEW:
        s = snap(cid)
        blob = json.dumps(s, ensure_ascii=False)
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", blob) and "Doe" not in blob and "sb-" not in blob, cid
        assert s["dispute"]["disputed_transactions"][0]["buyer"]["name"] == "J." and s["order"]["purchase_units"][0]["shipping"]["address"]["address_line_1"] == "•••"
        sh = s["shop"]["shipment"]
        if sh:
            assert sh["tracking"] not in seen
            seen.add(sh["tracking"])
