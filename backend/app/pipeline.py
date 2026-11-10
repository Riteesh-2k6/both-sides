"""execute (pure hearing), run_live, the settlement envelope reservation, send_action, approve and per-dispute locks."""
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable, Literal

from . import agents, checks, config, facts as F, llm, paypal, rules, store

EXECUTOR = ThreadPoolExecutor(max_workers=2)
DISPUTE_LOCKS: dict[str, threading.Lock] = {}
SENT_STATES = ("pending", "sent")


ApiErr = config.ApiErr


def dispute_lock(dispute_id: str) -> threading.Lock:
    with store.LOCK:
        return DISPUTE_LOCKS.setdefault(dispute_id, threading.Lock())


@dataclass
class RunResult:
    status: str
    steps: list[dict]
    verdict: dict | None
    validation: dict | None
    outcome: dict | None
    llm_outputs: dict


def facts_key(facts: dict, pre) -> str:
    g = lambda i: facts[i].value if i in facts else ""  # noqa: E731
    return f"{g('pp.dispute.status')}|{g('pp.dispute.stage')}|{','.join(sorted(pre.legal_actions))}"


# ---------------- hearing ----------------

def _vdump(v) -> dict:
    return v.model_dump(mode="json")


def execute(snapshot: dict, mode: Literal["desk", "baseline"], *, max_rounds: int, temperature: float | None = None,
            on_step: Callable[[dict], None] = lambda s: None) -> RunResult:
    steps: list[dict] = []
    trace: dict = {}
    tok = llm.TRACE.set(trace)
    t_last = [time.monotonic()]

    def emit(step, status="ok", data=None, meta=None):
        now = time.monotonic()
        s = {"step": step, "status": status, "ms": (meta or {}).get("ms", int((now - t_last[0]) * 1000)) if status != "skipped" else 0,
             "data": data if data is not None else {}, "llm": meta}
        t_last[0] = now
        steps.append(s)
        on_step(s)

    def done(status, verdict=None, validation=None, outcome=None):
        return RunResult(status, steps, verdict, validation, outcome, trace)

    cur = {"step": "facts"}
    try:
        fs = F.build_facts(snapshot)
        emit("facts", data={"count": len(fs), "errors": snapshot.get("errors", [])})
        pre = rules.precheck(fs, snapshot)
        emit("precheck", data=asdict(pre))
        cur["step"] = "quarantine"
        q, meta = agents.run_quarantine(fs["pp.dispute.reason"].value if "pp.dispute.reason" in fs else "", snapshot.get("buyer_text", ""), temperature)
        emit("quarantine", data=q.model_dump(mode="json"), meta=meta)

        def judge(step, first, kw):
            cur["step"] = step
            v, m = agents.run_judge(fs, pre, q, *kw, step=step, buyer_first=first, temperature=temperature)
            val = checks.validate_verdict(v, pre, fs, snapshot)
            if not val.ok:
                v, m2 = agents.run_judge(fs, pre, q, *kw, step=step, buyer_first=first, rejected=val.errors, temperature=temperature)
                val = checks.validate_verdict(v, pre, fs, snapshot)
                m2["attempts"] += m["attempts"]
                m = m2
            emit(step, "ok" if val.ok else "error", {"verdict": _vdump(v), "validation": asdict(val)}, m)
            return v, val

        if mode == "baseline":
            cur["step"] = "baseline"
            v, m = agents.run_baseline(fs, pre, q, temperature=temperature)
            val = checks.validate_verdict(v, pre, fs, snapshot)
            if not val.ok:
                v, m2 = agents.run_baseline(fs, pre, q, rejected=val.errors, temperature=temperature)
                val = checks.validate_verdict(v, pre, fs, snapshot)
                m2["attempts"] += m["attempts"]
                m = m2
            emit("baseline", "ok" if val.ok else "error", {"verdict": _vdump(v), "validation": asdict(val)}, m)
            emit("validate", data=asdict(val))
            if val.ok:
                return done("done", _vdump(v), asdict(val))
            return done("error", None, {"a": asdict(val), "b": None})

        def verify(*groups):
            kept, dropped = [], []
            for g in groups:
                k, d = checks.verify_claims(g, fs)
                kept.append(k)
                dropped += d
            return kept, {"kept": [c.id for k in kept for c in k], "dropped": [{"id": c.id, "reason": r} for c, r in dropped]}

        cur["step"] = "buyer_r1"
        b1, m = agents.run_buyer(fs, pre, q, temperature=temperature)
        emit("buyer_r1", data=_vdump(b1), meta=m)
        (kb,), _ = verify(b1.why_filed + b1.claims)
        cur["step"] = "merchant_r1"
        m1, m = agents.run_merchant(fs, pre, q, buyer_claims=kb, buyer_proposal=b1.proposal, temperature=temperature)
        emit("merchant_r1", data=_vdump(m1), meta=m)
        (kb, km), vdata = verify(b1.why_filed + b1.claims, m1.claims)
        emit("verify_r1", data=vdata)
        buyer_all, merch_all = list(kb), list(km)
        buyer_deciding, merch_deciding = b1.deciding_fact, m1.deciding_fact
        if max_rounds >= 2 and b1.proposal.action != m1.proposal.action:
            cur["step"] = "buyer_r2"
            b2, m = agents.run_buyer(fs, pre, q, round=2, merchant_claims=km, merchant_proposal=m1.proposal, temperature=temperature)
            emit("buyer_r2", data=_vdump(b2), meta=m)
            (kb2,), _ = verify(b2.claims)
            cur["step"] = "merchant_r2"
            m2, m = agents.run_merchant(fs, pre, q, buyer_claims=kb + kb2, buyer_proposal=b2.proposal, round=2, temperature=temperature)
            emit("merchant_r2", data=_vdump(m2), meta=m)
            (kb2, km2), vdata = verify(b2.claims, m2.claims)
            emit("verify_r2", data=vdata)
            buyer_all += kb2
            merch_all += km2
            buyer_deciding, merch_deciding = b2.deciding_fact or buyer_deciding, m2.deciding_fact or merch_deciding
        else:
            for s in ("buyer_r2", "merchant_r2", "verify_r2"):
                emit(s, "skipped")
        a, va = judge("judge_a", True, (buyer_all, merch_all))
        b, vb = judge("judge_b", False, (buyer_all, merch_all))
        if not (va.ok and vb.ok):
            emit("validate", "error", {"a": asdict(va), "b": asdict(vb)})
            return done("error", None, {"a": asdict(va), "b": asdict(vb)})
        if a.action != b.action:
            qs = []
            for raw in (buyer_deciding, merch_deciding, a.needs_fact, b.needs_fact):
                s = agents.clean_untrusted(raw, 300) if raw else ""
                if s and s not in qs:
                    qs.append(s)
            outcome = {"agreed": False, "differences": [], "second": None, "deadlock": {"questions": qs[:3], "a_action": a.action, "b_action": b.action}}
            emit("validate", data={"a": asdict(va), "b": asdict(vb)})
            return done("deadlock", None, None, outcome)
        diffs = []
        # Agreeing on the action is a verdict. Open questions are notes: the seller sees them, and they block automatic sending.
        for which, nf in (("first", a.needs_fact), ("second", b.needs_fact)):
            if nf := agents.clean_untrusted(nf or "", 300).strip():
                diffs.append(f"{which} reading asked: {nf}")
        if a.type != b.type:
            diffs.append(f"type: first reading {a.type}, second reading {b.type}")
        if (a.amount or 0) != (b.amount or 0) and abs((a.amount or 0) - (b.amount or 0)) > 0.01:
            diffs.append(f"amount: first reading {a.amount}, second reading {b.amount}")
        outcome = {"agreed": True, "differences": diffs, "second": {"action": b.action, "type": b.type, "amount": b.amount} if diffs else None, "deadlock": None}
        emit("validate", data=asdict(va))
        return done("done", _vdump(a), asdict(va), outcome)
    except llm.LLMUnavailable as e:
        emit(e.step or cur["step"], "error", {"message": f"Model unavailable ({e.last_error})"})
        return done("error")
    finally:
        llm.TRACE.reset(tok)


# ---------------- envelope ----------------

def current_envelope():
    """(Envelope | None, hash | None, source | None, row | None). Hosted: HOSTED_ENVELOPE, read-only. Local: the active row."""
    if config.S.HOSTED:
        if not config.S.HOSTED_ENVELOPE:
            return None, None, None, None
        try:
            e = checks.Envelope.model_validate_json(config.S.HOSTED_ENVELOPE)
        except ValueError:
            return None, None, None, None  # boot records the parse error
        h = checks.envelope_hash(e)
        return e, h, "hosted", {"id": 0, "body": e.model_dump_json(), "hash": h}
    row = store.one("SELECT * FROM envelopes WHERE active=1 ORDER BY id DESC LIMIT 1")
    if not row:
        return None, None, None, None
    return checks.Envelope.model_validate_json(row["body"]), row["hash"], "local", row


def save_envelope(e: checks.Envelope) -> str:
    h = checks.envelope_hash(e)
    with store.LOCK:
        store.run("UPDATE envelopes SET active=0")
        store.run("INSERT INTO envelopes(body,hash,active,created_at) VALUES(?,?,1,?)", (e.model_dump_json(), h, config.iso(config.utcnow())))
    return h


def reserve_envelope(envelope_row, v, pre, outcome, snapshot, run_id, dispute_id, now, injection_suspected: bool = False) -> tuple[bool, str, int]:
    """Check the envelope and insert the envelope_log row in one store.LOCK section, so concurrent checks cannot both spend the same cap."""
    e = checks.Envelope.model_validate_json(envelope_row["body"])
    d = snapshot.get("dispute") or {}
    dispute_cents = round(float((d.get("dispute_amount") or {}).get("value", 0)) * 100)
    cents = round(v.amount * 100) if v.action == "make_offer" and v.amount is not None else 0
    with store.LOCK:
        spent = store.one("SELECT COALESCE(SUM(amount_cents),0) AS s FROM envelope_log WHERE ok=1 AND status IN ('reserved','sent','unknown') AND substr(created_at,1,10)=?",
                          (config.iso(now)[:10],))["s"]
        prior = store.one("SELECT id FROM actions WHERE dispute_id=? AND approved_by='envelope'", (dispute_id,)) is not None
        ok, why = checks.check_envelope(e, envelope_row["hash"], v, pre, d.get("reason"), dispute_cents, spent, now, prior, outcome, snapshot.get("errors", []), injection_suspected)
        log_id = store.run("INSERT INTO envelope_log(envelope_id,dispute_id,run_id,action,amount_cents,ok,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (envelope_row["id"], dispute_id, run_id, v.action, cents, int(ok), why, "reserved" if ok else "rejected", config.iso(now)))
    return ok, why, log_id


def _counts(snapshot: dict) -> dict:
    d = snapshot.get("dispute") or {}
    off = ((d.get("offer") or {}).get("seller_offered_amount") or {}).get("value")
    return {"status": d.get("status"), "seller_messages": sum(1 for m in d.get("messages") or [] if m.get("posted_by") == "SELLER"),
            "seller_evidences": sum(1 for e in d.get("evidences") or [] if e.get("source") == "SUBMITTED_BY_SELLER"),
            "seller_supporting": sum(1 for e in d.get("supporting_info") or [] if e.get("source") == "SUBMITTED_BY_SELLER"), "seller_offered": off}


def send_action(dispute_id, run_id, verdict, snapshot, facts, message_text, approved_by, edited, edited_text) -> dict:
    suffix, kind, body = paypal.build_action_request(verdict, snapshot, facts, message_text)
    path = f"/v1/customer/disputes/{dispute_id}/{suffix}"
    aid = store.run("INSERT INTO actions(dispute_id,run_id,action,request,pre_counts,status,approved_by,edited,edited_text,created_at) VALUES(?,?,?,?,?,'pending',?,?,?,?)",
                    (dispute_id, run_id, verdict.action, store.jdump({"path": path, "kind": kind, "body": body}), store.jdump(_counts(snapshot)),
                     approved_by, int(bool(edited)), edited_text, config.iso(config.utcnow())))
    # the Disputes API documents no idempotency key, so none is sent
    try:
        r = paypal.PP.post(path, multipart_input=body) if kind == "multipart" else paypal.PP.post(path, json=body)
        # a timeout, 429 or 5xx may still have landed at PayPal, so the result is unknown rather than failed
        status = "unknown" if r.status in (0, 429) or r.status >= 500 else "sent" if 200 <= r.status < 300 else "failed"
        masked = paypal.mask(r.body)
        store.run("UPDATE actions SET status=?, http_status=?, debug_id=?, response=? WHERE id=?", (status, r.status, r.debug_id, store.jdump(masked), aid))
        res = {"action_id": aid, "status": status, "http_status": r.status, "debug_id": r.debug_id, "body": masked}
    except Exception as e:  # noqa: BLE001 - the write may have been sent; never leave the row 'pending'
        store.run("UPDATE actions SET status='unknown', http_status=0, response=? WHERE id=?", (store.jdump({"error": type(e).__name__}), aid))
        res = {"action_id": aid, "status": "unknown", "http_status": 0, "debug_id": None, "body": {"error": type(e).__name__}}
    F.invalidate(dispute_id)
    return res


def _envelope_step(run_id: int, dispute_id: str, snap: dict, verdict: dict, outcome: dict, persist, injection_suspected: bool = False):
    _, _, _, row = current_envelope()
    result = {"ok": False, "reason": "", "log_id": None, "send": None}

    def finish(ok, reason, log_id=None, send=None):
        result.update(ok=ok, reason=reason, log_id=log_id, send=send)
        persist({"step": "envelope", "status": "ok", "ms": 0, "data": {"ok": ok, "reason": reason}, "llm": None})
        if send:  # never persist the raw PayPal body: keep name and message only
            send = {**send, "body": paypal.mask({k: send["body"][k] for k in ("name", "message") if k in send.get("body", {})})}
            result["send"] = send
            persist({"step": "send", "status": "ok" if send["status"] == "sent" else "error", "ms": 0, "data": send, "llm": None})
        store.run("UPDATE runs SET envelope=? WHERE id=?", (store.jdump(result), run_id))

    if config.S.HOSTED and not (snap.get("meta") or {}).get("owned"):
        return finish(False, "shared case")
    with dispute_lock(dispute_id):
        if store.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] != "running":
            return finish(False, "the hearing was rejected")
        if store.one("SELECT id FROM actions WHERE dispute_id=? AND (status IN ('pending','unknown') OR run_id=?)", (dispute_id, run_id)):
            return finish(False, "an action is already in flight for this dispute or run")
        fresh = F.fetch_snapshot(dispute_id)
        fs = F.build_facts(fresh)
        pre = rules.precheck(fs, fresh)
        if facts_key(fs, pre) != store.one("SELECT facts_key FROM runs WHERE id=?", (run_id,))["facts_key"]:
            return finish(False, "dispute changed")
        v = agents.Verdict.model_validate(verdict)
        val = checks.validate_verdict(v, pre, fs, fresh)
        if not val.ok:
            return finish(False, "validation failed on fresh facts")
        ok, why, log_id = reserve_envelope(row, v, pre, outcome, fresh, run_id, dispute_id, config.utcnow(), injection_suspected)
        if not ok:
            return finish(False, why, log_id)
        send = send_action(dispute_id, run_id, v, fresh, fs, val.message_text, "envelope", False, None)
        store.run("UPDATE envelope_log SET status=? WHERE id=?", (send["status"], log_id))
        finish(True, why, log_id, send)


def run_live(run_id: int) -> None:
    row = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    snap, mode = store.jload(row["snapshot"]), row["mode"]
    steps: list[dict] = []

    def persist(step):
        steps.append(step)
        store.run("UPDATE runs SET steps=? WHERE id=?", (store.jdump(steps), run_id))
    try:
        res = execute(snap, mode, max_rounds=1 if config.S.HOSTED else 2, on_step=persist)
        fs = F.build_facts(snap)
        key = facts_key(fs, rules.precheck(fs, snap))
        # the run stays 'running' until the envelope step is over, so nobody can approve it while an auto-send may be in flight
        store.run("UPDATE runs SET facts_key=?, verdict=?, validation=?, outcome=?, steps=? WHERE id=?",
                  (key, store.jdump(res.verdict) if res.verdict else None, store.jdump(res.validation) if res.validation else None,
                   store.jdump(res.outcome) if res.outcome else None, store.jdump(steps), run_id))
        if mode == "desk" and res.status == "done" and res.validation["ok"] and current_envelope()[0] is not None:
            try:
                _envelope_step(run_id, row["dispute_id"], snap, res.verdict, res.outcome, persist,
                               any(s["step"] == "quarantine" and s["data"].get("injection_suspected") for s in steps))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                persist({"step": "envelope", "status": "error", "ms": 0, "data": {"ok": False, "reason": f"Internal error ({type(e).__name__})"}, "llm": None})
        store.run("UPDATE runs SET status=?, steps=? WHERE id=? AND status='running'", (res.status, store.jdump(steps), run_id))
    except Exception as e:  # noqa: BLE001 - a crashed hearing must end as an error run, not hang as "running"
        traceback.print_exc()
        steps.append({"step": "run", "status": "error", "ms": 0, "data": {"message": f"Internal error ({type(e).__name__})"}, "llm": None})
        store.run("UPDATE runs SET status='error', steps=? WHERE id=?", (store.jdump(steps), run_id))


def start_run(dispute_id: str, mode: str, owned: bool = True) -> int:
    import copy
    snap = copy.deepcopy(F.get_snapshot(dispute_id))
    # No dispute, no hearing. A failed order/capture read still runs: the envelope refuses and the seller sees the gap.
    if any(str(e.get("call", "")).startswith("GET /v1/customer/disputes/") for e in snap.get("errors") or []):
        raise ApiErr(502, "PayPal dispute read failed; retry")
    snap["meta"]["owned"] = owned
    with dispute_lock(dispute_id):
        if store.one("SELECT id FROM runs WHERE dispute_id=? AND status='running'", (dispute_id,)):
            raise ApiErr(409, "A hearing is already running for this dispute")
        rid = store.run("INSERT INTO runs(dispute_id,mode,status,snapshot,steps,created_at) VALUES(?,?,'running',?,'[]',?)",
                        (dispute_id, mode, store.jdump(snap), config.iso(config.utcnow())))
    EXECUTOR.submit(run_live, rid)
    return rid


# ---------------- approve / check ----------------

def _own_run(dispute_id, run_id):
    run = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run or run["dispute_id"] != dispute_id:
        raise ApiErr(404, "Run not found")
    return run


def _guard(run, dispute_id):
    validation = store.jload(run["validation"]) or {}
    if run["mode"] != "desk" or run["status"] != "done" or not run["verdict"] or not validation.get("ok"):
        raise ApiErr(409, "Only a finished hearing with a valid verdict can be approved")
    if store.one("SELECT id FROM actions WHERE run_id=? AND status IN ('pending','sent')", (run["id"],)):
        raise ApiErr(409, "Already sent")
    if store.one("SELECT id FROM actions WHERE dispute_id=? AND status IN ('pending','unknown')", (dispute_id,)):
        raise ApiErr(409, "A previous send's result is unknown; check it first.")


def approve(dispute_id: str, run_id: int, edits: dict | None) -> dict:
    run = _own_run(dispute_id, run_id)
    _guard(run, dispute_id)
    edits = edits or {}
    with dispute_lock(dispute_id):
        _guard(store.one("SELECT * FROM runs WHERE id=?", (run_id,)), dispute_id)
        fresh = F.fetch_snapshot(dispute_id)
        if F.incomplete(fresh.get("errors")):
            raise ApiErr(409, "PayPal read failed; retry")
        fs = F.build_facts(fresh)
        pre = rules.precheck(fs, fresh)
        key = facts_key(fs, pre)
        if key != run["facts_key"]:
            raise ApiErr(409, f"The dispute changed since this hearing (was {run['facts_key']}, now {key}). Run it again.")
        if pre.advisory:
            raise ApiErr(409, pre.advisory_text)
        v = agents.Verdict.model_validate(store.jload(run["verdict"]))
        if edits.get("amount") is not None:
            v.amount = float(edits["amount"])
        if edits.get("evidence_fact_ids") is not None:
            keep = set(edits["evidence_fact_ids"])
            v.evidence = [e for e in v.evidence if e.fact_id in keep]
        msg = edits.get("message")  # Edits.message is capped at 2000 characters by the request model
        val = checks.validate_verdict(v, pre, fs, fresh, message_override=msg)
        if not val.ok:
            raise ApiErr(422, None, errors=val.errors)
        return send_action(dispute_id, run_id, v, fresh, fs, val.message_text, "merchant",
                           any(edits.get(k) is not None for k in ("message", "amount", "evidence_fact_ids")), msg)


def _action_row(a: dict) -> dict:
    return {k: a[k] for k in ("id", "run_id", "action", "status", "http_status", "debug_id", "approved_by", "edited", "created_at")}


def check_action(dispute_id: str, action_id: int) -> dict:
    a = store.one("SELECT * FROM actions WHERE id=?", (action_id,))
    if not a or a["dispute_id"] != dispute_id:
        raise ApiErr(404, "Action not found")
    if a["status"] != "unknown":
        return _action_row(a)
    with dispute_lock(dispute_id):
        fresh = F.fetch_snapshot(dispute_id)
        if F.incomplete(fresh.get("errors")):
            raise ApiErr(409, "PayPal read failed; retry")
        now = _counts(fresh)
        pre = store.jload(a["pre_counts"])
        body = store.jload(a["request"])["body"]
        act, status_changed = a["action"], now["status"] != pre["status"]
        if act == "make_offer" and "offer_amount" in body:
            # an offer that carries an amount is confirmed only by PayPal showing that amount as new
            sent = (now["seller_offered"] is not None and now["seller_offered"] != pre["seller_offered"]
                    and float(now["seller_offered"]) == float(body["offer_amount"]["value"]))
        else:
            sent = ((act in ("accept_claim", "make_offer") and status_changed)
                    or (act == "accept_claim" and now["status"] == "RESOLVED")
                    or (act == "send_message" and now["seller_messages"] > pre["seller_messages"])
                    or (act == "provide_evidence" and now["seller_evidences"] > pre["seller_evidences"])
                    or (act == "provide_supporting_info" and now["seller_supporting"] > pre.get("seller_supporting", 0)))
        store.run("UPDATE actions SET status=? WHERE id=?", ("sent" if sent else "failed", action_id))
        F.invalidate(dispute_id)
    return _action_row(store.one("SELECT * FROM actions WHERE id=?", (action_id,)))
