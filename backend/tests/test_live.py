"""Live smoke tests against the PayPal sandbox: python -m pytest -m live (needs .env). Deselected by default."""
import re

import pytest

from app import config, facts, paypal, store

pytestmark = pytest.mark.live

ID_PATTERNS = [r"pp\.dispute\.(id|reason|status|stage|amount|created|due|requested_evidence|buyer_requested)", r"pp\.capture\.(id|amount|status|time|seller_protection)",
               r"pp\.order\.(ship_to|items|tracking)", r"pp\.refund\.\d+\.(id|amount|status)", r"shop\.product\.(name|category|kind|color|price|description)",
               r"shop\.order\.(size|via)", r"shop\.shipment\.(carrier|tracking|shipped_at|delivered_at|signature|size_shipped|return_delivered_at)",
               r"shop\.refund\.\d+\.(id|amount|at)", r"shop\.policy\.(returns|shipping)", r"shop\.mandate\.(text|constraints|choice|reason|hash)",
               r"merchant\.note\.\d+", r"check\.(address_match|hours_left|refunded_total)"]


@pytest.fixture
def live(tmp_path):
    if not config.S.PAYPAL_CLIENT_ID:
        pytest.skip("no PayPal credentials in .env")
    store.init(tmp_path / "live.sqlite")
    paypal.PP = paypal.PayPal(config.S.PAYPAL_CLIENT_ID, config.S.PAYPAL_CLIENT_SECRET)
    yield
    store.close()


def test_token_then_list_then_snapshot_facts_fit_patterns(live):
    assert paypal.PP.token()
    r = paypal.PP.read_tool("list_disputes", {})
    assert r.status == 200
    items = r.body.get("items", [])
    if not items:
        pytest.skip("no disputes in the sandbox")
    snap = facts.fetch_snapshot(items[0].get("dispute_id") or items[0]["dispute_info"]["dispute_id"])
    fs = facts.build_facts(snap)
    assert fs["pp.dispute.id"].value
    for fid in fs:
        assert any(re.fullmatch(p, fid) for p in ID_PATTERNS), fid
