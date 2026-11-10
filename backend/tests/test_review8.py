"""Pre-push review round 8 regressions."""
import httpx

import fakes
from app import checks, facts, llm, paypal, store
from app.agents import Claim


def test_failed_dispute_read_starts_no_hearing(app_env):
    env = app_env(routes={("GET", r"/v1/customer/disputes/PP-F-1"): (500, {"name": "X"})})
    r = env.client.post("/api/disputes/PP-F-1/runs", json={"mode": "desk"})
    assert r.status_code == 502
    assert store.one("SELECT COUNT(*) AS n FROM runs")["n"] == 0 and not env.llm.calls


def test_unframed_inference_still_gets_the_delivery_check():
    fs = facts.build_facts(fakes.snapshot("inr_delivered"))
    bare = Claim(id="M1", text="The quilt was never received.", kind="inference",
                 evidence_ids=["shop.shipment.delivered_at"], responds_to=[])
    kept, dropped = checks.verify_claims([bare], fs)
    assert not kept and dropped[0][0].id == "M1"


def test_malformed_wait_hint_does_not_raise():
    assert llm._duration("8.5.s") == 0
    assert llm._reset_seconds(httpx.Headers({}), "Please try again in 8.5.s") is None


def test_truncated_fact_id_list_is_stripped():
    assert checks._strip_fact_ids("It shipped on 2026-10-29 (pp.order.trac") == "It shipped on 2026-10-29"
    assert checks._strip_fact_ids("It shipped [fact_ids: shop.shipment.tracking, pp.or") == "It shipped"
    assert checks._strip_fact_ids("It shipped (by India Post)") == "It shipped (by India Post)"


def test_decimal_sizes_are_distinct():
    from app import shop
    assert shop.match_size("5", ["5", "5.5"]) == "5"
    assert shop.match_size("size 5.5", ["5", "5.5"]) == "5.5"


def test_hosted_health_hides_error_detail(app_env, monkeypatch):
    env = app_env(hosted=True, PAYPAL_CLIENT_ID="test-id")
    def boom():
        raise RuntimeError(r"C:\private\path token failed")
    monkeypatch.setattr(paypal.PP, "token", boom)
    j = env.client.get("/api/health").json()
    assert j["paypal"] == "error" and isinstance(j["boot_errors"], int)
