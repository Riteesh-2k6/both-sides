"""Bench v2 scoring (written before the code): action-only and full matches, blocked verdicts as their own outcome,
model failures kept apart, and a cost of mistakes in dollars from each case's amount."""
import hashlib
import json

from app import bench, config, facts, rules
from app.pipeline import RunResult

FEE = 15.0


def out(action="provide_evidence", type=None, amount=None, valid=True, deadlock=False, unavailable=False, fault_top=None):
    return {"action": action, "type": type, "amount": amount, "valid": valid, "deadlock": deadlock, "unavailable": unavailable, "match": False, "fault_top": fault_top}


def exp(*any_of, never=(), fault_top=None):
    e = {"any_of": list(any_of), "never": list(never)}
    if fault_top:
        e["fault_top"] = fault_top
    return e


BLOCKED = out(action=None, valid=False)
DEADLOCK = out(action=None, valid=False, deadlock=True)
DOWN = out(action=None, valid=False, unavailable=True)


def test_direction_of_each_move():
    assert bench.direction("accept_claim", "REFUND") == bench.direction("accept_claim", "PARTIAL_REFUND") == "pay"
    assert bench.direction("make_offer", "REFUND") == bench.direction("make_offer", "REFUND_WITH_REPLACEMENT") == "pay"
    assert bench.direction("make_offer", "REFUND_WITH_RETURN") == "return"
    for a in ("provide_evidence", "provide_supporting_info", "send_message"):
        assert bench.direction(a, None) == "hold"


def test_action_match_ignores_type_and_fault_but_not_never():
    e = exp({"action": "make_offer", "type": "REFUND"}, {"action": "accept_claim"})
    assert bench.action_match(e, out("make_offer", "REFUND_WITH_RETURN")) and not bench.is_match(e, out("make_offer", "REFUND_WITH_RETURN"))
    assert not bench.action_match(e, out("provide_evidence"))
    assert not bench.action_match(exp({"action": "make_offer"}, never=["make_offer"]), out("make_offer", "REFUND"))
    assert not any(bench.action_match(e, o) for o in (BLOCKED, DEADLOCK, DOWN))
    e = exp({"action": "make_offer", "type": "REFUND_WITH_RETURN"}, fault_top="agent_misread")
    o = out("make_offer", "REFUND_WITH_RETURN", fault_top="merchant_error")
    assert bench.action_match(e, o) and not bench.is_match(e, o)


def test_blocked_is_nothing_sent_and_a_model_failure_is_not_a_block():
    assert bench.blocked(BLOCKED) and bench.blocked(DEADLOCK)
    assert not bench.blocked(out()) and not bench.blocked(DOWN)


def test_cost_on_a_case_the_seller_should_defend():
    e = exp({"action": "provide_evidence"}, {"action": "make_offer", "type": "REFUND_WITH_RETURN"})
    c = lambda o: bench.cost(e, "seller", o, 140.0, FEE)  # noqa: E731
    assert c(out()) == 0 and c(out("make_offer", "REFUND_WITH_RETURN")) == 0
    assert c(out("accept_claim", "REFUND")) == 140.0 and c(out("make_offer", "REFUND")) == 140.0  # wrong payout: the money paid
    assert c(out("make_offer", "REFUND", amount=20.0)) == 20.0 and c(out("accept_claim", "PARTIAL_REFUND", amount=50.0)) == 50.0
    assert c(out("make_offer", "REFUND", amount=500.0)) == 140.0  # never more than the dispute
    assert c(out("make_offer", "REFUND_WITH_REPLACEMENT")) == 140.0
    assert c(out("send_message")) == 0 and c(out("provide_supporting_info")) == 0  # wrong, but nothing paid
    assert c(BLOCKED) is None and c(DEADLOCK) is None and c(DOWN) is None


def test_cost_on_a_case_the_buyer_should_win():
    e = exp({"action": "accept_claim"}, {"action": "make_offer", "type": "REFUND"})
    c = lambda o: bench.cost(e, "buyer", o, 38.0, FEE)  # noqa: E731
    assert c(out("accept_claim", "REFUND")) == 0 and c(out("make_offer", "REFUND")) == 0
    assert c(out("provide_evidence")) == c(out("provide_supporting_info")) == c(out("send_message")) == 53.0  # amount + fee
    assert c(out("make_offer", "REFUND_WITH_RETURN")) == 53.0  # a return the key doesn't allow is a contest
    assert c(out("make_offer", "REFUND_WITH_REPLACEMENT")) == 0 and c(out("accept_claim", "PARTIAL_REFUND", amount=10.0)) == 0
    ret_ok = exp({"action": "make_offer", "type": "REFUND_WITH_RETURN"}, {"action": "accept_claim"})
    assert bench.cost(ret_ok, "buyer", out("make_offer", "REFUND_WITH_RETURN"), 140.0, FEE) == 0
    assert c(BLOCKED) is None and c(DOWN) is None


def test_score_fills_match_action_match_and_cost():
    e = exp({"action": "accept_claim"})
    o = bench.score(out("make_offer", "REFUND_WITH_RETURN"), e, "buyer", 24.0, FEE)
    assert (o["match"], o["action_match"], o["cost"]) == (False, False, 39.0)
    o = bench.score(out("accept_claim", "REFUND"), e, None, 24.0, FEE)  # v1 cases have no side: no cost
    assert (o["match"], o["action_match"], o["cost"]) == (True, True, None)


def test_outcome_flags_model_failures_apart_from_blocks():
    down = RunResult("error", [{"step": "judge_a", "status": "error", "data": {"message": "Model unavailable (api.groq.com: HTTP 429)"}}], None, None, None, {})
    o = bench.outcome_of(down)
    assert o["unavailable"] is True and o["valid"] is False and not bench.blocked(o)
    refused = RunResult("error", [{"step": "judge_a", "status": "error", "data": {"verdict": {}, "validation": {}}}], None, {"a": {}, "b": None}, None, {})
    o = bench.outcome_of(refused)
    assert o["unavailable"] is False and bench.blocked(o)
    for why, infra in (("api.groq.com: HTTP 503", True), ("localhost: timeout", True), ("api.groq.com: network error", True),
                       ("api.groq.com: HTTP 401", True), ("api.groq.com: HTTP 404", True), ("no provider configured", True),  # a bad key or model name is not a verdict
                       ("api.groq.com: invalid reply", False), ("api.groq.com: HTTP 400", False)):  # a reply that never parses is the model's own failure
        o = bench.outcome_of(RunResult("error", [{"step": "baseline", "status": "error", "data": {"message": f"Model unavailable ({why})"}}], None, None, None, {}))
        assert o["unavailable"] is infra and bench.blocked(o) is not infra, why
    sent = RunResult("done", [], {"action": "make_offer", "type": "REFUND", "amount": 20.0, "fault": None}, {}, {}, {})
    assert bench.outcome_of(sent)["amount"] == 20.0 and bench.outcome_of(sent)["unavailable"] is False


def _entry(side, e, amount, desk):
    x = {"id": "X", "heldout": False, "side": side, "amount": amount, "desk_flip": False, "baseline_flip": False, "claims_dropped": 0}
    for k in bench.SYSTEMS:
        x[k] = bench.score(out(), e, side, amount, FEE)
    x["desk"] = bench.score(desk, e, side, amount, FEE)
    return x


def test_totals_keep_blocked_apart_and_split_the_cost():
    sell, buy = exp({"action": "provide_evidence"}), exp({"action": "accept_claim"})
    cs = [_entry("seller", sell, 140.0, out("accept_claim", "REFUND")),  # wrong payout: 140
          _entry("buyer", buy, 38.0, out("provide_evidence")),           # wrong contest: 38 + 15
          _entry("buyer", buy, 24.0, BLOCKED),                           # nothing sent: no cost, counted apart
          _entry("seller", sell, 12.0, out()),                           # right
          _entry("seller", sell, 38.0, DOWN)]                            # model failure: neither blocked nor scored
    t = bench._totals(cs, FEE)
    assert t["systems"]["desk"] == {"match": 1, "action_match": 1, "blocked": 1, "unavailable": 1,
                                    "cost_usd": 193.0, "wrong_payouts_usd": 140.0, "wrong_contests_usd": 53.0}
    assert t["systems"]["always_contest"]["cost_usd"] == 53.0 + 39.0 and set(t["systems"]) == set(bench.SYSTEMS)
    assert t["desk"] == 1 and t["n"] == 5 and t["sides"] == {"seller": 3, "buyer": 2}
    assert t["worst_usd"] == 140 + 53 + 39 + 12 + 38  # every seller case paid out, every buyer case contested


def test_always_contest_answers_a_chargeback_with_supporting_info():
    s = json.loads((config.BENCH_DIR / "snapshots" / "UNAUTH-1.json").read_text(encoding="utf-8"))
    pre = rules.precheck(facts.build_facts(s), s)
    assert bench.control_contest(pre) == ("provide_supporting_info", None)
    assert bench.control_accept(pre) == ("accept_claim", "REFUND")


HISTORY = {  # sha256 (LF line endings) of the v1 bench files: kept as they were for history
    "cases.json": "ba79a0248fbedd68",
    "results/2026-11-10-qwen3-4b-q5_k_m-run1.json": "2c34bf67c0deb2b4",
    "results/2026-11-10-qwen3-4b-q5_k_m-run2.json": "67f0d0978a63df68",
    "results/2026-11-10-qwen3-4b-q5_k_m-run3.json": "528fcbb2eadce739",
    "snapshots/AGENT-1.json": "6d69c5fc26c4018b",
    "snapshots/CNP-1.json": "c2504555738a73e1",
    "snapshots/CNP-2.json": "fd6745c35810e66f",
    "snapshots/INR-1.json": "54820d9d9c7c6054",
    "snapshots/INR-2.json": "49ac40279976837a",
    "snapshots/INR-3.json": "a46fc3d39c6122c6",
    "snapshots/SNAD-1.json": "99a2a4c3a2a64701",
    "snapshots/SNAD-2.json": "30d41c5d9884ff29",
    "snapshots/SNAD-3.json": "786749e6a1875b58",
    "snapshots/UNAUTH-1.json": "e409a3bc15ff3cd6",
    "snapshots/UNAUTH-2.json": "c38f725a39d3083f",
    "snapshots/UNAUTH-3.json": "e9750e1a0ad5de8e",
}


def test_v1_bench_files_are_unchanged():
    for rel, h in HISTORY.items():
        b = (config.BENCH_DIR / rel).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(b).hexdigest()[:16] == h, rel
