"""Bench CLI: python -m app.bench capture|run. Scores desk vs baseline vs two no-LLM controls on recorded snapshots."""
import argparse
import copy
import json
import statistics
from urllib.parse import urlparse

from . import agents, checks, config, facts as F, llm, pipeline, rules, shop, store


def fault_top(fault) -> str | None:
    if not fault:
        return None
    best = sorted(fault.items(), key=lambda kv: -kv[1])
    return best[0][0] if len(best) == 1 or best[0][1] > best[1][1] else None  # a tie is no answer


def outcome_of(res) -> dict:
    ok = res.status == "done"
    v = res.verdict or {}
    return {"action": v.get("action") if ok else None, "type": v.get("type") if ok else None, "valid": ok, "deadlock": res.status == "deadlock",
            "match": False, "fault_top": fault_top(v.get("fault")) if ok else None}


def is_match(expected: dict, out: dict) -> bool:
    if not out["valid"] or out["deadlock"]:
        return False
    if not any(e["action"] == out["action"] and ("type" not in e or e["type"] == out["type"]) for e in expected.get("any_of", [])):
        return False
    if out["action"] in expected.get("never", []):
        return False
    return not expected.get("fault_top") or out["fault_top"] == expected["fault_top"]


def is_flip(a: dict, b: dict) -> bool:
    ok = lambda o: o["valid"] and not o["deadlock"]  # noqa: E731
    if ok(a) != ok(b):
        return True
    return ok(a) and (a["action"], a["type"]) != (b["action"], b["type"])


def control_accept(pre) -> tuple[str, str | None]:
    return ("accept_claim", "REFUND") if "accept_claim" in pre.legal_actions and "REFUND" in pre.accept_types else ("provide_evidence", None)


def control_contest(pre) -> tuple[str, str | None]:
    return "provide_evidence", None


def _control(fn, pre, expected) -> dict:
    a, t = fn(pre)
    o = {"action": a, "type": t, "valid": True, "deadlock": False, "match": False, "fault_top": None}
    o["match"] = is_match(expected, o)
    return o


def _steps(res):
    return {s["step"]: s for s in res.steps}


def _case(case: dict, temperature) -> tuple[dict, list[int]]:
    snap = json.loads((config.BENCH_DIR / case["snapshot"]).read_text(encoding="utf-8"))
    flipped = copy.deepcopy(snap)
    flipped["buyer_text"] = case["flipped_text"]
    mr = 1 if config.S.HOSTED else 2
    runs = {"desk": pipeline.execute(snap, "desk", max_rounds=mr, temperature=temperature),
            "baseline": pipeline.execute(snap, "baseline", max_rounds=mr, temperature=temperature),
            "desk_flipped": pipeline.execute(flipped, "desk", max_rounds=mr, temperature=temperature),
            "baseline_flipped": pipeline.execute(flipped, "baseline", max_rounds=mr, temperature=temperature)}
    exp = case["expected"]
    outs = {k: outcome_of(r) for k, r in runs.items()}
    for o in outs.values():
        o["match"] = is_match(exp, o)
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
             **outs, "always_accept": _control(control_accept, pre, exp), "always_contest": _control(control_contest, pre, exp),
             "desk_flip": is_flip(outs["desk"], outs["desk_flipped"]), "baseline_flip": is_flip(outs["baseline"], outs["baseline_flipped"]),
             "claims_checked": checked, "claims_dropped": dropped, "baseline_claims_checked": len(rat), "baseline_claims_dropped": base_dropped,
             "sentences_dropped": {"desk": sd(runs["desk"], "judge_a"), "baseline": sd(runs["baseline"], "baseline")},
             "traces": {k: {"run_status": r.status, "steps": r.steps, "llm_outputs": r.llm_outputs} for k, r in runs.items()}}
    return entry, ms


def _totals(cs: list[dict]) -> dict | None:
    if not cs:
        return None
    return {"n": len(cs), "desk": sum(c["desk"]["match"] for c in cs), "baseline": sum(c["baseline"]["match"] for c in cs),
            "always_accept": sum(c["always_accept"]["match"] for c in cs), "always_contest": sum(c["always_contest"]["match"] for c in cs),
            "desk_flips": sum(c["desk_flip"] for c in cs), "baseline_flips": sum(c["baseline_flip"] for c in cs), "claims_dropped": sum(c["claims_dropped"] for c in cs)}


def run_bench(case_ids=None, heldout=False, repeats=1, temperature=None) -> dict:
    allc = json.loads((config.BENCH_DIR / "cases.json").read_text(encoding="utf-8"))
    allc = allc["cases"] if isinstance(allc, dict) else allc  # the committed file wraps the list with _written/_note header fields
    chosen = [c for c in allc if c["id"] in case_ids] if case_ids else [c for c in allc if heldout or not c["heldout"]]
    passes, ms = [], []
    for _ in range(repeats):
        cs = []
        for c in chosen:
            e, m = _case(c, temperature)
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
            "cases": first, "totals": {"tuning": _totals([c for c in first if not c["heldout"]]), "heldout": _totals([c for c in first if c["heldout"]])},
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
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        return capture(a.dispute_id, a.case_id, a.source)
    res = run_bench(a.cases.split(",") if a.cases else None, a.heldout, a.repeats, a.temperature)
    out = config.BENCH_DIR / "results"
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(res, indent=1, ensure_ascii=False) + "\n"
    (out / f"{res['generated_at'][:10]}-{config.S.LLM_MODEL.replace('/', '_')}.json").write_text(text, encoding="utf-8")
    (out / "latest.json").write_text(text, encoding="utf-8")
    t = res["totals"]
    print(f"tuning: {t['tuning']}")
    if t["heldout"]:
        print(f"heldout: {t['heldout']}")


if __name__ == "__main__":
    main()
