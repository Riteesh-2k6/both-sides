import dataclasses
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from app import config, facts, llm, paypal, pipeline, store  # noqa: E402
import fakes  # noqa: E402


class SyncExecutor:
    def submit(self, fn, *a, **k):
        f = Future()
        try:
            f.set_result(fn(*a, **k))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


BASE = config.load({})  # defaults only: a real .env must never leak into tests
REPO_REPLAYS = config.REPLAYS_DIR


def _settings(monkeypatch, tmp_path, **kw):
    kw.setdefault("USE_AGENT_TOOLKIT", False)
    s = dataclasses.replace(BASE, DATA_DIR=str(tmp_path), **kw)
    monkeypatch.setattr(config, "S", s)
    # Real recorded replays in the repo must not leak into tests (boot loads them as runs and shifts run ids).
    # Only replace the repo default; a test that points REPLAYS_DIR somewhere itself keeps its choice.
    if config.REPLAYS_DIR == REPO_REPLAYS:
        empty = tmp_path / "no-replays"
        empty.mkdir(exist_ok=True)
        monkeypatch.setattr(config, "REPLAYS_DIR", empty)
    return s


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Just a fresh database and settings; no app, no boot."""
    _settings(monkeypatch, tmp_path)
    monkeypatch.setattr(paypal, "RETRY_SLEEP", 0)
    store.init(tmp_path / "t.sqlite")
    facts.invalidate()
    yield
    store.close()


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Factory: env = app_env(hosted=False, routes=None, script=None, snapshot=None, **settings)."""
    from fastapi.testclient import TestClient
    from app import main

    def make(hosted=False, routes=None, script=None, repeat=False, snapshot=None, boot=True, **settings):
        s = _settings(monkeypatch, tmp_path, HOSTED=hosted, **settings)
        monkeypatch.setattr(paypal, "RETRY_SLEEP", 0)
        store.init(tmp_path / "t.sqlite")
        facts.invalidate()
        llm.COOLDOWN.clear()
        main.LIMITS.clear()
        if snapshot is not None:
            routes = fakes.routes_for_snapshot(snapshot, routes)
        pp = fakes.fake_paypal(routes)
        monkeypatch.setattr(paypal, "PP", pp)
        fl = fakes.fake_llm(script or {}, repeat=repeat)
        monkeypatch.setattr(llm, "FAKE", fl)
        monkeypatch.setattr(pipeline, "EXECUTOR", SyncExecutor())
        if boot:
            main.boot()
        if snapshot is not None:
            fakes.seed_shop(snapshot)
        client = TestClient(main.app, raise_server_exceptions=True, base_url="https://testserver" if hosted else "http://testserver")  # the hosted cookie is Secure
        return SimpleNamespace(client=client, pp=pp, llm=fl, S=s, main=main)

    yield make
    store.close()
