"""chat_json: OpenAI-compatible chat calls with strict JSON schema, a provider chain, cooldowns, a fake hook and trace replay."""
import copy
import json
import math
import re
import sys
import time
from contextvars import ContextVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from . import config

FAKE = None            # Callable[[str, type, str, str], BaseModel] | None; tests and LLM_SCRIPT set it
TRANSPORT = None       # httpx transport override for tests
COOLDOWN: dict[str, float] = {}   # provider base url -> monotonic time it may be used again
COOLDOWN_WHY: dict[str, str] = {}  # provider base url -> the failure class that started the cooldown
TRACE: ContextVar = ContextVar("llm_trace", default=None)  # execute() sets a dict; chat_json appends each parsed output
LOCAL_HOSTS = ("localhost", "127.0.0.1", "host.docker.internal")


class LLMUnavailable(Exception):
    def __init__(self, step, last_error):
        super().__init__(last_error)
        self.step, self.last_error = step, last_error


_DROP = {"title", "default", "maxLength", "minLength", "maxItems", "minItems", "$defs"}


_LIMITS = {"maxLength", "minLength", "maxItems", "minItems"}


def strict_schema(cls: type[BaseModel], keep_limits: bool = False) -> dict:
    """pydantic schema with $refs inlined, every object closed with all keys required.
    Length keywords are dropped for cloud providers (not all accept them); keep_limits=True keeps them for
    llama.cpp, which turns them into hard grammar limits (S7: Qwen3-4B looped without them)."""
    drop = _DROP - _LIMITS if keep_limits else _DROP
    sch = cls.model_json_schema()
    defs = sch.get("$defs", {})

    def walk(n):
        if isinstance(n, list):
            return [walk(x) for x in n]
        if not isinstance(n, dict):
            return n
        if "$ref" in n:
            return walk(defs[n["$ref"].split("/")[-1]])
        out = {}
        for k, v in n.items():
            if k in drop:
                continue
            out[k] = {pk: walk(pv) for pk, pv in v.items()} if k == "properties" else walk(v)
        if "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out
    return walk(sch)


def _providers():
    s = config.S
    ps = []
    for base, model, key, mode in ((s.LLM_BASE_URL, s.LLM_MODEL, s.LLM_API_KEY, s.LLM_JSON_MODE),
                                   (s.FALLBACK_LLM_BASE_URL, s.FALLBACK_LLM_MODEL, s.FALLBACK_LLM_API_KEY, s.FALLBACK_LLM_JSON_MODE)):
        if base:
            ps.append({"base": base.rstrip("/"), "model": model, "key": key, "mode": mode, "host": urlparse(base).hostname or base})
    return ps


def _log(step, p, detail):
    """Full failure detail goes to the server log only; LLMUnavailable carries just host and failure class."""
    print(f"[llm] {step} {p['host']}: {detail}", file=sys.stderr)


def _clean(text: str) -> str:
    t = re.sub(r"^\s*<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S)
    return (m.group(1) if m else t).strip()


def _record(step, parsed):
    rec = TRACE.get()
    if rec is not None:
        rec.setdefault(step, []).append(parsed.model_dump(mode="json"))


SHORT_WAIT = 45.0  # longest single 429 wait done in place
MAX_WAIT = 75.0    # total 429 waiting allowed per provider per call


def _duration(text: str) -> float:
    """'37.5s', '615ms', '1m2.5s' -> seconds (0 if none)."""
    return sum(float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", text))


def _reset_seconds(headers, body: str = "") -> float | None:
    """Seconds until a rate limit lifts. Most specific first: the error body's "try again in 8.5s"
    (it accounts for this request's size), then Retry-After, then Groq's x-ratelimit-reset-tokens."""
    def ok(v):
        return v if v is not None and math.isfinite(v) and v > 0 else None
    m = re.search(r"try again in ([\dhms.]+)", body or "")
    if m and ok(_duration(m.group(1))):
        return _duration(m.group(1))
    ra = headers.get("retry-after") or headers.get("Retry-After")
    if ra:
        try:
            if ok(float(ra)):
                return float(ra)
        except ValueError:
            pass
    rt = headers.get("x-ratelimit-reset-tokens")
    return ok(_duration(rt)) if rt else None


def _validate(model_cls: type[BaseModel], text: str) -> BaseModel:
    """Validate; if the only problems are strings over max_length, cut them to the limit and validate again.
    Object-mode providers never see the length limits (strict_schema strips them), so an over-long 'why'
    must not throw away an otherwise sound answer. Content is still fact-checked after parsing."""
    try:
        return model_cls.model_validate_json(text)
    except ValidationError as e:
        err = e

    def only_length(e):
        errs = e.errors()
        return bool(errs) and all(x["type"] in ("string_too_long", "too_long") for x in errs)

    if not only_length(err):
        raise err  # keep pydantic's own message (it carries the reply for the server log)
    data = json.loads(text)
    for _ in range(4):  # a too-long list hides its items' too-long strings until it is cut, so repeat
        errs = err.errors()
        if not only_length(err):
            raise err
        for x in errs:
            *path, last = x["loc"]
            node = data
            for k in path:
                node = node[k]
            node[last] = node[last][: x["ctx"]["max_length"]]  # strings and lists alike
        try:
            return model_cls.model_validate(data)
        except ValidationError as e:
            err = e
    raise err


def _key(p: dict) -> str:
    """Cooldown key: per model, since Groq rate-limits each model separately on the same base URL."""
    return f"{p['base']}|{p['model']}"


def chat_json(model_cls: type[BaseModel], system: str, user: str, *, step: str, temperature: float | None = None) -> tuple[BaseModel, dict]:
    if FAKE is not None:
        parsed = FAKE(step, model_cls, system, user)
        _record(step, parsed)
        return parsed, {"provider": "fake", "model": "fake", "ms": 0, "prompt_tokens": 0, "completion_tokens": 0, "attempts": 1}
    temp = config.S.LLM_TEMPERATURE if temperature is None else temperature
    t0, attempts, last = time.monotonic(), 0, "no provider configured"
    with httpx.Client(transport=TRANSPORT, timeout=300) as http:  # local CPU/GPU models are slow
        for p in _providers():
            if COOLDOWN.get(_key(p), 0) > time.monotonic():
                last = f"{p['host']}: {COOLDOWN_WHY.get(_key(p), 'HTTP 429')}"
                continue
            sysmsg = system
            local = p["host"] in LOCAL_HOSTS
            if p["mode"] == "object":
                rf = {"type": "json_object"}
                sysmsg += "\nReturn only JSON matching this JSON Schema:\n" + json.dumps(strict_schema(model_cls, keep_limits=local))
            else:
                rf = {"type": "json_schema", "json_schema": {"name": step, "schema": strict_schema(model_cls, keep_limits=local), "strict": True}}
            messages = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]
            retried, waited = False, 0.0  # one content retry per provider; 429 waits share a time budget
            while True:
                body = {"model": p["model"], "messages": messages, "temperature": temp, "response_format": rf}
                body["max_tokens"] = 3072 if local else 4096  # bounds a runaway generation
                if local:
                    body["chat_template_kwargs"] = {"enable_thinking": False}
                    body["repeat_penalty"] = 1.1  # small models loop under greedy decoding
                if "gpt-oss" in p["model"]:
                    body["reasoning_effort"] = "low"  # hidden reasoning was ~60% of tokens on Groq's 8k/min free tier
                attempts += 1
                try:
                    r = http.post(p["base"] + "/chat/completions", json=body, headers={"Authorization": f"Bearer {p['key']}"})
                except httpx.HTTPError as e:
                    last = f"{p['host']}: {'timeout' if isinstance(e, httpx.TimeoutException) else 'network error'}"
                    _log(step, p, f"{type(e).__name__}: {e}")
                    break
                if r.status_code == 429:
                    reset = _reset_seconds(r.headers, r.text)
                    if reset is not None and reset <= SHORT_WAIT and waited + reset + 1 <= MAX_WAIT:
                        # Per-minute token bucket refills soon (Groq free tier): wait it out, retry same model.
                        _log(step, p, f"HTTP 429, waiting {reset:.1f}s")
                        time.sleep(reset + 1)
                        waited += reset + 1
                        continue
                if r.status_code == 429 or r.status_code >= 500:
                    wait = _reset_seconds(r.headers, r.text) or 30.0
                    COOLDOWN[_key(p)] = time.monotonic() + wait
                    COOLDOWN_WHY[_key(p)] = f"HTTP {r.status_code}"
                    last = f"{p['host']}: HTTP {r.status_code}"
                    hdrs = {k: r.headers.get(k) for k in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests") if r.headers.get(k)}
                    _log(step, p, f"HTTP {r.status_code} {hdrs} cooldown {wait:.0f}s: {r.text[:300]}")
                    break
                if r.status_code >= 400:
                    last = f"{p['host']}: HTTP {r.status_code}"
                    _log(step, p, f"HTTP {r.status_code}: {r.text[:300]}")
                    if r.status_code == 400 and "json_validate_failed" in r.text and not retried:
                        # Groq rejects a fumbled strict-schema generation; retry once in json_object
                        # mode (schema in the prompt, pydantic validates) - a different decoding path.
                        retried = True
                        rf = {"type": "json_object"}
                        messages = [{"role": "system", "content": system + "\nReturn only JSON matching this JSON Schema:\n"
                                     + json.dumps(strict_schema(model_cls))}] + messages[1:]
                        continue
                    break
                try:
                    j = r.json()
                    content = j["choices"][0]["message"]["content"]
                    parsed = _validate(model_cls, _clean(content))
                except (ValueError, KeyError, IndexError, TypeError, ValidationError) as e:
                    err = " ".join(str(e).split())[:500]
                    last = f"{p['host']}: invalid reply"
                    _log(step, p, f"invalid reply: {err}")
                    if not retried:
                        retried = True
                        messages = messages + [{"role": "user", "content": f"Your last reply was invalid: {err}. Reply again with valid JSON only."}]
                        continue
                    break
                _record(step, parsed)
                u = j.get("usage") or {}
                return parsed, {"provider": p["host"], "model": p["model"], "ms": int((time.monotonic() - t0) * 1000),
                                "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0), "attempts": attempts}
    raise LLMUnavailable(step, last)


def script_fake(trace: dict):
    """LLM_SCRIPT: a FAKE that replays a Trace's llm_outputs in order, per step."""
    outs = copy.deepcopy(trace.get("llm_outputs", trace))

    def fake(step, model_cls, system, user):
        q = outs.get(step)
        if not q:
            raise LLMUnavailable(step, f"trace has no more outputs for {step}")
        return model_cls.model_validate(q.pop(0))
    return fake
