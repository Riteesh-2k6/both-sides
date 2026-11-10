"""Settings, read from the environment once: S = load(). Other modules use config.S.X at call time
(import config, never `from config import S`) so tests can monkeypatch config.S."""
import dataclasses
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (/app in the Docker image)
SEED_PATH = ROOT / "seed" / "shop.json"
REPLAYS_DIR = ROOT / "replays"
BENCH_DIR = ROOT / "bench"
DIST_DIR = ROOT / "frontend" / "dist"


@dataclass(frozen=True)
class Settings:
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MERCHANT_ID: str = ""
    PAYPAL_BASE: str = "https://api-m.sandbox.paypal.com"
    LLM_BASE_URL: str = "http://localhost:8080/v1"
    LLM_MODEL: str = "qwen3-4b"
    LLM_API_KEY: str = "local"
    LLM_JSON_MODE: str = "strict"
    FALLBACK_LLM_BASE_URL: str = ""
    FALLBACK_LLM_MODEL: str = ""
    FALLBACK_LLM_API_KEY: str = ""
    FALLBACK_LLM_JSON_MODE: str = "strict"
    LLM_TEMPERATURE: float = 0.0
    USE_AGENT_TOOLKIT: bool = True
    HOSTED: bool = False
    APP_BASE_URL: str = "http://localhost:8000"
    RUNS_PER_HOUR: int = 6
    CHARGEBACK_ENABLED: bool = False
    CHARGEBACK_REASON_CODE: str = "10.4"
    CHARGEBACK_CARD_BRAND: str = "VISA"
    SHOPPER_VAULT_TOKEN: str = ""
    SHOPPER_TEST_CARD: bool = False
    HOSTED_ENVELOPE: str = ""
    DATA_DIR: str = "./data"
    CLIENT_IP_HEADER: str = "xff-last"
    LLM_SCRIPT: str = ""
    PORT: int = 8000


def _flag(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load(env=None) -> Settings:
    """Missing vars default as in Settings (credentials default to "")."""
    env = os.environ if env is None else env
    kw = {}
    for f in dataclasses.fields(Settings):
        if f.name not in env or env[f.name] == "":
            continue
        raw = env[f.name]
        kw[f.name] = _flag(raw) if f.type in (bool, "bool") else float(raw) if f.type in (float, "float") else int(raw) if f.type in (int, "int") else raw
    return Settings(**kw)


def _dotenv():
    p = ROOT / ".env"
    env = dict(os.environ)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


S = load(_dotenv())


# ---- time helpers: every datetime is aware UTC, serialised YYYY-MM-DDTHH:MM:SSZ ----

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_ts(s):
    """ISO string (or datetime) -> aware UTC datetime; naive input is UTC; None/garbage -> None."""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        d = s
    else:
        try:
            d = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def iso(d) -> str | None:
    d = parse_ts(d)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


class ApiErr(Exception):
    """An HTTP error: {error: str} or, for 422 from actions, {errors: [str]}. Raised anywhere, rendered by main."""

    def __init__(self, status: int, error=None, **extra):
        self.status = status
        self.body = {"error": error, **extra} if error is not None else dict(extra)
        super().__init__(str(error))
