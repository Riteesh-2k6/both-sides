# Brainstorm: PayPal AI Hackathon entry (4 Nov 2026)

Run with `/eg-brainstorm`. Five lens agents (technical, prior art, UX/demo, pre-mortem, adjacent/agentic) plus one "what they all missed" sweep.

**Decision:** build the dispute desk in three layers: the Evidence Court desk, a measured bench, and an agentic layer (settlement envelope + agent-purchase dispute). The agentic layer is the first thing cut if time runs short.

## Seed

A PayPal-sandbox dispute desk for merchants, run by three agents:

1. **Buyer advocate.** Pulls the facts from PayPal records first (transaction, order, tracking, message thread), never from the buyer's claim text. Then it takes the buyer's point of view: given those facts, why would they have filed?
2. **Merchant advocate.** Drafts the merchant's response from the same facts.
3. **Judge.** The two advocates debate until they agree or deadlock. The judge settles it, picks the action (provide evidence / make offer / accept claim) and writes the response. A human approves before it goes out through the Disputes API.

Facts come only from API records. The buyer's words are a claim to test.

## Concepts (deduplicated)

**A. Making the debate trustworthy**
- Evidence Court: the debate is made of claims tied to fields in PayPal records, and a claim with no record behind it is dropped. Red-team mode rewrites the buyer's text to check that the verdict doesn't move.
- Replayable Dispute Court: every line links to its API record; sandbox `adjudicate` closes the loop.
- Policy-as-code pre-judge: deterministic seller-protection checks run first, and only ambiguous cases go to the debate.
- Permission-split tools: the buyer advocate is read-only, and only the judge (behind the human gate) holds the write actions.

**B. The decision and its output**
- Settle-don't-fight: the judge weighs contest vs offer vs accept on fee, item cost and the strength of the facts.
- Evidence packet compiler: a per-reason checklist (present / missing / unobtainable), sent via `provide-evidence`.

**C. Product surface**
- Courtroom UI: three panes (facts with "from API" chips, debate, verdict). The buyer's text is greyed out as "claim, untested".
- Deadlock Card: when the advocates deadlock, both name the one fact that decides it; the merchant supplies it and the verdict updates.
- Webhook inbox with deadline clocks (`CUSTOMER.DISPUTE.*`).
- One-tap approval on a phone.

**D. Proof**
- Sandbox dispute bench: 10-15 seeded disputes with known expected actions, run against a one-prompt baseline, plus a flipped-claim stability test.
- Root-cause grid: pivot disputes by carrier, reason and SKU (an AG Grid fit).

**E. Agentic commerce**
- Settlement envelope: the merchant pre-authorises a range, the agents act alone inside it and escalate outside it.
- Mandate Court: a dispute over an AI-agent purchase, where the shopper's instruction to the agent is the key evidence. The judge splits fault between agent misread, merchant error and buyer's remorse.
- Pre-dispute risk feed / dry run.

## Agreement across lenses
- The differentiator is facts with receipts on screen next to the untested claim.
- Sandbox dispute creation is the riskiest dependency, so prove it first.
- Same-model debaters drift toward agreement, so the structure has to prevent it (claims tied to records, capped turns, a judge that checks).

## Disagreement
- Who the product is for: the merchant reacting to a dispute, or a buyer-side or pre-dispute agent?
- Measured bench or polished demo?
- How central agentic commerce should be.
- Webhooks or polling.

## Verified facts (PayPal docs, read 4 Nov 2026)
Source: https://developer.paypal.com/disputes/test-go-live.md
- Create a dispute (sandbox only): `POST /v1/customer/disputes`, multipart, needs a `PayPal-Auth-Assertion` JWT for the buyer. Alternatively, the buyer opens an INR or SNAD case from the sandbox personal account UI.
- Create a chargeback (sandbox only): `POST /v2/customer-support/process-chargeback` with just the merchant access token.
- Buyer simulation: `change-reason`, `accept-offer`, `deny-offer`.
- PayPal simulation: `adjudicate` (`BUYER_FAVOR` / `SELLER_FAVOR`, only from `UNDER_REVIEW`) and `require-evidence` (`BUYER_EVIDENCE` / `SELLER_EVIDENCE`).
- Seller actions depend on the HATEOAS links in the dispute JSON. Webhooks: `CUSTOMER.DISPUTE.CREATED`, `.UPDATED`, `.RESOLVED`.
- The official `@paypal/mcp` server's dispute tools are `list_disputes`, `get_dispute` and `accept_dispute_claim` (per its GitHub README, via a brainstorm agent; not re-checked).

## Sources
- https://developer.paypal.com/disputes/test-go-live.md
- https://github.com/paypal/paypal-mcp-server
- https://www.chargeflow.io/paypal-dispute-automation
- https://welcome.justt.ai/midigator-alternative
- https://arxiv.org/html/2509.23055v1 (premature agreement in multi-agent debate)
- https://justt.ai/blog/ai-agent-chargeback-liability/
- https://forkast.news/the-agentic-commerce-liability-gap-who-pays-when-the-agent-buys-wrong/
- https://www.newswire.com/news/ryde-explores-agentic-ai-to-reimagine-ride-hailing-dispute-resolution-at (closest architectural prior art: rider advocate, driver advocate and judge, for ride-hailing)
