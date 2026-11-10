"""Rules pre-check: Seller Protection coverage, legal actions and warnings. No LLM."""
from dataclasses import dataclass, field
from typing import Literal

from . import config, facts as F

ADVISORY_ACTIONS = ["provide_evidence", "make_offer", "accept_claim", "send_message"]  # shown as advice when PayPal offers none
ACTIONS = ADVISORY_ACTIONS + ["provide_supporting_info"]
LINKS = {"provide-evidence": "provide_evidence", "make-offer": "make_offer", "accept-claim": "accept_claim", "send-message": "send_message",
         "provide-supporting-info": "provide_supporting_info"}
ADVISORY = {
    "WAITING_FOR_SELLER_RESPONSE": "PayPal hasn't opened seller actions on this case yet. They usually appear a few minutes after a dispute opens.",
    "UNDER_REVIEW": "PayPal is reviewing this case; no seller action is possible now.",
    "RESOLVED": "This case is resolved.",
}
PAYS_BUYER = ("make_offer", "accept_claim")
PAID = "{}; a refund offer or accepting the claim could pay the buyer twice, so neither is offered."


def _paid_already(v, snapshot: dict, amount: float) -> str | None:
    """Why the buyer may already have the money back, or None. An unknown refund state counts as paid (fail safe)."""
    if any(str(e.get("call", "")).startswith("GET /v2/payments/refunds/") for e in snapshot.get("errors") or []):
        return "A refund on this order could not be read"
    cur = (v("pp.dispute.amount") or "0 USD").split()[-1]
    if any(rf.get("status") in F.REFUND_COUNTED and (rf.get("amount") or {}).get("currency_code") != cur for rf in snapshot.get("refunds") or []):
        return "A refund is in a different currency from the dispute"
    if v("pp.capture.status") == "REFUNDED":  # also catches refunds made outside the shop
        return "PayPal shows the payment as refunded"
    refunded = v("check.refunded_total")
    if amount > 0 and refunded and float(refunded.split()[0]) >= amount - 0.005:
        return f"Already refunded in full ({refunded})"
    return None


@dataclass
class Precheck:
    coverage: Literal["covered", "not_covered", "unknown"]
    coverage_rule: str
    hours_left: float | None
    legal_actions: list[str]
    advisory: bool
    advisory_text: str | None
    offer_types: list[str] = field(default_factory=list)
    accept_types: list[str] = field(default_factory=list)
    sandbox_calls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _coverage(v, A) -> tuple[str, str]:
    """First matching rule wins (§6). v(id) returns a fact value or None; A is check.address_match."""
    reason = v("pp.dispute.reason")
    if v("pp.dispute.channel") == "EXTERNAL":  # a card chargeback; PayPal's own (INTERNAL) escalations are not
        return "not_covered", "Card chargebacks are not covered by PayPal Seller Protection."
    if reason == "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED":
        return "not_covered", "Seller Protection does not cover not-as-described claims."
    if reason == "CREDIT_NOT_PROCESSED":
        return "not_covered", "Refund-not-processed is not a Seller Protection claim type (our reading)."
    if v("shop.product.kind") == "digital":
        return "not_covered", "Intangible items are not covered by Seller Protection."
    if v("pp.capture.seller_protection") == "NOT_ELIGIBLE":
        return "not_covered", "PayPal marks this payment not eligible."
    if (v("pp.dispute.protection") or "").startswith("not eligible"):
        return "not_covered", "PayPal marks this transaction not eligible for Seller Protection."
    if A == "no":
        return "not_covered", "Shipped to a different address than the one on the transaction."
    if reason == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED":
        if v("shop.shipment.delivered_at") is None and v("shop.shipment.shipped_at") is None and v("shop.shipment.tracking") is None:
            return "not_covered", "No proof of shipment."
        if v("shop.shipment.delivered_at") == "not delivered":
            return "unknown", "Tracking shows no delivery yet."
        if A == "yes":
            return "covered", "Delivered to the address on the transaction (international shipments need proof of delivery)."
        return "unknown", "Delivered, but the delivery address can't be checked."
    if reason == "UNAUTHORISED":
        shipped, created = config.parse_ts(v("shop.shipment.shipped_at")), config.parse_ts(v("pp.dispute.created"))
        if shipped and A == "yes" and created and (shipped - created).total_seconds() <= 2 * 86400:
            return "covered", "Shipped to the transaction address with proof of shipment."
        return "unknown", "Can't confirm shipment to the transaction address in time."
    return "unknown", "No rule matched."


def precheck(facts: dict, snapshot: dict) -> Precheck:
    d = snapshot.get("dispute") or {}
    v = lambda i: facts[i].value if i in facts else None  # noqa: E731
    present, sandbox = set(), []
    for l in d.get("links") or []:
        seg = str(l.get("href", "")).split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if seg in LINKS:
            present.add(LINKS[seg])
        elif seg in ("require-evidence", "adjudicate") and seg.replace("-", "_") not in sandbox:
            sandbox.append(seg.replace("-", "_"))
    legal = [a for a in ACTIONS if a in present]
    advisory, text = False, None
    if not legal:
        advisory, legal = True, list(ADVISORY_ACTIONS)
        status = v("pp.dispute.status") or "unknown"
        text = ADVISORY.get(status, f"No seller actions are available in status {status}.")
    amount = float(v("pp.dispute.amount").split()[0]) if v("pp.dispute.amount") else 0.0
    warn = []
    # Rules before models: when the buyer may already have the money back, the model never sees a move that pays again.
    if (why := _paid_already(v, snapshot, amount)) and any(a in legal for a in PAYS_BUYER):
        legal = [a for a in legal if a not in PAYS_BUYER]
        if legal:
            warn.append(PAID.format(why))
        else:  # PayPal offered only paying moves: nothing to send, explain why
            advisory, text, legal = True, PAID.format(why), [a for a in ADVISORY_ACTIONS if a not in PAYS_BUYER]
    aro = d.get("allowed_response_options") or {}
    mo, ac = aro.get("make_offer") or {}, aro.get("accept_claim") or {}
    offer = [] if "make_offer" not in legal else (list(mo.get("offer_types") or []) or ["REFUND"])
    if "accept_claim" not in legal:
        accept = []
    elif ac.get("accept_claim_types"):
        accept = [t for t in ac["accept_claim_types"] if t in ("REFUND", "PARTIAL_REFUND")]
    else:
        accept = ["REFUND"]
    cov, rule = _coverage(v, v("check.address_match"))
    hours = float(v("check.hours_left")) if v("check.hours_left") is not None else None
    if F.incomplete(snapshot.get("errors")):
        warn.append("Incomplete PayPal facts")
    if snapshot.get("order") and not (snapshot.get("shop") or {}).get("order"):
        warn.append("No shop record for this order (shipping and listing facts missing)")
    if amount > 750.0 and v("shop.shipment.signature") == "no":
        warn.append("Over $750 without signature confirmation.")
    if hours is not None and 0 <= hours < 48:
        warn.append("Under 48 hours to respond.")
    if hours is not None and hours < 0:
        warn.append("The response deadline has passed.")
    return Precheck(cov, rule, hours, legal, advisory, text, offer, accept, sandbox, warn)
