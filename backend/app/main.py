"""FastAPI routes (/api), boot, hosted gating and rate limits, and the built frontend at "/"."""
import html
import json
from pathlib import Path
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import agents, checks, config, facts as F, llm, paypal, pipeline, rules, seed, shop, store

ApiErr = config.ApiErr
BOOT_ERRORS: list[str] = []
LIMITS: dict[tuple[str, str], deque] = defaultdict(deque)
_LIMIT_LOCK = threading.Lock()
_last_sweep = [0.0]
SHARED_CASE = "Shared demo case: dry run only"
HOSTED_OFF = "Disabled on the hosted demo"


# ---------------- boot ----------------

def _reconcile():
    for r in store.q("SELECT id, steps FROM runs WHERE status='running'"):
        steps = store.jload(r["steps"]) or []
        steps.append({"step": "restart", "status": "error", "ms": 0, "data": {"message": "interrupted by restart"}, "llm": None})
        store.run("UPDATE runs SET status='error', steps=? WHERE id=?", (store.jdump(steps), r["id"]))
    store.run("UPDATE actions SET status='unknown' WHERE status='pending'")
    store.run("UPDATE envelope_log SET status='unknown' WHERE status='reserved'")  # still counts toward the daily cap


def _seed():
    if not store.one("SELECT sku FROM products LIMIT 1"):
        shop.load_seed()


def _replays():
    for f in sorted(config.REPLAYS_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        name = d.get("name") or f.stem
        if store.one("SELECT id FROM runs WHERE replay_name=?", (name,)):
            continue
        store.run("INSERT INTO runs(dispute_id,mode,status,snapshot,steps,verdict,validation,outcome,envelope,facts_key,replay_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (d.get("dispute_id"), "replay", d.get("status", "done"), store.jdump(d["snapshot"]), store.jdump(d.get("steps", [])), store.jdump(d.get("verdict")) if d.get("verdict") else None,
                   store.jdump(d.get("validation")) if d.get("validation") else None, store.jdump(d.get("outcome")) if d.get("outcome") else None,
                   store.jdump(d.get("envelope")) if d.get("envelope") else None, d.get("facts_key"), name, d.get("created_at") or config.iso(config.utcnow())))


def _hosted_envelope():
    if config.S.HOSTED and config.S.HOSTED_ENVELOPE:
        checks.Envelope.model_validate_json(config.S.HOSTED_ENVELOPE)


def _llm_script():
    if config.S.LLM_SCRIPT and not config.S.HOSTED:
        llm.FAKE = llm.script_fake(json.loads(Path(config.S.LLM_SCRIPT).read_text(encoding="utf-8")))


def boot():
    """Every step is wrapped: failures go to BOOT_ERRORS and boot never fails. PayPal is not called here."""
    BOOT_ERRORS.clear()
    for name, fn in (("tables", store.init_default), ("reconcile", _reconcile), ("seed", _seed), ("replays", _replays), ("hosted envelope", _hosted_envelope), ("llm script", _llm_script)):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            BOOT_ERRORS.append(f"{name}: {type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app):
    boot()
    yield


app = FastAPI(lifespan=lifespan, title="Both Sides")
api = APIRouter(prefix="/api")


@app.middleware("http")
async def visitor_cookie(request: Request, call_next):
    vid = request.cookies.get("bs_visitor")
    new = not (vid and re.fullmatch(r"[0-9a-f]{32}", vid))
    if new:
        vid = uuid.uuid4().hex
    request.state.visitor = vid
    resp = await call_next(request)
    if new and request.url.path.startswith("/api"):
        resp.set_cookie("bs_visitor", vid, httponly=True, samesite="lax", secure=config.S.HOSTED, max_age=365 * 86400)
    return resp


@app.exception_handler(ApiErr)
async def _api_err(request, e: ApiErr):
    return JSONResponse(e.body, status_code=e.status)


@app.exception_handler(llm.LLMUnavailable)
async def _llm_down(request, e: llm.LLMUnavailable):
    return JSONResponse({"error": f"Model unavailable ({e.last_error})"}, status_code=503)


@app.exception_handler(RequestValidationError)
async def _invalid(request, e: RequestValidationError):
    msgs = [f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in e.errors()]
    return JSONResponse({"error": "Invalid request: " + "; ".join(msgs)}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def _http(request, e: StarletteHTTPException):
    return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)


# ---------------- hosted gating, rate limits, ownership ----------------

def client_key(request: Request) -> str:
    mode = config.S.CLIENT_IP_HEADER
    xff = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if mode == "xff-last" and xff:
        return xff[-1]
    if mode == "xff-first" and xff:
        return xff[0]
    if mode in ("cf-connecting-ip", "true-client-ip") and request.headers.get(mode):
        return request.headers[mode]
    return request.client.host if request.client else "unknown"


def limit(bucket: str, request: Request, n: int):
    """Sliding 3600 s window per (bucket, ip); hosted only. # ponytail: in-memory, resets on restart."""
    if not config.S.HOSTED:
        return
    key, now = (bucket, client_key(request)), time.time()
    with _LIMIT_LOCK:
        if len(LIMITS) > 1000 and now - _last_sweep[0] >= 60:  # sweep keys whose newest hit is outside the window, at most once a minute
            _last_sweep[0] = now
            for k in [k for k, d in LIMITS.items() if not d or d[-1] < now - 3600]:
                del LIMITS[k]
        q = LIMITS.get(key) or deque()
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= n:
            if not q:
                LIMITS.pop(key, None)
            raise ApiErr(429, "Limit reached; try a recorded run")
        q.append(now)
        LIMITS[key] = q


def _R() -> int:
    return config.S.RUNS_PER_HOUR


def hosted_off():
    if config.S.HOSTED:
        raise ApiErr(403, HOSTED_OFF)


def owned(dispute_id: str, request: Request) -> bool:
    if not config.S.HOSTED:
        return True
    return store.one("SELECT 1 AS x FROM orders WHERE dispute_id=? AND visitor=?", (dispute_id, request.state.visitor)) is not None


DISPUTE_ID_RE = re.compile(r"^PP-[A-Z]-[A-Za-z0-9-]{1,40}$")


def valid_dispute(dispute_id: str):
    """Dependency: reject malformed ids with 400 before any PayPal call."""
    if not DISPUTE_ID_RE.fullmatch(dispute_id):
        raise ApiErr(400, "Invalid dispute id")


V = [Depends(valid_dispute)]


def require_owned(dispute_id: str, request: Request):
    if not owned(dispute_id, request):
        raise ApiErr(403, SHARED_CASE)


# ---------------- summaries ----------------

def _run_action(r):
    return (store.jload(r["verdict"]) or {}).get("action") if r.get("verdict") else None


def summary(item: dict, request: Request) -> dict:
    did = item.get("dispute_id")
    amt = item.get("dispute_amount") or {}
    cents = round(float(amt["value"]) * 100) if amt.get("value") not in (None, "") else None
    due = config.parse_ts(item.get("seller_response_due_date"))
    lr = store.one("SELECT id, mode, status, verdict FROM runs WHERE dispute_id=? AND mode!='replay' ORDER BY id DESC LIMIT 1", (did,))
    aa = store.one("SELECT action, created_at FROM actions WHERE dispute_id=? AND approved_by='envelope' ORDER BY id DESC LIMIT 1", (did,))
    return {"id": did, "reason": item.get("reason"), "status": item.get("status"), "stage": item.get("dispute_life_cycle_stage"), "amount": F.money(amt),
            "amount_cents": cents, "currency": amt.get("currency_code"), "created": config.iso(item.get("create_time")), "due": config.iso(due),
            "hours_left": round((due - config.utcnow()).total_seconds() / 3600, 1) if due else None,
            "last_run": {"id": lr["id"], "mode": lr["mode"], "status": lr["status"], "action": _run_action(lr)} if lr else None,
            "auto_action": aa, "owned": owned(did, request)}


def _list(request: Request):
    try:
        return [summary(i, request) for i in F.list_disputes()]
    except F.ListError as e:
        raise ApiErr(502, str(e), debug_id=e.debug_id)


def run_json(r: dict) -> dict:
    snap = store.jload(r["snapshot"]) or {}
    return {"id": r["id"], "dispute_id": r["dispute_id"], "mode": r["mode"], "status": r["status"], "steps": store.jload(r["steps"]) or [], "verdict": store.jload(r["verdict"]),
            "validation": store.jload(r["validation"]), "outcome": store.jload(r["outcome"]), "envelope": store.jload(r["envelope"]),
            "facts": [F.fact_json(f) for f in F.build_facts(snap).values()] if snap else [], "replay_name": r["replay_name"]}


# ---------------- request bodies (every field has a default so hosted 403s come before validation errors) ----------------

class RunReq(BaseModel):
    mode: str = "desk"


class NoteReq(BaseModel):
    question: str = Field(max_length=200)
    answer: str = Field(max_length=300)


class Edits(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    amount: float | None = Field(default=None, allow_inf_nan=False)
    evidence_fact_ids: list[str] | None = None


class ActionReq(BaseModel):
    run_id: int
    edits: Edits | None = None


class SimReq(BaseModel):
    call: str = ""
    value: str = ""


class OrderReq(BaseModel):
    sku: str = ""
    size: str | None = None


class ShipReq(BaseModel):
    carrier: str = shop.DEFAULT_CARRIER
    size_shipped: str | None = None


class DeliverReq(BaseModel):
    elsewhere: bool = False
    signature: bool = False


class RefundReq(BaseModel):
    amount: float | None = Field(default=None, allow_inf_nan=False)


class ChargebackReq(BaseModel):
    reason_code: str | None = None


class ShopperReq(BaseModel):
    instruction: str = ""


# ---------------- routes ----------------

@api.get("/health")
def health():
    s = config.S
    if not s.PAYPAL_CLIENT_ID:
        pp = "missing credentials"
    else:
        try:
            paypal.PP.token()
            pp = "ok"
        except Exception as e:  # noqa: BLE001
            print(f"health: PayPal token failed: {e!r}")
            pp = "error" if s.HOSTED else (str(e) or type(e).__name__)
    boot = len(BOOT_ERRORS) if s.HOSTED else BOOT_ERRORS  # hosted shows a count, never paths or exception text
    return {"ok": True, "paypal": pp, "llm": [f"{p['host']}:{p['model']}" for p in llm._providers()], "boot_errors": boot}


@api.get("/config")
def get_config():
    s = config.S
    host = urlparse(s.LLM_BASE_URL).hostname or s.LLM_BASE_URL
    return {"paypal_client_id": s.PAYPAL_CLIENT_ID, "hosted": s.HOSTED, "max_rounds": 1 if s.HOSTED else 2, "llm_label": f"{s.LLM_MODEL} via {host}",
            "chargeback_enabled": s.CHARGEBACK_ENABLED, "shopper_paths": shop.shopper_paths()}


@api.get("/whoami")
def whoami(request: Request):
    if not config.S.HOSTED:
        raise ApiErr(404, "Not found")
    h = request.headers
    return {"xff": h.get("x-forwarded-for"), "cf_connecting_ip": h.get("cf-connecting-ip"), "true_client_ip": h.get("true-client-ip"),
            "client_host": request.client.host if request.client else None, "key": client_key(request)}


@api.get("/disputes")
def list_disputes(request: Request):
    return _list(request)


@api.post("/disputes/refresh")
def refresh(request: Request):
    limit("refresh", request, 30)
    F.invalidate()
    return _list(request)


@api.get("/disputes/{dispute_id}", dependencies=V)
def dispute_detail(dispute_id: str, request: Request):
    limit("reads", request, 60 * _R())
    snap = F.get_snapshot(dispute_id)
    fs = F.build_facts(snap)
    runs = [{"id": r["id"], "mode": r["mode"], "status": r["status"], "action": _run_action(r), "created_at": r["created_at"]}
            for r in store.q("SELECT id, mode, status, verdict, created_at FROM runs WHERE dispute_id=? ORDER BY id DESC", (dispute_id,))]
    acts = [pipeline._action_row(a) for a in store.q("SELECT * FROM actions WHERE dispute_id=? ORDER BY id DESC", (dispute_id,))]
    item = dict(snap.get("dispute") or {})
    item.setdefault("dispute_id", dispute_id)
    return {"summary": summary(item, request), "facts": [F.fact_json(f) for f in fs.values()], "precheck": asdict(rules.precheck(fs, snap)),
            "snapshot_errors": snap.get("errors", []), "runs": runs, "actions": acts}


@api.post("/disputes/{dispute_id}/runs", status_code=202, dependencies=V)
def start_run(dispute_id: str, body: RunReq, request: Request):
    if body.mode not in ("desk", "baseline"):
        raise ApiErr(400, "mode must be desk or baseline")
    limit("runs", request, _R())
    return {"run_id": pipeline.start_run(dispute_id, body.mode, owned(dispute_id, request))}


@api.get("/runs/{run_id}")
def get_run(run_id: int, request: Request):
    r = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r or r["status"] != "running":  # the Case page polls a running hearing every 700 ms: those polls are free
        limit("reads", request, 60 * _R())
    if not r:
        raise ApiErr(404, "Run not found")
    return run_json(r)


@api.get("/runs/{run_id}/dry-run")
def dry_run(run_id: int, request: Request):
    limit("reads", request, 60 * _R())
    r = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise ApiErr(404, "Run not found")
    if not r["verdict"] or not r["validation"]:
        raise ApiErr(409, "This run has no verdict")
    snap = store.jload(r["snapshot"])
    v = agents.Verdict.model_validate(store.jload(r["verdict"]))
    fs = F.build_facts(snap)
    # Re-validate exactly as approve does, so the dry run shows what approve would send (not the stored text).
    val = checks.validate_verdict(v, rules.precheck(fs, snap), fs, snap)
    if not val.ok:  # approve would refuse this verdict, so there is nothing to preview
        raise ApiErr(422, None, errors=val.errors)
    suffix, kind, body = paypal.build_action_request(v, snap, fs, val.message_text)
    return {"path": f"/v1/customer/disputes/{r['dispute_id']}/{suffix}", "kind": kind, "body": body}


@api.get("/replays")
def replays():
    return [{"run_id": r["id"], "replay_name": r["replay_name"], "dispute_id": r["dispute_id"], "status": r["status"], "action": _run_action(r)}
            for r in store.q("SELECT id, replay_name, dispute_id, status, verdict FROM runs WHERE mode='replay' ORDER BY id")]


@api.post("/disputes/{dispute_id}/actions/{action_id}/check", dependencies=V)
def check_action(dispute_id: str, action_id: int, request: Request):
    require_owned(dispute_id, request)
    limit("writes", request, 2 * _R())
    return pipeline.check_action(dispute_id, action_id)


@api.post("/disputes/{dispute_id}/runs/{run_id}/reject", dependencies=V)
def reject(dispute_id: str, run_id: int, request: Request):
    require_owned(dispute_id, request)
    limit("writes", request, 2 * _R())
    run = pipeline._own_run(dispute_id, run_id)
    with pipeline.dispute_lock(dispute_id):
        run = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if run["mode"] == "replay":
            raise ApiErr(409, "A recorded hearing cannot be rejected")
        if run["status"] == "running":
            raise ApiErr(409, "The hearing is still running")
        if store.one("SELECT id FROM actions WHERE run_id=? AND status IN ('pending','sent','unknown')", (run_id,)):
            raise ApiErr(409, "This hearing already has an action")
        store.run("UPDATE runs SET status='rejected' WHERE id=?", (run_id,))
    return {"ok": True}


@api.post("/disputes/{dispute_id}/notes", dependencies=V)
def add_note(dispute_id: str, body: NoteReq, request: Request):
    require_owned(dispute_id, request)
    limit("writes", request, 2 * _R())
    store.run("INSERT INTO notes(dispute_id,question,answer,created_at) VALUES(?,?,?,?)",
              (dispute_id, agents.clean_untrusted(body.question, 200), agents.clean_untrusted(body.answer, 300), config.iso(config.utcnow())))
    F.invalidate(dispute_id)
    return {"ok": True}


@api.post("/disputes/{dispute_id}/actions", dependencies=V)
def approve(dispute_id: str, body: ActionReq, request: Request):
    require_owned(dispute_id, request)
    limit("writes", request, 2 * _R())
    return pipeline.approve(dispute_id, body.run_id, body.edits.model_dump() if body.edits else None)


@api.post("/disputes/{dispute_id}/simulate", dependencies=V)
def simulate(dispute_id: str, body: SimReq, request: Request):
    valid = {"require_evidence": ("SELLER_EVIDENCE", "BUYER_EVIDENCE"), "adjudicate": ("SELLER_FAVOR", "BUYER_FAVOR")}
    if body.call not in valid or body.value not in valid[body.call]:
        raise ApiErr(400, "Invalid call or value")
    require_owned(dispute_id, request)
    limit("writes", request, 2 * _R())
    fresh = F.fetch_snapshot(dispute_id)
    if body.call not in rules.precheck(F.build_facts(fresh), fresh).sandbox_calls:
        raise ApiErr(409, f"PayPal does not offer {body.call} on this dispute right now")
    suffix, _, payload = paypal.build_sandbox_request(body.call, body.value)
    r = paypal.PP.post(f"/v1/customer/disputes/{dispute_id}/{suffix}", json=payload)
    F.invalidate(dispute_id)
    return {"status": r.status, "debug_id": r.debug_id, "body": paypal.mask(r.body)}


@api.get("/receipts/{receipt_id}")
def receipt(receipt_id: int):
    r = store.one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
    if not r:
        raise ApiErr(404, "Receipt not found")
    return {**{k: r[k] for k in ("id", "via", "method", "path", "status", "debug_id")}, "body": store.jload(r["body"])}


@api.get("/envelope")
def get_envelope():
    e, h, src, _ = pipeline.current_envelope()
    return {"envelope": e.model_dump(mode="json") if e else None, "hash": h, "source": src}


@api.put("/envelope", dependencies=[Depends(hosted_off)])
def put_envelope(e: checks.Envelope):
    h = pipeline.save_envelope(e)
    return {"envelope": e.model_dump(mode="json"), "hash": h, "source": "local"}


@api.get("/envelope/log")
def envelope_log():
    return [{"id": r["id"], "dispute_id": r["dispute_id"], "run_id": r["run_id"], "action": r["action"], "amount": r["amount_cents"] / 100 if r["amount_cents"] is not None else None,
             "ok": bool(r["ok"]), "reason": r["reason"], "status": r["status"], "created_at": r["created_at"]}
            for r in store.q("SELECT * FROM envelope_log ORDER BY id DESC LIMIT 100")]


@api.post("/demo/new-dispute")
def new_dispute(request: Request):
    s = config.S
    if not (s.HOSTED and s.CHARGEBACK_ENABLED and (s.SHOPPER_VAULT_TOKEN or s.SHOPPER_TEST_CARD)):
        raise ApiErr(403, "Not available")
    limit("new_dispute", request, max(1, _R() // 2))
    return seed.new_dispute(request.state.visitor)


@api.get("/bench")
def bench():
    p = config.BENCH_DIR / "results" / "latest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---- shop ----

@api.get("/shop/products")
def shop_products():
    return shop.products()


@api.get("/shop/orders")
def shop_orders(request: Request):
    return shop.order_rows(visitor=request.state.visitor if config.S.HOSTED else None)


@api.post("/shop/orders")
def shop_create(body: OrderReq, request: Request):
    limit("shop", request, _R())
    oid, _ = shop.create_order(body.sku, body.size, request.state.visitor)
    return {"id": oid}


@api.post("/shop/orders/{order_id}/capture")
def shop_capture(order_id: str, request: Request):
    limit("shop", request, _R())
    row = store.one("SELECT visitor FROM orders WHERE id=?", (order_id,))
    if config.S.HOSTED and row and row["visitor"] != request.state.visitor:
        raise ApiErr(403, "This order belongs to another visitor")
    return shop.capture(order_id)


@api.post("/shop/orders/{order_id}/ship")
def shop_ship(order_id: str, body: ShipReq = ShipReq()):
    hosted_off()
    return shop.ship(order_id, body.carrier, body.size_shipped)


@api.post("/shop/orders/{order_id}/deliver")
def shop_deliver(order_id: str, body: DeliverReq = DeliverReq()):
    hosted_off()
    return shop.deliver(order_id, body.elsewhere, body.signature)


@api.post("/shop/orders/{order_id}/return-delivered")
def shop_return_delivered(order_id: str):
    hosted_off()
    return shop.return_delivered(order_id)


@api.post("/shop/orders/{order_id}/refund")
def shop_refund(order_id: str, body: RefundReq = RefundReq()):
    hosted_off()
    if body.amount is None:
        raise ApiErr(400, "amount is required")
    return shop.refund(order_id, body.amount)


@api.post("/shop/orders/{order_id}/chargeback")
def shop_chargeback(order_id: str, body: ChargebackReq = ChargebackReq()):
    hosted_off()
    if not config.S.CHARGEBACK_ENABLED:
        raise ApiErr(403, "Chargebacks are not enabled")
    return shop.chargeback(order_id, body.reason_code)


# ---- shopper ----

@api.post("/shopper/run")
def shopper_run(body: ShopperReq, request: Request):
    limit("shopper", request, max(1, _R() // 2))
    return shop.run_shopper(body.instruction, request.state.visitor)


@api.post("/shopper/vault/setup")
def vault_setup():
    hosted_off()
    return shop.vault_setup()


@api.get("/shopper/vault/return")
def vault_return(approval_token_id: str = ""):
    hosted_off()
    if not approval_token_id:
        raise ApiErr(400, "approval_token_id is required")
    tid = shop.vault_return(approval_token_id)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Shopper wallet connected</title><body style='font-family:system-ui;margin:2rem'>"
                        f"<h1>Shopper wallet connected</h1><p>Put this in your <code>.env</code> and restart:</p><pre>SHOPPER_VAULT_TOKEN={html.escape(tid)}</pre></body>")


@api.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def api_not_found(path: str):
    raise ApiErr(404, "Not found")


app.include_router(api)
if config.DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.DIST_DIR), html=True), name="frontend")
