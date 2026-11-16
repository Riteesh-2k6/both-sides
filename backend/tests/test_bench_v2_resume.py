"""--resume: a run on a quota-limited model (Groq free tier: tokens per day) saves each finished case, stops at the
first case the model couldn't answer, and picks up from there on the next run."""
import json

import pytest

from app import bench, llm
from test_seed_bench import bench_script, make_bench


def test_resume_after_the_model_runs_out_of_quota(app_env, tmp_path, monkeypatch, capsys):
    b = make_bench(tmp_path, monkeypatch)  # INR-1, UNAUTH-1 (tuning), INR-3 (held out)
    app_env(script=bench_script(), repeat=True, LLM_MODEL="org/m1")
    inner, budget = llm.FAKE, {"n": 15}  # enough for the first case, not the second

    def quota(step, cls, system, user):
        if budget["n"] <= 0:
            raise llm.LLMUnavailable(step, "api.groq.com: HTTP 429")
        budget["n"] -= 1
        return inner(step, cls, system, user)
    monkeypatch.setattr(llm, "FAKE", quota)

    with pytest.raises(SystemExit) as e:
        bench.main(["run", "--heldout", "--resume"])
    assert e.value.code == 3 and "--resume" in capsys.readouterr().out
    prog = b / "results" / "in-progress-org_m1.json"
    p = json.loads(prog.read_text(encoding="utf-8"))
    assert [c["id"] for c in p["cases"]] == ["INR-1"] and (p["case_file"], p["model"]) == ("cases-v2.json", "org/m1")
    assert not (b / "results" / "latest.json").exists()  # nothing final until every case is in

    p["cases"][0]["title"] = "kept from the first session"
    prog.write_text(json.dumps(p), encoding="utf-8")
    budget["n"] = 10**6
    bench.main(["run", "--heldout", "--resume"])
    res = json.loads((b / "results" / "latest.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in res["cases"]] == ["INR-1", "UNAUTH-1", "INR-3"] and res["cases"][0]["title"] == "kept from the first session"
    assert not prog.exists() and all(v["unavailable"] == 0 for t in res["totals"].values() for v in t["systems"].values())
    assert res["ms_per_call"] == 0.0 and list(res["models_used"]) == ["fake/fake"]


def test_max_wait_lets_the_bench_wait_out_long_rate_limits(app_env, tmp_path, monkeypatch):
    make_bench(tmp_path, monkeypatch)
    app_env(script=bench_script(), repeat=True)
    monkeypatch.setattr(llm, "SHORT_WAIT", 45.0)
    monkeypatch.setattr(llm, "MAX_WAIT", 75.0)
    bench.main(["run", "--cases", "INR-1"])
    assert (llm.SHORT_WAIT, llm.MAX_WAIT) == (45.0, 75.0)  # the app's own limits by default
    bench.main(["run", "--cases", "INR-1", "--max-wait", "7200"])
    assert (llm.SHORT_WAIT, llm.MAX_WAIT) == (7200.0, 7200.0)


def test_progress_from_another_case_file_or_temperature_is_ignored(app_env, tmp_path, monkeypatch):
    b = make_bench(tmp_path, monkeypatch)
    app_env(script=bench_script(), repeat=True, LLM_MODEL="m1")
    stale = {"case_file": "cases.json", "model": "m1", "temperature": 0.0, "cases": [{"id": "INR-1", "title": "stale"}]}
    (b / "results" / "in-progress-m1.json").write_text(json.dumps(stale), encoding="utf-8")
    bench.main(["run", "--resume"])
    res = json.loads((b / "results" / "latest.json").read_text(encoding="utf-8"))
    assert res["cases"][0]["title"] == "INR-1"
    with pytest.raises(SystemExit):
        bench.main(["run", "--resume", "--repeats", "2"])  # one pass at a time when resuming
