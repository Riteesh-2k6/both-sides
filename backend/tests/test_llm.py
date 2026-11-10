import dataclasses
import json

import httpx
import pytest

import fakes
from app import agents, config, llm

T = agents.BuyerTurn
GOOD = json.dumps(fakes.turn_buyer().model_dump(mode="json"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", None)
    llm.COOLDOWN.clear()
    yield
    llm.COOLDOWN.clear()


def settings(monkeypatch, **kw):
    base = dict(LLM_BASE_URL="http://primary.test/v1", LLM_MODEL="m1", LLM_API_KEY="k1", LLM_JSON_MODE="strict",
                FALLBACK_LLM_BASE_URL="", FALLBACK_LLM_MODEL="", FALLBACK_LLM_API_KEY="", FALLBACK_LLM_JSON_MODE="strict", LLM_TEMPERATURE=0.0)
    base.update(kw)
    monkeypatch.setattr(config, "S", dataclasses.replace(config.S, **base))


def serve(monkeypatch, handler):
    seen = []

    def h(req):
        seen.append((req.url.host, json.loads(req.content), dict(req.headers)))
        return handler(req, len(seen))
    monkeypatch.setattr(llm, "TRANSPORT", httpx.MockTransport(h))
    return seen


def ok(content, usage=(5, 7)):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})


# ---------- strict_schema ----------

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


@pytest.mark.parametrize("cls", [agents.Verdict, agents.BuyerTurn, agents.MerchantTurn, agents.QuarantineOut])
def test_strict_schema_shape(cls):
    sch = llm.strict_schema(cls)
    txt = json.dumps(sch)
    for banned in ("$ref", "$defs", '"default"', "maxLength", "minLength", "maxItems", "minItems", '"title"'):
        assert banned not in txt, banned
    objs = [d for d in walk(sch) if d.get("type") == "object" or "properties" in d]
    assert objs
    for o in objs:
        assert o["additionalProperties"] is False and sorted(o["required"]) == sorted(o["properties"])


def test_strict_schema_keeps_enums_and_nullables():
    sch = llm.strict_schema(agents.Verdict)
    assert sch["properties"]["action"]["enum"] == ["provide_evidence", "make_offer", "accept_claim", "send_message", "provide_supporting_info"]
    assert "anyOf" in sch["properties"]["fault"] or sch["properties"]["fault"].get("type")


# ---------- request / parsing ----------

def test_request_body_strict_and_thinking_off_for_localhost(monkeypatch):
    settings(monkeypatch, LLM_BASE_URL="http://localhost:8080/v1")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    parsed, meta = llm.chat_json(T, "SYS", "USER", step="buyer_r1")
    host, body, headers = seen[0]
    assert isinstance(parsed, T) and host == "localhost"
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}] and body["temperature"] == 0.0
    assert body["model"] == "m1" and body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "buyer_r1" and body["chat_template_kwargs"] == {"enable_thinking": False}
    assert headers["authorization"] == "Bearer k1"
    assert meta["provider"] and meta["model"] == "m1" and meta["attempts"] == 1 and (meta["prompt_tokens"], meta["completion_tokens"]) == (5, 7) and meta["ms"] >= 0


def test_no_thinking_flag_for_remote_host_and_object_mode(monkeypatch):
    settings(monkeypatch, LLM_JSON_MODE="object")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "SYS", "USER", step="s", temperature=0.5)
    body = seen[0][1]
    assert "chat_template_kwargs" not in body and body["response_format"] == {"type": "json_object"} and body["temperature"] == 0.5
    assert body["messages"][0]["content"].startswith("SYS\nReturn only JSON matching this JSON Schema:\n")
    assert json.loads(body["messages"][0]["content"].split("Schema:\n")[1]) == llm.strict_schema(T)


def test_think_block_and_code_fence_stripped(monkeypatch):
    settings(monkeypatch)
    serve(monkeypatch, lambda r, n: ok("<think>hmm {not json}</think>\n```json\n" + GOOD + "\n```"))
    parsed, _ = llm.chat_json(T, "S", "U", step="s")
    assert parsed.proposal.action == "provide_evidence"


def test_parse_failure_retries_once_same_provider(monkeypatch):
    settings(monkeypatch)
    seen = serve(monkeypatch, lambda r, n: ok("garbage") if n == 1 else ok(GOOD))
    parsed, meta = llm.chat_json(T, "S", "U", step="s")
    assert meta["attempts"] == 2 and len(seen) == 2
    last = seen[1][1]["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith("Your last reply was invalid: ") and last["content"].endswith("Reply again with valid JSON only.")


def test_two_failures_move_to_fallback_then_raise(monkeypatch):
    settings(monkeypatch, FALLBACK_LLM_BASE_URL="http://fallback.test/v1", FALLBACK_LLM_MODEL="m2", FALLBACK_LLM_API_KEY="k2")
    seen = serve(monkeypatch, lambda r, n: ok("garbage") if r.url.host == "primary.test" else ok(GOOD))
    parsed, meta = llm.chat_json(T, "S", "U", step="s")
    assert [h for h, _, _ in seen] == ["primary.test", "primary.test", "fallback.test"] and meta["attempts"] == 3 and meta["model"] == "m2"
    assert seen[2][2]["authorization"] == "Bearer k2"
    serve(monkeypatch, lambda r, n: ok("garbage"))
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat_json(T, "S", "U", step="buyer_r1")
    assert e.value.step == "buyer_r1" and e.value.last_error


def test_429_cools_provider_down(monkeypatch):
    settings(monkeypatch, FALLBACK_LLM_BASE_URL="http://fallback.test/v1", FALLBACK_LLM_MODEL="m2")
    seen = serve(monkeypatch, lambda r, n: httpx.Response(429, headers={"Retry-After": "120"}, json={}) if r.url.host == "primary.test" else ok(GOOD))
    llm.chat_json(T, "S", "U", step="s")
    assert [h for h, _, _ in seen] == ["primary.test", "fallback.test"]
    llm.chat_json(T, "S", "U", step="s")  # primary is cooling: skipped
    assert [h for h, _, _ in seen] == ["primary.test", "fallback.test", "fallback.test"]
    assert llm.COOLDOWN["http://primary.test/v1|m1"] > 0


def test_5xx_default_cooldown_and_4xx_moves_on_without_cooldown(monkeypatch):
    settings(monkeypatch, FALLBACK_LLM_BASE_URL="http://fallback.test/v1", FALLBACK_LLM_MODEL="m2")
    serve(monkeypatch, lambda r, n: httpx.Response(503, json={}) if r.url.host == "primary.test" else ok(GOOD))
    llm.chat_json(T, "S", "U", step="s")
    import time
    assert 20 < llm.COOLDOWN["http://primary.test/v1|m1"] - time.monotonic() <= 30
    llm.COOLDOWN.clear()
    serve(monkeypatch, lambda r, n: httpx.Response(400, json={}) if r.url.host == "primary.test" else ok(GOOD))
    llm.chat_json(T, "S", "U", step="s")
    assert not llm.COOLDOWN


def test_groq_json_validate_failed_retries_once_in_object_mode(monkeypatch):
    # Seen live on Groq: HTTP 400 code json_validate_failed when the model fumbles strict JSON.
    settings(monkeypatch)
    bad = httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "Failed to validate JSON."}})
    seen = serve(monkeypatch, lambda r, n: bad if n == 1 else ok(GOOD))
    parsed, meta = llm.chat_json(T, "S", "U", step="s")
    assert meta["attempts"] == 2 and len(seen) == 2
    retry = seen[1][1]
    assert retry["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in retry["messages"][0]["content"]


def test_short_429_waits_and_retries_same_provider(monkeypatch):
    # Groq free tier: 8k tokens/min per model; it says when the bucket refills.
    settings(monkeypatch)
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    limited = httpx.Response(429, headers={"x-ratelimit-reset-tokens": "12.5s"}, json={})
    seen = serve(monkeypatch, lambda r, n: limited if n == 1 else ok(GOOD))
    parsed, meta = llm.chat_json(T, "S", "U", step="s")
    assert len(seen) == 2 and seen[0][0] == seen[1][0] == "primary.test"
    assert slept and 12.5 <= slept[0] <= 14 and not llm.COOLDOWN


def test_body_hint_beats_header_and_repeated_waits_stay_bounded(monkeypatch):
    # Live case: header said 5s, body said "try again in 8.5275s"; a second 429 followed.
    settings(monkeypatch)
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    body = {"error": {"message": "Rate limit reached ... Please try again in 8.5275s. Need more tokens?"}}
    limited = httpx.Response(429, headers={"x-ratelimit-reset-tokens": "5s"}, json=body)
    seen = serve(monkeypatch, lambda r, n: limited if n <= 2 else ok(GOOD))
    llm.chat_json(T, "S", "U", step="judge_a")
    assert len(seen) == 3 and len(slept) == 2 and all(9 <= s <= 10 for s in slept)
    # a provider that keeps saying "wait 40s" gives up after the budget, then cools down
    slept.clear()
    seen = serve(monkeypatch, lambda r, n: httpx.Response(429, headers={"retry-after": "40"}, json={}))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json(T, "S", "U", step="judge_a")
    assert sum(slept) <= llm.MAX_WAIT and llm.COOLDOWN


def test_parse_reset_header():
    assert llm._reset_seconds({"x-ratelimit-reset-tokens": "37.492s"}) == pytest.approx(37.492)
    assert llm._reset_seconds({"x-ratelimit-reset-tokens": "615ms"}) == pytest.approx(0.615)
    assert llm._reset_seconds({"x-ratelimit-reset-tokens": "1m2.5s"}) == pytest.approx(62.5)
    assert llm._reset_seconds({"retry-after": "20"}) == 20
    assert llm._reset_seconds({}) is None


def test_other_400_still_moves_on_without_retry(monkeypatch):
    settings(monkeypatch)
    seen = serve(monkeypatch, lambda r, n: httpx.Response(400, json={"error": {"code": "invalid_api_key"}}))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json(T, "S", "U", step="s")
    assert len(seen) == 1


def test_timeout_moves_on_and_all_fail_raises(monkeypatch):
    settings(monkeypatch)

    def h(req):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(llm, "TRANSPORT", httpx.MockTransport(h))
    with pytest.raises(llm.LLMUnavailable) as e:
        llm.chat_json(T, "S", "U", step="judge_a")
    assert e.value.last_error == "primary.test: timeout"


def test_empty_providers_raise(monkeypatch):
    settings(monkeypatch, LLM_BASE_URL="")
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_json(T, "S", "U", step="s")


# ---------- fake hook and trace replay ----------

def test_fake_hook(monkeypatch):
    fk = fakes.fake_llm({"buyer_r1": [fakes.turn_buyer()]})
    monkeypatch.setattr(llm, "FAKE", fk)
    parsed, meta = llm.chat_json(T, "SYS", "USR", step="buyer_r1")
    assert isinstance(parsed, T) and meta["provider"] == "fake" and fk.calls == [("buyer_r1", "SYS", "USR")]


def test_trace_recorder_collects_outputs_in_call_order(monkeypatch):
    monkeypatch.setattr(llm, "FAKE", fakes.fake_llm({"a": [fakes.turn_buyer(), fakes.turn_buyer(action="make_offer", type="REFUND", amount=1.0)]}))
    rec = {}
    tok = llm.TRACE.set(rec)
    try:
        llm.chat_json(T, "S", "U", step="a")
        llm.chat_json(T, "S", "U", step="a")
    finally:
        llm.TRACE.reset(tok)
    assert [o["proposal"]["action"] for o in rec["a"]] == ["provide_evidence", "make_offer"]


def test_script_fake_replays_trace_in_order():
    trace = fakes.load("trace_inr2_offer")
    fk = llm.script_fake(trace)
    q = fk("quarantine", agents.QuarantineOut, "s", "u")
    assert isinstance(q, agents.QuarantineOut) and q.alleged[0].id == "A1"
    assert isinstance(fk("judge_a", agents.Verdict, "s", "u"), agents.Verdict)
    with pytest.raises(llm.LLMUnavailable):
        fk("judge_a", agents.Verdict, "s", "u")  # only one recorded output
    with pytest.raises(llm.LLMUnavailable):
        fk("baseline", agents.Verdict, "s", "u")


def test_two_models_on_one_host_cool_down_separately(monkeypatch):
    # Live setup: primary and fallback are both api.groq.com, different models, separate quotas.
    settings(monkeypatch, LLM_BASE_URL="http://groq.test/v1", LLM_MODEL="big",
             FALLBACK_LLM_BASE_URL="http://groq.test/v1", FALLBACK_LLM_MODEL="small", FALLBACK_LLM_API_KEY="k")
    limited = httpx.Response(429, headers={"retry-after": "120"}, json={})
    seen = serve(monkeypatch, lambda r, n: limited if json.loads(r.content)["model"] == "big" else ok(GOOD))
    parsed, meta = llm.chat_json(T, "S", "U", step="judge_a")
    assert meta["model"] == "small" and [b["model"] for _, b, _ in seen] == ["big", "small"]
    assert "http://groq.test/v1|big" in llm.COOLDOWN and "http://groq.test/v1|small" not in llm.COOLDOWN


def test_gpt_oss_models_get_low_reasoning_effort_others_do_not(monkeypatch):
    # Live: gpt-oss spent 1.2-2.1k hidden reasoning tokens per call, ~23k per hearing on an 8k/min free quota.
    settings(monkeypatch, LLM_MODEL="openai/gpt-oss-120b")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "S", "U", step="s")
    assert seen[0][1]["reasoning_effort"] == "low"
    settings(monkeypatch, LLM_MODEL="gemini-2.5-flash")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "S", "U", step="s")
    assert "reasoning_effort" not in seen[0][1]


def test_overlong_text_fields_are_truncated_not_rejected(monkeypatch):
    # Bench trial on Gemini (object mode, limits not in the schema): proposal.why ran past 300 chars twice
    # and the whole hearing failed on formatting. Truncate to the field's max_length and keep the answer.
    settings(monkeypatch)
    d = fakes.turn_buyer().model_dump(mode="json")
    d["proposal"]["why"] = "x" * 450
    seen = serve(monkeypatch, lambda r, n: ok(json.dumps(d)))
    parsed, meta = llm.chat_json(T, "S", "U", step="buyer_r1")
    assert len(parsed.proposal.why) == 300 and len(seen) == 1


def test_other_validation_errors_still_retry(monkeypatch):
    settings(monkeypatch)
    d = fakes.turn_buyer().model_dump(mode="json")
    d["proposal"]["action"] = "refund_everything"
    seen = serve(monkeypatch, lambda r, n: ok(json.dumps(d)) if n == 1 else ok(GOOD))
    llm.chat_json(T, "S", "U", step="buyer_r1")
    assert len(seen) == 2


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def test_local_model_keeps_grammar_limits_and_caps_output(monkeypatch):
    # S7: Qwen3-4B looped past 120 s on BuyerTurn; llama.cpp turns maxItems/maxLength into hard grammar limits.
    settings(monkeypatch, LLM_BASE_URL="http://localhost:8080/v1")
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "S", "U", step="buyer_r1")
    body = seen[0][1]
    sch = body["response_format"]["json_schema"]["schema"]
    assert any("maxItems" in d for d in _walk(sch)) and any("maxLength" in d for d in _walk(sch))
    assert body["max_tokens"] <= 3072 and body["repeat_penalty"] > 1  # 1536 truncated BuyerTurn JSON in the bench


def test_remote_model_schema_stays_stripped_but_output_is_capped(monkeypatch):
    settings(monkeypatch)
    seen = serve(monkeypatch, lambda r, n: ok(GOOD))
    llm.chat_json(T, "S", "U", step="buyer_r1")
    body = seen[0][1]
    assert not any("maxItems" in d or "maxLength" in d for d in _walk(body["response_format"]["json_schema"]["schema"]))
    assert body["max_tokens"] >= 2048 and "repeat_penalty" not in body
