"""The merchant is an Indian exporter (Rangsaaz Prints, Jaipur) selling to buyers abroad."""
import json
import re
import typing

import pytest

import fakes
from app import facts, paypal, rules, shop, store
from test_api import posts, shop_routes

ENUMS = json.loads((fakes.FIX / "paypal_enums.json").read_text(encoding="utf-8"))


def test_shop_constants():
    assert shop.SHOP_NAME == "Rangsaaz Prints" and shop.SHOPPER_NAME == "Sam Rivera"
    assert shop.POLICY_RETURNS == "Unused items can be returned within 30 days. International return postage is paid by the buyer unless the item is faulty or not as described."
    assert shop.POLICY_SHIPPING == ("Ships from Jaipur in 2–4 business days by India Post (registered/EMS) or DHL, with tracking. "
                                    "International delivery takes 7–21 days; tracking can pause after a parcel leaves India. Signature on orders over $750.")
    jaipur = {"address_line_1": "21 Block Printers Lane", "admin_area_2": "Jaipur", "admin_area_1": "RJ", "postal_code": "302001", "country_code": "IN"}
    assert shop.SHOP_RETURN_ADDRESS == jaipur and shop.SHOP_BILLING_ADDRESS == jaipur
    assert shop.SHOPPER_SHIP_ADDRESS["country_code"] == "US" and shop.SHOPPER_SHIP_ADDRESS["postal_code"] == "73301"
    assert facts.address_key(shop.SHOP_RETURN_ADDRESS) != facts.address_key(shop.SHOPPER_SHIP_ADDRESS)


def test_policy_facts_come_from_the_constants():
    fs = facts.build_facts(fakes.snapshot())
    assert fs["shop.policy.returns"].value == shop.POLICY_RETURNS and fs["shop.policy.shipping"].value == shop.POLICY_SHIPPING


def test_carrier_mapping_lives_in_one_place_and_exists_in_both_enums():
    assert shop.DEFAULT_CARRIER == "INDIA_POST" and set(shop.CARRIERS) == {"INDIA_POST", "DHL"}
    for name, m in shop.CARRIERS.items():
        assert m["orders"] in ENUMS["orders_carrier"], name  # Orders /track
        assert m["dispute"] in ENUMS["dispute_carrier_name"], name  # Disputes tracking_info.carrier_name
    assert shop.CARRIERS["INDIA_POST"]["label"] == "India Post"


def test_tracking_number_shapes():
    for _ in range(20):
        assert re.fullmatch(r"EE\d{9}IN", shop.tracking_number("INDIA_POST")) and re.fullmatch(r"\d{10}", shop.tracking_number("DHL"))


def test_ship_defaults_to_india_post_and_dhl_stays_available(app_env):
    env = app_env(routes=shop_routes())
    c = env.client
    c.post("/api/shop/orders", json={"sku": "RP-SCARF-IND", "size": None})
    c.post("/api/shop/orders/ORD-A/capture")
    j = c.post("/api/shop/orders/ORD-A/ship", json={}).json()
    assert j["shipment"]["carrier"] == "INDIA_POST" and re.fullmatch(r"EE\d{9}IN", j["shipment"]["tracking"])
    assert [p for p in posts(env) if p["path"].endswith("/track")][0]["json"]["carrier"] == "INDIA_POST"
    store.run("DELETE FROM shipments")
    j = c.post("/api/shop/orders/ORD-A/ship", json={"carrier": "DHL"}).json()
    assert j["shipment"]["carrier"] == "DHL" and re.fullmatch(r"\d{10}", j["shipment"]["tracking"])
    assert [p for p in posts(env) if p["path"].endswith("/track")][-1]["json"]["carrier"] == "DHL"
    store.run("DELETE FROM shipments")
    assert c.post("/api/shop/orders/ORD-A/ship", json={"carrier": "USPS"}).status_code == 400


def test_evidence_tracking_info_uses_the_dispute_carrier_value():
    s = fakes.snapshot()
    fs = facts.build_facts(s)
    _, _, body = paypal.build_action_request(fakes.verdict(), s, fs, "Hello.")
    info = body["evidences"][0]["evidence_info"]["tracking_info"][0]
    assert info == {"carrier_name": "INDIA_POST", "tracking_number": "EE123456789IN"} and info["carrier_name"] in ENUMS["dispute_carrier_name"]


def test_shopper_categories():
    assert set(typing.get_args(typing.get_args(shop.Constraints.model_fields["category"].annotation)[0])) == {"scarf", "quilt", "cushion", "pattern"}
    assert shop.Constraints(category=None).category is None
    with pytest.raises(Exception):
        shop.Constraints(category="road")
    assert "scarf, quilt, cushion, pattern" in shop.CONSTRAINTS_SYSTEM


def test_international_inr_text_and_chargeback_rule():
    s = fakes.snapshot()
    fs = facts.build_facts(s)
    assert rules.precheck(fs, s).coverage_rule == "Delivered to the address on the transaction (international shipments need proof of delivery)."
    s["dispute"]["dispute_life_cycle_stage"] = "CHARGEBACK"
    s["dispute"]["dispute_channel"] = "EXTERNAL"
    p = rules.precheck(facts.build_facts(s), s)
    assert (p.coverage, p.coverage_rule) == ("not_covered", "Card chargebacks are not covered by PayPal Seller Protection.")
