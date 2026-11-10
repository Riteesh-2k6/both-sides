"""Pure checks: claim verification, verdict validation, and the settlement envelope."""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import facts as F, shop
from .agents import INSTRUCTION_RE

NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
NEG_DELIVERED = re.compile(
    r"\bun(?:delivered|received)\b"
    r"|(?:\b(?:not|never|no)|n't)\b(?:\W+\w+){0,3}?\W+(?:delivered|received|arrived|got)\b"
    r"|(?:\bdid\s*not|didn't)\W+(?:\w+\W+){0,2}?(?:arrive|come)\b", re.I)
DELIVERED = re.compile(r"\bdelivered\b", re.I)
REFUNDED = re.compile(
    r"\b(?:already\s+refunded|(?:we|i)\s+(?:have\s+)?refunded|(?:has|have|had|was|were)\s+(?:been\s+)?refunded"
    r"|refund(?:ed)?\s+(?:was\s+|has\s+been\s+)?(?:issued|sent|completed|processed))\b", re.I)
# outgoing sentences are ours: only prompt-injection shapes are refused ("We accept the claim." is a legitimate sentence)
OUTGOING_INSTRUCTION_RE = re.compile(
    r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(instruction|instructions|rules|prompt|above|previous|prior)\b"
    r"|\b(system prompt|you are now|act as|new instructions)\b", re.I)
COURTESY_FACT = re.compile(r"\b(delivered|received|shipped|refund\w*|tracking|arrived)\b", re.I)
NEG_REFUNDED = re.compile(
    r"(?:\b(?:not|never|no)|n't)\b\W+(?:\w+\W+){0,3}?(?:refunded|refund(?:ed)?\s+(?:was|has\s+been|is)\s+(?:issued|sent|processed|completed))\b", re.I)
BARE_DOMAIN_RE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|app|xyz|info|biz|me|ly|link|shop|store|online|site|dev)\b(?:/\S*)?", re.I)
URL_RE = re.compile(r"https?://|www\.", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
AMOUNT_OFFER_TYPES = ("REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT")


def _text_problem(text: str, ids: list[str], facts: dict, status: bool = True) -> str | None:
    """Rules 3 and 4 of §8.1 against the facts the text cites (ids are known to exist).
    status=False skips rule 4 (delivered/refunded keywords) - used for inferences about what someone believes."""
    allowed = [float(n.replace(",", "")) for n in NUM_RE.findall(" ".join(facts[i].value for i in ids))]
    for raw in NUM_RE.findall(text):
        n = float(raw.replace(",", ""))
        if not any(abs(n - a) <= 0.005 for a in allowed):
            return f"number {raw} not in cited facts"
    return _status_problem(text, facts, ids) if status else None


def _status_problem(text: str, facts: dict, ids=None) -> str | None:
    """Delivered / refunded keywords against the facts. ids=None checks the whole facts dict (outgoing messages), else only the cited ones."""
    neg = bool(NEG_DELIVERED.search(text))
    if "shop.shipment.delivered_at" in facts and (ids is None or "shop.shipment.delivered_at" in ids):
        not_delivered = facts["shop.shipment.delivered_at"].value == "not delivered"
        if (neg and not not_delivered) or (not neg and DELIVERED.search(text) and not_delivered):
            return "contradicts shop.shipment.delivered_at"
    if "check.refunded_total" in facts and (ids is None or "check.refunded_total" in ids):
        nothing_refunded = facts["check.refunded_total"].value.startswith("0.00 ")
        negated = bool(NEG_REFUNDED.search(text))  # "No refund was issued." is a true statement when nothing was refunded
        if (REFUNDED.search(text) and not negated and nothing_refunded) or (negated and not nothing_refunded):
            return "contradicts check.refunded_total"
    return None


_FACT_ID = r"(?:pp|shop|check|merchant)\.[a-z0-9_.]+"
_ID_LABEL = r"(?:(?:fact(?:_?ids?)?|facts|evidence|sources?)\s*:\s*)?"
FACT_ID_LIST_RE = re.compile(rf"\s*[(\[]\s*{_ID_LABEL}{_FACT_ID}(?:\s*,\s*{_FACT_ID})*\s*[)\]]", re.I)
CUT_ID_LIST_RE = re.compile(rf"\s*[(\[]\s*{_ID_LABEL}(?:pp|shop|check|merchant)\.[^)\]]*$", re.I)  # unclosed, at the end
TAIL_ID_LIST_RE = re.compile(rf"\s*\bfacts?[ _]?ids?\s*:\s*{_FACT_ID}(?:\s*(?:,|and)\s*{_FACT_ID})*\s*$", re.I)  # "... Fact IDs: a, b"


def _strip_fact_ids(text: str) -> str:
    """Remove '(pp.x, shop.y)' / '[pp.x]' lists the model wrote into a sentence; ids belong in fact_ids, not in what PayPal sees."""
    for rx in (FACT_ID_LIST_RE, CUT_ID_LIST_RE, TAIL_ID_LIST_RE):
        text = rx.sub("", text)
    return re.sub(r"\s+([.,;!?])", r"\1", text).strip()


def _has_link(text: str) -> bool:
    """A URL or a bare domain such as pay.example.shop. The shop's own name is not a domain."""
    return bool(URL_RE.search(text) or BARE_DOMAIN_RE.search(re.sub(re.escape(shop.SHOP_NAME), "", text, flags=re.I)))


def _unknown(ids, facts):
    return next((i for i in ids if i not in facts), None)


BELIEF_RE = re.compile(r"\b(believ\w*|think\w*|thought|feel\w*|felt|says?|said|claim\w*|report\w*|assum\w*|expect\w*|suspect\w*|worr\w*)\b", re.I)


def verify_claims(claims, facts: dict):
    kept, dropped = [], []
    for c in claims:
        if not c.evidence_ids:
            dropped.append((c, "no evidence cited"))
        elif (u := _unknown(c.evidence_ids, facts)) is not None:
            dropped.append((c, f"cites unknown fact {u}"))
        # An inference worded as someone's belief may differ from the record ("the buyer believes it never arrived");
        # the kind label is the model's, so the wording decides. Numbers must still match, and outgoing message
        # sentences still get the full delivered/refunded check.
        elif (why := _text_problem(c.text, c.evidence_ids, facts,
                                   status=not (c.kind == "inference" and BELIEF_RE.search(c.text)))):
            dropped.append((c, why))
        else:
            kept.append(c)
    return kept, dropped


@dataclass
class Validation:
    ok: bool
    errors: list[str]
    warnings: list[str]
    dropped_sentences: list[tuple[str, str]]
    message_text: str
    dropped_rationale: list[tuple[str, str]] = field(default_factory=list)
    kept_sentences: list[dict] = field(default_factory=list)  # {text, fact_ids, courtesy} in send order; message_text is their texts joined by a space


_BACKING = {  # evidence type -> predicate(fact_id, facts)
    "PROOF_OF_FULFILLMENT": lambda i, f: (i.startswith("shop.shipment.") or i == "pp.order.tracking") and "shop.shipment.tracking" in f and "shop.shipment.carrier" in f,
    "PROOF_OF_DELIVERY_SIGNATURE": lambda i, f: i == "shop.shipment.signature" and f[i].value == "yes",
    "PROOF_OF_REFUND": lambda i, f: (i.startswith(("shop.refund.", "pp.refund."))) and any(re.fullmatch(r"shop\.refund\.\d+\.id", k) for k in f),
    "ITEM_DESCRIPTION": lambda i, f: i.startswith("shop.product.") or i in ("shop.order.size", "shop.shipment.size_shipped"),
    "RETURN_POLICY": lambda i, f: i.startswith("shop.policy."),
    "OTHER": lambda i, f: True,
}


def validate_verdict(v, pre, facts: dict, snapshot: dict, message_override: str | None = None) -> Validation:
    errors, warnings = [], []
    dispute_cents = round(float(facts["pp.dispute.amount"].value.split()[0]) * 100) if "pp.dispute.amount" in facts else None
    if v.action not in pre.legal_actions:
        errors.append(f"action {v.action} is not legal for this dispute")
    if v.action == "make_offer" and v.type not in pre.offer_types:
        errors.append(f"offer type {v.type} is not allowed")
    if v.action == "accept_claim" and v.type not in pre.accept_types:
        errors.append(f"accept type {v.type} is not allowed")
    needs_amount = (v.action == "make_offer" and v.type in AMOUNT_OFFER_TYPES) or (v.action == "accept_claim" and v.type == "PARTIAL_REFUND")
    if needs_amount:
        cents = None if v.amount is None else round(v.amount * 100)
        if dispute_cents is None:
            errors.append("dispute amount unknown")
        elif cents is None or cents <= 0 or cents > dispute_cents:
            errors.append("amount is missing or outside (0, dispute amount]")
    if v.action in ("provide_evidence", "provide_supporting_info"):
        if v.action == "provide_evidence" and not v.evidence:
            errors.append("provide_evidence needs at least one evidence item")
        for e in v.evidence:  # supporting info may carry evidence items: their fact ids must exist
            if e.fact_id not in facts:
                errors.append(f"evidence cites unknown fact {e.fact_id}")
            elif v.action == "provide_evidence" and not _BACKING[e.evidence_type](e.fact_id, facts):
                # supporting info goes to PayPal as notes only, so its evidence "type" carries no meaning there
                errors.append(f"fact {e.fact_id} cannot back {e.evidence_type}")
        req = facts["pp.dispute.requested_evidence"].value.split(", ") if "pp.dispute.requested_evidence" in facts else []
        if v.action == "provide_evidence" and req and v.evidence and not {e.evidence_type for e in v.evidence} & set(req):
            warnings.append(f"PayPal requested evidence ({', '.join(req)}) that this plan does not include")
    if v.amount is not None and ((v.action == "make_offer" and v.type == "REPLACEMENT_WITHOUT_REFUND") or (v.action == "accept_claim" and v.type == "REFUND")):
        warnings.append("The amount is ignored for this response type and will not be sent.")
    if v.fault is None and any(k.startswith("shop.mandate.") for k in facts):
        errors.append("fault is required when a shopper mandate exists")

    kept, dropped, courtesy = [], [], 0
    if message_override is not None:
        message_text = message_override[:2000].strip()
        if not message_text:
            errors.append("message is empty")
        else:
            kept.append((message_text, False, [], True))
            # the per-sentence support check is skipped for an edited message; links and injection shapes are still refused
            if URL_RE.search(message_text):
                errors.append("edited message contains a link")
            if BARE_DOMAIN_RE.search(re.sub(re.escape(shop.SHOP_NAME), "", message_text, flags=re.I)):
                errors.append("edited message contains a web address")
            if EMAIL_RE.search(message_text):
                errors.append("edited message contains an email address")
            if OUTGOING_INSTRUCTION_RE.search(message_text):
                errors.append("edited message contains instruction-like text")
            if (why := _status_problem(message_text, facts)):
                warnings.append(f"Your edited message contradicts the records: {why}")
    else:
        for s in v.message:
            t = _strip_fact_ids(s.text)
            why = None
            # A sentence that cites facts is checked as a fact sentence even if the model flagged it courtesy
            # (live hearing: the judge marked its tracking-number sentence courtesy and the digit rule dropped it).
            is_courtesy = s.courtesy and not s.fact_ids
            if not t:
                why = "empty after stripping fact ids"
            elif re.search(r"\bfact_?ids?\b", t, re.I) or any(fid in t for fid in facts):  # woven into the prose; cutting would garble it
                why = "fact id left in the text"
            elif _has_link(t) or EMAIL_RE.search(t) or OUTGOING_INSTRUCTION_RE.search(t):
                why = "link, email or instruction text"
            elif not is_courtesy:
                if not s.fact_ids:
                    why = "no fact cited"
                elif (u := _unknown(s.fact_ids, facts)) is not None:
                    why = f"cites unknown fact {u}"
                else:
                    why = _text_problem(t, s.fact_ids, facts)
                    if why is None:
                        why = _status_problem(t, facts)  # an outgoing sentence may not contradict delivery or refund facts, cited or not
            elif re.search(r"\d", t):
                why = "courtesy sentence has a digit"
            elif NEG_DELIVERED.search(t) or DELIVERED.search(t) or REFUNDED.search(t) or COURTESY_FACT.search(t):
                why = "courtesy sentence makes a factual claim"
            elif len(t.split()) > 20:
                why = "courtesy sentence over 20 words"
            elif courtesy >= 2:
                why = "third courtesy sentence"
            if why is None and len(kept) >= 6:
                why = "over 6 sentences"
            if why is None and len(" ".join([k[0] for k in kept] + [t])) > 2000:
                why = "over 2000 characters"
            if why:
                dropped.append((t or s.text.strip(), why))
                continue
            kept.append((t, is_courtesy, list(s.fact_ids), not s.courtesy or len(t.split()) > 6))
            courtesy += is_courtesy
        message_text = " ".join(k[0] for k in kept)
        # supported = a fact sentence; a greeting the model flagged courtesy does not count even if it cites an id
        if not any(not k[1] and k[3] for k in kept):
            errors.append("no supported sentences")
    _, bad = verify_claims(v.rationale, facts)
    return Validation(not errors, errors, warnings, dropped, message_text, [(c.text, why) for c, why in bad],
                      [{"text": t, "fact_ids": ids, "courtesy": c} for t, c, ids, _ in kept])


# ---------------- envelope ----------------

class Envelope(BaseModel):
    max_offer_amount: float = Field(allow_inf_nan=False)
    max_refund_pct: float = Field(allow_inf_nan=False)
    daily_cap: float = Field(allow_inf_nan=False)
    allowed_actions: list[Literal["make_offer", "send_message"]]
    allowed_reasons: list[str]
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, d):
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def envelope_hash(e: Envelope) -> str:
    return hashlib.sha256(json.dumps(e.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_envelope(e, stored_hash, v, pre, reason, dispute_cents, spent_today_cents, now, prior_auto, outcome, snapshot_errors, injection_suspected: bool = False) -> tuple[bool, str]:
    if e is None:
        return False, "no envelope"
    if envelope_hash(e) != stored_hash:
        return False, "envelope hash mismatch"
    if not now < e.expires_at:
        return False, "envelope expired"
    if v.action not in e.allowed_actions:
        return False, f"action {v.action} not allowed by the envelope"
    if v.action == "make_offer" and (v.type != "REFUND" or v.amount is None):
        return False, "only full-amount REFUND offers can be sent automatically"
    if reason not in e.allowed_reasons:
        return False, "dispute reason not allowed"
    cents = round(v.amount * 100) if v.action == "make_offer" else 0
    if cents > round(e.max_offer_amount * 100):  # everything in integer cents, so 19.99 against a 19.99 limit passes
        return False, "over the maximum offer"
    if cents > math.floor(e.max_refund_pct * dispute_cents / 100 + 1e-9):
        return False, "over the maximum refund percentage"
    if spent_today_cents + cents > round(e.daily_cap * 100):
        return False, "over the daily cap"
    if pre.coverage == "covered":
        return False, "Seller Protection covers this case"
    if pre.advisory:
        return False, pre.advisory_text or "PayPal has not opened seller actions"
    if prior_auto:
        return False, "this dispute already had an automatic action"
    if not (outcome or {}).get("agreed") or (outcome or {}).get("differences"):
        return False, "the two judge readings differ or left an open question"
    if F.incomplete(snapshot_errors):
        return False, "incomplete facts"
    if injection_suspected:
        return False, "buyer text looked like instructions"
    return True, "within settlement limits"
