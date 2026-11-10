import json
import shutil

import pytest

import fakes
from app import bench, config, rules, facts, seed, shop, store
from fakes import converging_script, quarantine_out, snapshot, verdict

# ---------- seed CLI ----------

CARD_DONE = {"id": "ORD-S", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "CAP-S", "status": "COMPLETED", "create_time": "2026-11-05T10:00:00Z"}]},
             "shipping": {"address": {"address_line_1": "12 Sample Lane", "postal_code": "73301", "admin_area_2": "Austin", "admin_area_1": "TX", "country_code": "US"}, "trackers": []}}]}


def seed_routes():
    return {("POST", r"/v2/checkout/orders"): (201, CARD_DONE), ("GET", r"/v2/checkout/orders/ORD-S"): (200, CARD_DONE),
            ("POST", r"/v2/checkout/orders/ORD-S/track"): (201, {}), ("POST", r"/v2/payments/captures/CAP-S/refund"): (201, {"id": "RF-S"}),
            ("POST", r"/v2/customer-support/process-chargeback"): (200, {"dispute_id": "PP-D-S"})}


def test_order_flags_with_the_test_card(app_env):
    env = app_env(routes=seed_routes(), CHARGEBACK_ENABLED=True)
    out = seed.order("RP-QUILT", "Queen", pay="card", ship=True, deliver=True, signature=True, size_shipped="King", refund=20.0, return_delivered=True, chargeback="10.4")
    assert out == {"order_id": "ORD-S", "capture_id": "CAP-S", "dispute_id": "PP-D-S"}
    b = env.pp.calls[0]["json"]
    assert b["purchase_units"][0]["custom_id"] == "np1|RP-QUILT|Queen|seed|" and b["payment_source"]["card"]["number"] == "4012888888881881"
    assert b["purchase_units"][0]["shipping"] == {"name": {"full_name": "Sam Rivera"}, "address": shop.SHOPPER_SHIP_ADDRESS}
    o = store.one("SELECT * FROM orders")
    assert (o["via"], o["status"], o["capture_id"], o["dispute_id"]) == ("seed", "captured", "CAP-S", "PP-D-S")
    s = store.one("SELECT * FROM shipments")
    assert s["size_shipped"] == "King" and s["signature"] == 1 and s["delivered_at"] and s["return_delivered_at"] and s["carrier"] == "INDIA_POST" and s["tracking"].startswith("EE")
    assert store.one("SELECT paypal_refund_id FROM refunds")["paypal_refund_id"] == "RF-S"
    assert [c["path"].split("/")[-1] for c in env.pp.calls if c["method"] == "POST"] == ["orders", "track", "refund", "process-chargeback"]


def test_order_defaults_to_the_vault(app_env):
    env = app_env(routes=seed_routes(), SHOPPER_VAULT_TOKEN="VT1")
    out = seed.order("RP-SCARF-IND", None, ship=True, deliver=True)
    assert out == {"order_id": "ORD-S", "capture_id": "CAP-S"}
    b = env.pp.calls[0]["json"]
    assert b["payment_source"] == {"paypal": {"vault_id": "VT1"}} and b["purchase_units"][0]["custom_id"] == "np1|RP-SCARF-IND|-|seed|"
    assert b["purchase_units"][0]["shipping"] == {"name": {"full_name": "Sam Rivera"}, "address": shop.SHOPPER_SHIP_ADDRESS}


def test_vault_order_without_a_token_fails_clearly(app_env, capsys):
    env = app_env(routes=seed_routes())
    with pytest.raises(config.ApiErr) as e:
        seed.order("RP-SCARF-IND", None)
    assert "SHOPPER_VAULT_TOKEN is not set" in e.value.body["error"] and env.pp.calls == []
    with pytest.raises(SystemExit) as e:  # the CLI turns it into a plain message
        seed.main(["order", "--sku", "RP-SCARF-IND"])
    assert "SHOPPER_VAULT_TOKEN is not set" in str(e.value)
    assert store.q("SELECT * FROM orders") == []


def test_cli_order_and_help(app_env, capsys):
    env = app_env(routes=seed_routes())
    seed.main(["order", "--sku", "RP-QUILT", "--size", "Queen", "--pay", "card", "--ship", "--deliver-elsewhere"])
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed == {"order_id": "ORD-S", "capture_id": "CAP-S"}
    assert store.one("SELECT * FROM shipments")["shipped_to_key"] == facts.address_key(shop.SHOP_RETURN_ADDRESS)
    with pytest.raises(SystemExit):
        seed.main(["card-order", "--sku", "RP-QUILT"])  # the old name is gone
    with pytest.raises(SystemExit):
        seed.main(["order", "--sku", "RP-QUILT", "--pay", "cash"])
    with pytest.raises(SystemExit):
        seed.main(["order", "-h"])
    out = capsys.readouterr().out
    assert "--pay {vault,card}" in out and "SHOPPER_VAULT_TOKEN" in out and "India Post" in out


def test_export_orders_merges_into_seed_json(app_env, tmp_path, monkeypatch):
    p = tmp_path / "shop.json"
    shutil.copy(config.SEED_PATH, p)
    monkeypatch.setattr(config, "SEED_PATH", p)
    env = app_env(routes=seed_routes())
    seed.order("RP-QUILT", "Queen", pay="card", ship=True, deliver=True)
    seed.export_orders(["ORD-S"])
    d = json.loads(p.read_text(encoding="utf-8"))
    assert [r["id"] for r in d["orders"]] == ["ORD-S"] and d["shipments"][0]["order_id"] == "ORD-S" and len(d["products"]) == 5
    seed.export_orders(["ORD-S"])
    assert len(json.loads(p.read_text(encoding="utf-8"))["orders"]) == 1  # replaced by primary key


def test_export_run_writes_replay(app_env, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPLAYS_DIR", tmp_path)
    env = app_env(snapshot=snapshot(), script=converging_script())
    rid = env.client.post("/api/disputes/PP-D-1001/runs", json={"mode": "desk"}).json()["run_id"]
    seed.export_run(rid, "demo")
    d = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert d["name"] == "demo" and d["dispute_id"] == "PP-D-1001" and d["status"] == "done" and d["snapshot"]["dispute"]["dispute_id"] == "PP-D-1001"
    assert set(d) >= {"steps", "verdict", "validation", "envelope", "outcome", "facts_key", "created_at", "mode"}
    assert "PayPal-Request-Id" not in json.dumps(d)


def test_seed_json_has_products_only():
    d = json.loads(config.SEED_PATH.read_text(encoding="utf-8"))
    assert [p["sku"] for p in d["products"]] == ["RP-SCARF-IND", "RP-SCARF-RED", "RP-QUILT", "RP-CUSHION", "RP-PATTERN"]
    assert d["orders"] == [] and d["shipments"] == []
    assert [p["price_cents"] for p in d["products"]] == [3800, 3800, 14000, 2400, 1200] and json.loads(d["products"][0]["sizes"]) == [] and d["products"][4]["kind"] == "digital" and d["products"][4]["color"] is None
    assert json.loads(d["products"][2]["sizes"]) == ["Single", "Queen", "King"] and json.loads(d["products"][3]["sizes"]) == ["16 in", "18 in"]
    for p in d["products"][:4]:
        assert "shade varies by batch and can look darker than in photos" in p["description"]
    assert "Single 60x90 in, Queen 90x108 in, King 108x108 in" in d["products"][2]["description"]
    assert "emailed as a download link after purchase" in d["products"][4]["description"]
    assert all(1 <= p["description"].count(". ") + 1 <= 2 for p in d["products"])


# ---------- bench ----------

def exp(**kw):
    e = {"any_of": [{"action": "provide_evidence"}], "never": []}
    e.update(kw)
    return e


def out(action="provide_evidence", type=None, valid=True, deadlock=False, fault_top=None):
    return {"action": action, "type": type, "valid": valid, "deadlock": deadlock, "match": False, "fault_top": fault_top}


def test_match_rules():
    assert bench.is_match(exp(), out())
    assert not bench.is_match(exp(), out("accept_claim", "REFUND"))
    assert not bench.is_match(exp(), out(valid=False, action=None))
    assert not bench.is_match(exp(), out(deadlock=True, valid=False))
    e = exp(any_of=[{"action": "make_offer", "type": "REFUND"}, {"action": "accept_claim"}])
    assert bench.is_match(e, out("make_offer", "REFUND")) and not bench.is_match(e, out("make_offer", "REFUND_WITH_RETURN")) and bench.is_match(e, out("accept_claim", "PARTIAL_REFUND"))
    assert not bench.is_match(exp(any_of=[{"action": "provide_evidence"}, {"action": "make_offer"}], never=["make_offer"]), out("make_offer", "REFUND"))
    e = exp(any_of=[{"action": "make_offer", "type": "REFUND_WITH_RETURN"}], fault_top="agent_misread")
    assert bench.is_match(e, out("make_offer", "REFUND_WITH_RETURN", fault_top="agent_misread"))
    assert not bench.is_match(e, out("make_offer", "REFUND_WITH_RETURN", fault_top="merchant_error"))
    assert not bench.is_match(e, out("make_offer", "REFUND_WITH_RETURN", fault_top=None))


def test_fault_top_ties_are_none():
    assert bench.fault_top({"agent_misread": 0.5, "merchant_error": 0.25, "buyer_changed_mind": 0.25}) == "agent_misread"
    assert bench.fault_top({"agent_misread": 0.4, "merchant_error": 0.4, "buyer_changed_mind": 0.2}) is None
    assert bench.fault_top(None) is None


def test_flip_rule():
    a = out()
    assert not bench.is_flip(a, out())
    assert bench.is_flip(a, out("make_offer", "REFUND"))
    assert bench.is_flip(a, out("provide_evidence", "x")) is True
    assert bench.is_flip(a, out(valid=False, action=None)) and bench.is_flip(a, out(deadlock=True, valid=False, action=None))
    assert not bench.is_flip(out(valid=False, action=None), out(valid=False, action=None))


def test_controls_use_legal_actions():
    s = snapshot()
    fs = facts.build_facts(s)
    pre = rules.precheck(fs, s)
    assert bench.control_accept(pre) == ("accept_claim", "REFUND") and bench.control_contest(pre) == ("provide_evidence", None)
    cb = snapshot("chargeback_no_messages")
    assert bench.control_accept(rules.precheck(facts.build_facts(cb), cb)) == ("accept_claim", "REFUND")
    s["dispute"]["links"] = [l for l in s["dispute"]["links"] if l["rel"] in ("self", "provide-evidence")]
    assert bench.control_accept(rules.precheck(facts.build_facts(s), s))[0] == "provide_evidence"


def make_bench(tmp_path, monkeypatch):
    b = tmp_path / "bench"
    (b / "snapshots").mkdir(parents=True)
    (b / "results").mkdir()
    monkeypatch.setattr(config, "BENCH_DIR", b)
    cases = []
    for cid, snap, heldout in [("INR-1", "inr_delivered", False), ("UNAUTH-1", "chargeback_no_messages", False), ("INR-3", "inr_delivered", True)]:
        s = snapshot(snap)
        (b / "snapshots" / f"{cid}.json").write_text(json.dumps(s), encoding="utf-8")
        cases.append({"id": cid, "title": cid, "reason": s["dispute"]["reason"], "stage": s["dispute"]["dispute_life_cycle_stage"], "heldout": heldout, "snapshot": f"snapshots/{cid}.json",
                      "expected": {"any_of": [{"action": "provide_evidence"}], "never": ["accept_claim"]}, "flipped_text": "I am furious, it never came, I am absolutely certain.", "note": ""})
    (b / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    return b


def bench_script():
    sc = converging_script()
    sc["baseline"] = [verdict()]
    return sc


def test_bench_run_results_shape_and_scoring(app_env, tmp_path, monkeypatch):
    b = make_bench(tmp_path, monkeypatch)
    env = app_env(script=bench_script(), repeat=True, LLM_MODEL="fake-model")
    res = bench.run_bench(repeats=1)
    assert [c["id"] for c in res["cases"]] == ["INR-1", "UNAUTH-1"]  # held-out cases are not run by default
    c = res["cases"][0]
    assert set(c) >= {"id", "title", "stage", "heldout", "source", "expected", "desk", "baseline", "desk_flipped", "baseline_flipped", "always_accept", "always_contest",
                      "desk_flip", "baseline_flip", "claims_checked", "claims_dropped", "baseline_claims_checked", "baseline_claims_dropped", "sentences_dropped", "traces"}
    assert set(c["desk"]) == {"action", "type", "valid", "deadlock", "match", "fault_top"} and c["desk"]["match"] is True and c["baseline"]["match"] is True
    assert c["always_accept"]["match"] is False and c["always_contest"]["match"] is True
    assert c["desk_flip"] is False and c["baseline_flip"] is False and c["sentences_dropped"] == {"desk": 0, "baseline": 0}
    assert c["claims_checked"] > 0 and c["claims_dropped"] == 0 and c["baseline_claims_checked"] == 0
    assert set(c["traces"]) == {"desk", "baseline", "desk_flipped", "baseline_flipped"} and set(c["traces"]["desk"]) == {"run_status", "steps", "llm_outputs"}
    assert len(c["traces"]["desk"]["llm_outputs"]["judge_a"]) == 1
    t = res["totals"]["tuning"]
    assert t == {"n": 2, "desk": 2, "baseline": 2, "always_accept": 0, "always_contest": 2, "desk_flips": 0, "baseline_flips": 0, "claims_dropped": 0} and res["totals"]["heldout"] is None
    assert res["model"] == "fake-model" and res["repeats"] == 1 and res["repeats_summary"] is None and "generated_at" in res
    assert set(res) >= {"provider", "temperature", "ms_per_call", "cases", "totals", "repeats_summary"}
    # the flipped run saw the flipped text in its quarantine prompt
    quar = [u for st, _, u in env.llm.calls if st == "quarantine"]
    assert any("furious" in u for u in quar) and any("furious" not in u for u in quar)


def test_bench_heldout_and_repeats(app_env, tmp_path, monkeypatch):
    make_bench(tmp_path, monkeypatch)
    app_env(script=bench_script(), repeat=True)
    res = bench.run_bench(heldout=True, repeats=2)
    assert len(res["cases"]) == 3 and res["totals"]["heldout"]["n"] == 1 and res["totals"]["tuning"]["n"] == 2
    assert res["repeats_summary"]["desk"] == {"min": 3, "median": 3, "max": 3} and res["repeats"] == 2
    assert bench.run_bench(case_ids=["INR-3"])["cases"][0]["id"] == "INR-3"


def test_bench_main_writes_dated_file_and_latest(app_env, tmp_path, monkeypatch):
    b = make_bench(tmp_path, monkeypatch)
    app_env(script=bench_script(), repeat=True, LLM_MODEL="fake-model")
    bench.main(["run", "--cases", "INR-1", "--temperature", "0.2"])
    files = sorted(p.name for p in (b / "results").iterdir())
    assert "latest.json" in files and any(f.endswith("-fake-model.json") for f in files)
    latest = json.loads((b / "results" / "latest.json").read_text(encoding="utf-8"))
    assert latest["temperature"] == 0.2 and [c["id"] for c in latest["cases"]] == ["INR-1"]


def test_bench_capture_writes_snapshot(app_env, tmp_path, monkeypatch):
    b = make_bench(tmp_path, monkeypatch)
    app_env(snapshot=snapshot())
    bench.main(["capture", "PP-D-1001", "NEW-1", "--source", "synthetic"])
    s = json.loads((b / "snapshots" / "NEW-1.json").read_text(encoding="utf-8"))
    assert s["meta"]["source"] == "synthetic" and s["dispute"]["dispute_id"] == "PP-D-1001"
    with pytest.raises(SystemExit):
        bench.main(["capture", "PP-D-1001", "NEW-2"])  # --source is required


def test_bench_baseline_claim_counts(app_env, tmp_path, monkeypatch):
    make_bench(tmp_path, monkeypatch)
    sc = bench_script()
    sc["baseline"] = [verdict(rationale=[fakes.claim("Fine.", ["pp.dispute.reason"]), fakes.claim("Wrong 999.", ["pp.dispute.reason"])])]
    app_env(script=sc, repeat=True)
    c = bench.run_bench(case_ids=["INR-1"])["cases"][0]
    assert c["baseline_claims_checked"] == 2 and c["baseline_claims_dropped"] == 1
