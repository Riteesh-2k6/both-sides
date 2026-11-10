"""sqlite3 schema and helpers. One connection (check_same_thread=False) guarded by LOCK (RLock).
Modules write their own SQL; never call PayPal or an LLM while holding LOCK."""
import json
import sqlite3
import threading
from pathlib import Path

from . import config

LOCK = threading.RLock()
_db: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY, name, category TEXT, kind TEXT, color, price_cents INT, sizes TEXT, description);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, status TEXT, capture_id TEXT, sku, size, amount_cents INT, via TEXT, mandate_id INT, dispute_id TEXT, created_at, visitor TEXT);
CREATE TABLE IF NOT EXISTS shipments(order_id TEXT PRIMARY KEY, carrier, tracking, shipped_at, shipped_to_key, delivered_at, signature INT, size_shipped, return_delivered_at);
CREATE TABLE IF NOT EXISTS refunds(id INTEGER PRIMARY KEY, order_id, paypal_refund_id, amount_cents INT, created_at);
CREATE TABLE IF NOT EXISTS mandates(id INTEGER PRIMARY KEY, text, constraints TEXT, choice TEXT, reason, hash, created_at);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, dispute_id, question, answer, created_at);
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY, via TEXT, method, path, status INT, debug_id, body TEXT, created_at);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, dispute_id, mode TEXT, status TEXT, snapshot TEXT, steps TEXT, verdict TEXT, validation TEXT, outcome TEXT, envelope TEXT, facts_key TEXT, replay_name TEXT UNIQUE, created_at);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, dispute_id, run_id, action, request TEXT, pre_counts TEXT, status TEXT, http_status INT, debug_id, response TEXT, approved_by TEXT, edited INT, edited_text TEXT, created_at);
CREATE TABLE IF NOT EXISTS envelopes(id INTEGER PRIMARY KEY, body TEXT, hash, active INT, created_at);
CREATE TABLE IF NOT EXISTS envelope_log(id INTEGER PRIMARY KEY, envelope_id, dispute_id, run_id, action, amount_cents INT, ok INT, reason, status TEXT, created_at);
CREATE INDEX IF NOT EXISTS actions_dispute ON actions(dispute_id);
CREATE INDEX IF NOT EXISTS runs_dispute ON runs(dispute_id);
"""


def data_dir() -> Path:
    """DATA_DIR; a relative value is anchored at backend/ (Docker's WORKDIR too), never at the shell's cwd."""
    d = Path(config.S.DATA_DIR)
    return d if d.is_absolute() else Path(__file__).resolve().parent.parent / d


def init(path=None):
    """Create tables. path=None uses DATA_DIR/both_sides.sqlite. Replaces any open connection."""
    global _db
    with LOCK:
        if path is None:
            d = data_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / "both_sides.sqlite"
        close()
        _db = sqlite3.connect(str(path), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
        _db.commit()


def init_default():
    """Boot: keep an already-open connection (tests open their own), otherwise open the default file."""
    if _db is None:
        init()


def close():
    global _db
    with LOCK:
        if _db is not None:
            _db.close()
            _db = None


def q(sql, params=()) -> list[dict]:
    with LOCK:
        return [dict(r) for r in _db.execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def run(sql, params=()) -> int:
    with LOCK:
        cur = _db.execute(sql, params)
        _db.commit()
        return cur.lastrowid


def jdump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)


def jload(s):
    return None if s is None else json.loads(s)
