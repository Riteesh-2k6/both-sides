"""Seeding CLI (prints JSON):
  python -m app.seed order --sku RP-QUILT --size Queen [--pay vault|card] [--ship] [--deliver | --deliver-elsewhere] [--signature]
                     [--size-shipped S] [--refund AMOUNT] [--return-delivered] [--chargeback [CODE]]
  python -m app.seed export --orders ID[,ID...]      merge those orders into seed/shop.json
  python -m app.seed export-run RUN_ID NAME          write replays/NAME.json
--pay vault (default) pays from the buyer's vaulted PayPal wallet (SHOPPER_VAULT_TOKEN); this merchant cannot take cards,
--pay card uses PayPal's sandbox test card and is kept for completeness."""
import argparse
import json
import random
import time

from . import config, paypal, shop, store

PK = {"orders": "id", "shipments": "order_id", "refunds": "id", "mandates": "id"}


def order(sku, size, *, pay="vault", ship=False, deliver=False, deliver_elsewhere=False, signature=False, size_shipped=None, refund=None,
               return_delivered=False, chargeback=None, via="seed", flags="", visitor=None, poll=False) -> dict:
    """Create and capture an order (pay: vault or card), then apply each flag in the order listed. chargeback: None, True (default code) or a reason code."""
    oid = shop.place_order(sku, size, visitor, pay=pay, via=via, flags=flags)
    out = {"order_id": oid, "capture_id": store.one("SELECT capture_id FROM orders WHERE id=?", (oid,))["capture_id"]}
    if ship:
        shop.ship(oid, size_shipped=size_shipped)
    if deliver or deliver_elsewhere:
        shop.deliver(oid, elsewhere=deliver_elsewhere, signature=signature)
    if refund is not None:
        shop.refund(oid, refund)
    if return_delivered:
        shop.return_delivered(oid)
    if chargeback is not None:
        res = shop.chargeback(oid, None if chargeback is True else chargeback)
        did = res["dispute_id"]
        for _ in range(15 if poll and not did else 0):  # the dispute shows up in PayPal a few minutes later
            time.sleep(20)
            r = paypal.PP.get("/v1/customer/disputes", params={"disputed_transaction_id": out["capture_id"]})
            items = r.body.get("items") or []
            if items:
                did = items[0].get("dispute_id")
                store.run("UPDATE orders SET dispute_id=? WHERE id=?", (did, oid))
                break
        out["dispute_id"] = did
    return out


def new_dispute(visitor: str | None) -> dict:
    """POST /demo/new-dispute: a shipped and delivered order for a random physical SKU, paid from the vaulted wallet when SHOPPER_VAULT_TOKEN
    is set (otherwise the test card), then a chargeback. No polling."""
    p = random.choice([p for p in shop.products() if p["kind"] == "physical"])
    out = order(p["sku"], random.choice(p["sizes"]) if p["sizes"] else None, pay="vault" if config.S.SHOPPER_VAULT_TOKEN else "card", ship=True, deliver=True, chargeback=True, flags="sd", visitor=visitor)
    return {"order_id": out["order_id"], "capture_id": out["capture_id"], "message": "Your case appears in the Inbox in a few minutes."}


def export_orders(ids: list[str]) -> None:
    d = json.loads(config.SEED_PATH.read_text(encoding="utf-8"))
    rows = {"orders": [], "shipments": [], "refunds": [], "mandates": []}
    for oid in ids:
        o = store.one("SELECT * FROM orders WHERE id=?", (oid,))
        if not o:
            raise SystemExit(f"no such order {oid}")
        o.pop("visitor", None)
        rows["orders"].append(o)
        rows["shipments"] += store.q("SELECT * FROM shipments WHERE order_id=?", (oid,))
        rows["refunds"] += store.q("SELECT * FROM refunds WHERE order_id=?", (oid,))
        if o["mandate_id"]:
            rows["mandates"] += store.q("SELECT * FROM mandates WHERE id=?", (o["mandate_id"],))
    for table, new in rows.items():
        pk = PK[table]
        keep = [r for r in d.get(table, []) if r[pk] not in {n[pk] for n in new}]
        d[table] = keep + new
    config.SEED_PATH.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def export_run(run_id: int, name: str) -> None:
    r = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise SystemExit(f"no such run {run_id}")
    snap = store.jload(r["snapshot"])
    snap["receipts"] = {}  # receipt ids belong to this database; replays say "Recorded snapshot"
    d = {"name": name, "dispute_id": r["dispute_id"], "mode": "desk", "status": r["status"], "snapshot": snap, "steps": store.jload(r["steps"]),
         "verdict": store.jload(r["verdict"]), "validation": store.jload(r["validation"]), "outcome": store.jload(r["outcome"]),
         "facts_key": r["facts_key"], "envelope": store.jload(r["envelope"]), "created_at": r["created_at"]}
    config.REPLAYS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPLAYS_DIR / f"{name}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.seed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("order", help="place and capture a sandbox order, then apply the flags in this order")
    c.add_argument("--sku", required=True, help="e.g. RP-QUILT")
    c.add_argument("--size", default=None, help='e.g. Queen, "16 in"; omit for products without sizes')
    c.add_argument("--pay", choices=["vault", "card"], default="vault", help="vault: buyer's vaulted PayPal wallet (needs SHOPPER_VAULT_TOKEN); card: sandbox test card")
    c.add_argument("--ship", action="store_true", help="mark shipped (India Post tracking, posted to PayPal /track)")
    c.add_argument("--deliver", action="store_true", help="mark delivered to the transaction address")
    c.add_argument("--deliver-elsewhere", action="store_true", help="mark delivered to a different address")
    c.add_argument("--signature", action="store_true", help="delivery was signed for")
    c.add_argument("--size-shipped", default=None, help="size actually shipped, if different from the order")
    c.add_argument("--refund", type=float, default=None, help="refund this amount (USD)")
    c.add_argument("--return-delivered", action="store_true", help="the buyer's return reached the shop")
    c.add_argument("--chargeback", nargs="?", const=True, default=None, metavar="CODE", help="open a chargeback dispute (needs CHARGEBACK_ENABLED)")
    e = sub.add_parser("export")
    e.add_argument("--orders", required=True)
    x = sub.add_parser("export-run")
    x.add_argument("run_id", type=int)
    x.add_argument("name")
    a = ap.parse_args(argv)
    store.init_default()
    if not store.one("SELECT sku FROM products LIMIT 1"):
        shop.load_seed()
    if a.cmd == "order":
        try:
            out = order(a.sku, a.size, pay=a.pay, ship=a.ship, deliver=a.deliver, deliver_elsewhere=a.deliver_elsewhere, signature=a.signature, size_shipped=a.size_shipped,
                         refund=a.refund, return_delivered=a.return_delivered, chargeback=a.chargeback, poll=True)
        except config.ApiErr as e:
            raise SystemExit(e.body.get("error", "failed"))
        print(json.dumps(out))
    elif a.cmd == "export":
        export_orders([x.strip() for x in a.orders.split(",") if x.strip()])
    else:
        export_run(a.run_id, a.name)


if __name__ == "__main__":
    main()
