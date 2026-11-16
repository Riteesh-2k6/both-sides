"""Bench CLI: python -m app.bench capture|run. Scores desk vs baseline vs two no-LLM controls on recorded snapshots.
v2 (bench/cases-v2.json, the default) adds each case's side and a cost of mistakes in dollars; see the README's Bench section."""
import argparse
import collections
import copy
import json
import statistics
from urllib.parse import urlparse

from . import agents, checks, config, facts as F, llm, pipeline, rules, shop, store

SYSTEMS = ("desk", "baseline", "always_accept", "always_contest", "desk_flipped", "baseline_flipped")


def fault_top(fault) -> str | None:
    if not fault:
        return None
    best = sorted(fault.items(), key=lambda kv: -kv[1])
    return best[0][0] if len(best) == 1 or best[0][1] > best[1][1] else None  # a tie is no answer


def _unavailable(res) -> bool:
    return any(s["status"] == "error" and str((s.get("data") or {}).get("message", "")).startswith("Model unavailable") for s in res.steps)


def outcome_of(res) -> dict:
    ok = res.status == "done"
    v = res.verdict or {}
    return {"action": v.get("action") if ok else None, "type": v.get("type") if ok else None, "amount": v.get("amount") if ok else None,
            "valid": ok, "deadlock": res.status == "deadlock", "unavailable": _unavailable(res), "match": False, "fault_top": fault_top(v.get("fault")) if ok else None}


def blocked(out: dict) -> bool:
    """Nothing sent: the validator refused the verdict, or the two readings deadlocked and the seller decides. A model failure is not a block."""
    return (not out["valid"] or out["deadlock"]) and not out.get("unavailable")


def direction(action: str, type: str | None) -> str:
    """pay: the buyer gets money now; return: money only once the item comes back; hold: nothing paid (contest, notes, message)."""
    if action == "accept_claim":
        return "pay"
    if action == "make_offer":
        return "return" if type == "REFUND_WITH_RETURN" else "pay"
    return "hold"


def action_match(expected: dict, out: dict) -> bool:
    if not out["valid"] or out["deadlock"]:
        return False
    return any(e["action"] == out["action"] for e in expected.get("any_of", [])) and out["action"] not in expected.get("never", [])


def _key_allows(expected: dict, out: dict) -> bool:
    """The action and type are among the expected answers (fault aside)."""
    return any(e["action"] == out["action"] and ("type" not in e or e["type"] == out["type"]) for e in expected.get("any_of", [])) \
        and out["action"] not in expected.get("never", [])


def cost(expected: dict, side: str | None, out: dict, amount: float, fee: float) -> float | None:
    """Dollars a mistake costs the seller. None when nothing was sent, or the case has no side (v1 cases).
    Seller should defend: a wrong payout costs what it pays. Buyer should win: anything that doesn't pay costs the amount plus the fee."""
    if side is None or not out["valid"] or out["deadlock"]:
        return None
    if _key_allows(expected, out):
        return 0.0
    d = direction(out["action"], out["type"])
    if side == "seller":
        return min(out.get("amount") or amount, amount) if d == "pay" else 0.0
    return 0.0 if d == "pay" else amount + fee  # ponytail: a partial payout counts as paying; score its shortfall if one ever shows up


def is_match(expected: dict, out: dict) -> bool:
    """The full match: action, type where the case names one, and fault where the case names one."""
    if not out["valid"] or out["deadlock"] or not _key_allows(expected, out):
        return False
    return not expected.get("fault_top") or out["fault_top"] == expected["fault_top"]


def score(out: dict, expected: dict, side: str | None, amount: float, fee: float) -> dict:
    out["match"], out["action_match"], out["cost"] = is_match(expected, out), action_match(expected, out), cost(expected, side, out, amount, fee)
    return out


def is_flip(a: dict, b: dict) -> bool:
    ok = lambda o: o["valid"] and not o["deadlock"]  # noqa: E731
    if ok(a) != ok(b):
        return True
    return ok(a) and (a["action"], a["type"]) != (b["action"], b["type"])


def control_accept(pre) -> tuple[str, str | None]:
    return ("accept_claim", "REFUND") if "accept_claim" in pre.legal_actions and "REFUND" in pre.accept_types else control_contest(pre)


def control_contest(pre) -> tuple[str, str | None]:
    """The contest move PayPal allows: evidence, or supporting notes while PayPal reviews a chargeback (v1 always sent evidence)."""
    return ("provide_supporting_info", None) if "provide_evidence" not in pre.legal_actions and "provide_supporting_info" in pre.legal_actions else ("provide_evidence", None)


def _control(fn, pre, expected, side, amount, fee) -> dict:
    a, t = fn(pre)
    o = {"action": a, "type": t, "amount": None, "valid": True, "deadlock": False, "unavailable": False, "match": False, "fault_top": None}
    return score(o, expected, side, amount, fee)


def _steps(res):
    return {s["step"]: s for s in res.steps}


def _case(case: dict, temperature, fee: float = 0.0) -> tuple[dict, list[int]]:
    snap = json.loads((config.BENCH_DIR / case["snapshot"]).read_text(encoding="utf-8"))
    flipped = copy.deepcopy(snap)
    flipped["buyer_text"] = case["flipped_text"]
    mr = 1 if config.S.HOSTED else 2
    runs = {"desk": pipeline.execute(snap, "desk", max_rounds=mr, temperature=temperature),
            "baseline": pipeline.execute(snap, "baseline", max_rounds=mr, temperature=temperature),
            "desk_flipped": pipeline.execute(flipped, "desk", max_rounds=mr, temperature=temperature),
            "baseline_flipped": pipeline.execute(flipped, "baseline", max_rounds=mr, temperature=temperature)}
    exp, side = case["expected"], case.get("side")
    amount = float(((snap.get("dispute") or {}).get("dispute_amount") or {}).get("value") or 0)
    outs = {k: score(outcome_of(r), exp, side, amount, fee) for k, r in runs.items()}
    fs = F.build_facts(snap)
    pre = rules.precheck(fs, snap)
    st = _steps(runs["desk"])
    vsteps = [st[s]["data"] for s in ("verify_r1", "verify_r2") if s in st and st[s]["status"] != "skipped"]
    checked = sum(len(d["kept"]) + len(d["dropped"]) for d in vsteps)
    dropped = sum(len(d["dropped"]) for d in vsteps)
    rat = [agents.Claim.model_validate(c) for c in ((runs["baseline"].verdict or {}).get("rationale") or [])]
    base_dropped = len(checks.verify_claims(rat, fs)[1])

    def sd(r, step):
        v = ((_steps(r).get(step) or {}).get("data") or {}).get("validation") or {}
        return len(v.get("dropped_sentences", []))
    ms = [s["llm"]["ms"] for r in runs.values() for s in r.steps if s.get("llm")]
    entry = {"id": case["id"], "title": case["title"], "stage": case["stage"], "heldout": case["heldout"], "source": snap.get("meta", {}).get("source"), "expected": exp,
             "side": side, "amount": amount,
             **outs, "always_accept": _control(control_accept, pre, exp, side, amount, fee), "always_contest": _control(control_contest, pre, exp, side, amount, fee),
             "desk_flip": is_flip(outs["desk"], outs["desk_flipped"]), "baseline_flip": is_flip(outs["baseline"], outs["baseline_flipped"]),
             "claims_checked": checked, "claims_dropped": dropped, "baseline_claims_checked": len(rat), "baseline_claims_dropped": base_dropped,
             "sentences_dropped": {"desk": sd(runs["desk"], "judge_a"), "baseline": sd(runs["baseline"], "baseline")},
             "traces": {k: {"run_status": r.status, "steps": r.steps, "llm_outputs": r.llm_outputs} for k, r in runs.items()}}
    return entry, ms


def _system(cs: list[dict], k: str) -> dict:
    side_cost = lambda s: round(sum(c[k]["cost"] or 0 for c in cs if c.get("side") == s), 2)  # noqa: E731
    return {"match": sum(c[k]["match"] for c in cs), "action_match": sum(c[k]["action_match"] for c in cs), "blocked": sum(blocked(c[k]) for c in cs),
            "unavailable": sum(bool(c[k].get("unavailable")) for c in cs), "cost_usd": round(side_cost("seller") + side_cost("buyer"), 2),
            "wrong_payouts_usd": side_cost("seller"), "wrong_contests_usd": side_cost("buyer")}


def _totals(cs: list[dict], fee: float = 0.0) -> dict | None:
    if not cs:
        return None
    return {"n": len(cs), "desk": sum(c["desk"]["match"] for c in cs), "baseline": sum(c["baseline"]["match"] for c in cs),
            "always_accept": sum(c["always_accept"]["match"] for c in cs), "always_contest": sum(c["always_contest"]["match"] for c in cs),
            "desk_flips": sum(c["desk_flip"] for c in cs), "baseline_flips": sum(c["baseline_flip"] for c in cs), "claims_dropped": sum(c["claims_dropped"] for c in cs),
            "sides": {s: sum(c.get("side") == s for c in cs) for s in ("seller", "buyer")},
            "worst_usd": round(sum(c["amount"] + (fee if c["side"] == "buyer" else 0) for c in cs if c.get("side")), 2),
            "systems": {k: _system(cs, k) for k in SYSTEMS}}


def _models(cs: list[dict]) -> dict:
    """Which model answered each call: a fallback model mid-run would mix two models in one result."""
    return dict(collections.Counter(f"{s['llm']['provider']}/{s['llm']['model']}" for c in cs for t in c["traces"].values() for s in t["steps"] if s.get("llm")))


def run_bench(case_ids=None, heldout=False, repeats=1, temperature=None, file="cases-v2.json") -> dict:
    doc = json.loads((config.BENCH_DIR / file).read_text(encoding="utf-8"))
    allc = doc["cases"] if isinstance(doc, dict) else doc  # the committed files wrap the list with header fields
    fee = float(doc.get("dispute_fee_usd", 0.0)) if isinstance(doc, dict) else 0.0
    chosen = [c for c in allc if c["id"] in case_ids] if case_ids else [c for c in allc if heldout or not c["heldout"]]
    passes, ms = [], []
    for _ in range(repeats):
        cs = []
        for c in chosen:
            e, m = _case(c, temperature, fee)
            cs.append(e)
            ms += m
        passes.append(cs)
    first = passes[0]

    def summ(k):
        p = [sum(c[k]["match"] for c in cs) for cs in passes]
        return {"min": min(p), "median": statistics.median(p), "max": max(p)}
    s = config.S
    return {"generated_at": config.iso(config.utcnow()), "provider": "fake" if llm.FAKE else (urlparse(s.LLM_BASE_URL).hostname or s.LLM_BASE_URL), "model": s.LLM_MODEL,
            "temperature": s.LLM_TEMPERATURE if temperature is None else temperature, "repeats": repeats, "ms_per_call": round(sum(ms) / len(ms), 1) if ms else 0.0,
            "case_file": file, "dispute_fee_usd": fee, "models_used": _models([c for cs in passes for c in cs]),
            "cases": first, "totals": {"tuning": _totals([c for c in first if not c["heldout"]], fee), "heldout": _totals([c for c in first if c["heldout"]], fee)},
            "repeats_summary": {"desk": summ("desk"), "baseline": summ("baseline")} if repeats > 1 else None}


def capture(dispute_id: str, case_id: str, source: str) -> None:
    store.init_default()
    if not store.one("SELECT sku FROM products LIMIT 1"):
        shop.load_seed()
    snap = F.fetch_snapshot(dispute_id)
    snap["meta"]["source"] = source
    snap["receipts"] = {}  # bench facts say "recorded snapshot"
    p = config.BENCH_DIR / "snapshots" / f"{case_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("dispute_id")
    c.add_argument("case_id")
    c.add_argument("--source", required=True, choices=["sandbox-ui", "chargeback", "synthetic"])
    r = sub.add_parser("run")
    r.add_argument("--cases", default=None)
    r.add_argument("--heldout", action="store_true")
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--temperature", type=float, default=None)
    r.add_argument("--file", default="cases-v2.json", help="case file in bench/ (cases.json is the v1 set)")
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        return capture(a.dispute_id, a.case_id, a.source)
    res = run_bench(a.cases.split(",") if a.cases else None, a.heldout, a.repeats, a.temperature, a.file)
    out = config.BENCH_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(res, indent=1, ensure_ascii=False) + "\n"
    (out / f"{res['generated_at'][:10]}-{config.S.LLM_MODEL.replace('/', '_')}.json").write_text(text, encoding="utf-8")
    (out / "latest.json").write_text(text, encoding="utf-8")
    for name in ("tuning", "heldout"):
        t = res["totals"][name]
        if not t:
            continue
        print(f"{name}: n={t['n']} sides={t['sides']} worst=${t['worst_usd']}")
        for k, v in t["systems"].items():
            print(f"  {k:17} match {v['match']:>2}  action {v['action_match']:>2}  blocked {v['blocked']:>2}  cost ${v['cost_usd']:>8.2f}"
                  + (f"  MODEL FAILURES {v['unavailable']}" if v["unavailable"] else ""))
    if len(res["models_used"]) > 1:
        print(f"WARNING: more than one model answered: {res['models_used']}")


if __name__ == "__main__":
    main()
