# PRD: Both Sides, a PayPal dispute desk that argues the buyer's case first

Working title "Both Sides"; the demo shop is the fictional "Northpace Running". Both names are placeholders (open question Q1).
Written 4 Nov 2026 with `/eg-prd` (standard depth, web research). Brainstorm: [brainstorm-2026-11-04.md](brainstorm-2026-11-04.md).

## Executive summary

Small PayPal sellers get a dispute email, a deadline, and no idea what evidence counts. Most either refund in a panic or fight cases they should settle.

Both Sides reads the dispute and the facts from PayPal and the shop's own records. A buyer advocate agent works out why this buyer filed. A merchant advocate answers it. A judge turns their record-backed claims into one action: send evidence, offer a refund, accept the claim, or message the buyer. The merchant edits and approves, and the action goes to PayPal's Disputes API.

A 12-case bench measures the desk against a single-prompt baseline. An agentic layer lets the agents settle small cases alone inside a refund limit the merchant sets once. It also handles disputes over purchases made by an AI shopping agent.

## Problem statement

A dispute is a timed argument where the seller rarely sees the other side. The buyer's message mixes facts ("I paid $96") with claims ("it never arrived"). The seller has to work out which claims the records support, what the buyer actually wants, and whether fighting is worth the fee.

Existing chargeback tools package evidence for one side and sell on win rate. None of them shows the buyer's likely reasoning, separates claims from facts on screen, or recommends settling when settling is cheaper.

## Target users

- **Primary: a one-person online seller on PayPal Checkout.**
  - Sells physical goods (running shoes) and some digital goods (a downloadable training plan).
  - Handles 20-200 orders a month, with no chargeback team.
  - **Trigger:** a `CUSTOMER.DISPUTE.CREATED` email and a response deadline.
  - **Job to be done:** "Tell me what really happened, what the buyer wants, and the cheapest defensible response, before the deadline."
- **Secondary: a shopper who delegates purchases to an AI agent.**
  - They need a fair outcome when the agent bought the wrong thing.
  - The dispute desk must tell an agent misreading the instruction apart from a merchant error.

## Current state

Greenfield repo at `Programming/paypal-ai-hackathon`. Nothing is reused from other projects.

Verified PayPal sandbox capabilities (sources at the end):

- The Disputes API v1 has these seller actions: `provide-evidence`, `make-offer`, `accept-claim`, `send-message`. Each is legal only when its HATEOAS link is present and the status is `WAITING_FOR_SELLER_RESPONSE`.
- Sandbox-only simulation endpoints:
  - `adjudicate` and `require-evidence`, both only from `UNDER_REVIEW`;
  - `process-chargeback`, which needs only the merchant token;
  - buyer-side `/v1/customer/disputes`, which needs the `DISPUTE_CREATE` scope, and only a PayPal account manager can grant it.
- A buyer can open INR/SNAD cases by hand from the sandbox personal account.
- Seller actions appear "a few minutes" after a dispute is created.
- A transaction can only ever have one dispute.
- PayPal Agent Toolkit (Python, Apache-2.0) reads disputes, orders, tracking and transactions. Its only dispute write is `accept_dispute_claim`.
- Vaulting a PayPal wallet (Vault v3) is limited-release and may need billing-agreement approval.
- Transaction Search lags by up to 3 hours, so it is not used.

## Proposed solution

Five parts, in the order a case flows through them:

1. **Facts.** Every fact gets an id and a stored raw response, for example `pp:dispute:PP-D-123#seller_response_due_date`, `pp:capture:…`, `pp:track:…`, `shop:listing:sku-201`, `shop:policy:returns`, `shop:ship:…`, `shop:mandate:…`.
   - PayPal reads go through the Agent Toolkit where it has the call, and direct REST where it doesn't.
   - The buyer's text is never a fact.
2. **Quarantine and rules.**
   - One extraction step reads the buyer's messages as delimited, datamarked data. It outputs only `{stated_reason, requested_outcome, alleged_facts[]}`. Later agents see these as claims to test.
   - A deterministic pre-check computes:
     - seller-protection coverage (INR and unauthorised are covered; SNAD and digital goods are not);
     - the deadline from `seller_response_due_date`;
     - the legal actions from HATEOAS links and `allowed_response_options`.
3. **Debate.**
   - **Buyer advocate:** for each alleged fact, is it supported, contradicted or unknown by the records? Then, why would this buyer plausibly have filed, and what outcome would be fair to them? Every hypothesis cites fact ids.
   - **Merchant advocate:** answers the buyer advocate's claims and proposes an action.
   - **Format:** one round, plus a second only if the proposed actions differ. Roles are fixed and there is no instruction to agree.
   - **Deadlock:** each side names the one fact that would decide it. That fact becomes a **Deadlock Card** the merchant can answer.
4. **Judge and validators.**
   - Code drops any claim whose evidence ids don't resolve, or whose cited value doesn't match the record (amount, date, tracking status).
   - The judge sees the surviving claims with anonymised, shuffled sides, and runs twice in swapped order. If the two runs disagree, the result is a Deadlock Card instead of a verdict.
   - The judge outputs an action from the legal set, an amount, an evidence plan (fact ids mapped to PayPal evidence types) and the message text.
   - Code validates the output against the legal actions, the dispute amount and the settlement envelope.
5. **Human gate and PayPal write.**
   - The merchant edits, approves or rejects. Approval calls the Disputes API, and every call is logged with the PayPal debug id.
   - Inside an active envelope, `make-offer` and `send-message` may run without approval. They are logged and the merchant is notified.
   - A sandbox panel can push the case to a ruling with `require-evidence` / `adjudicate`.

Around the core:

- **Storefront.** PayPal JS SDK checkout, Orders v2, and tracking via `/v2/checkout/orders/{id}/track`.
- **Bench.**
- **Shopping agent.** Takes a shopper's instruction, buys from the storefront, and stores the instruction as a hashed shop record.
- **Replay mode.** Plays back stored debates.

## Scope

**In**
- Dispute reasons: INR, SNAD, UNAUTHORISED, CREDIT_NOT_PROCESSED. All four seller actions.
- Fact sources: PayPal plus shop records.
- Storefront, seeding script, bench of 12 cases, settlement envelope, shopping agent.
- Hosting: Render, plus local docker-compose.
- LLMs: local Qwen and a hosted fallback.

**Out**
- Live PayPal and real money.
- Appeals and escalation to claim (`escalate`, `appeal`).
- Webhooks; the app polls instead.
- Multi-merchant accounts, auth beyond a single demo merchant, mobile apps.
- PayPal "Agent Ready" / "Store Sync", since there is no sandbox path.
- Any real brand other than PayPal.

## User stories

1. As a seller, I see every open dispute with its reason, amount, deadline countdown and status, so I know what is urgent.
2. As a seller, I open a case and see the facts, each with its source, next to the buyer's claims marked "untested", so I can tell what is proven.
3. As a seller, I read why this buyer probably filed, so I can respond to the real problem.
4. As a seller, I get one recommended action with the drafted text and the evidence plan, and I can edit it before it goes to PayPal.
5. As a seller, when the agents can't decide, I'm asked for the one fact that would settle it.
6. As a seller, I set a refund limit once, and small fair cases settle without me.
7. As a shopper, I tell an agent what to buy. If it goes wrong, the dispute is judged against my instruction.
8. As a judge of this hackathon, I can open a hosted demo, create a fresh case, run it, and see the PayPal state change.

## Functional requirements

1. **FR1** The inbox lists disputes from `GET /v1/customer/disputes`. Each row shows the reason, amount, stage, status and `seller_response_due_date` countdown, refreshed by polling at most once a minute.
2. **FR2** The case view shows a facts panel. Each fact has an id, a value, a source chip and a "receipt" that opens the stored raw PayPal or shop response.
3. **FR3** The buyer's dispute text and messages are only ever passed to the quarantine step, wrapped as data. No other prompt contains them verbatim.
4. **FR4** The rules pre-check outputs coverage (`covered` / `not_covered` / `unknown`, with the rule that fired), the deadline, and the legal actions and offer types. It makes no LLM call.
5. **FR5** The buyer advocate and merchant advocate each return JSON claims: `{id, text, side, kind: fact|inference, evidence_ids[], confidence}`. Output that doesn't parse is retried once, then the step shows an error.
6. **FR6** The claim verifier drops a claim if any of these hold:
   - an evidence id doesn't resolve;
   - a `fact` claim cites no id;
   - a cited amount, date or tracking status doesn't match the record.

   Dropped claims are shown struck out, with the reason.
7. **FR7** A second round runs only when the advocates propose different actions. There are at most 2 rounds.
8. **FR8** The judge runs twice, with side labels anonymised and order swapped. If the actions differ, the result is a Deadlock Card naming the deciding fact(s), with an input for the merchant. The merchant's answer is stored as a `shop:merchant_note:` fact and the case re-runs.
9. **FR9** The judge's output is valid only if:
   - the action is in the legal set;
   - the offer or accept type is in `allowed_response_options`;
   - the amount is at most the dispute amount;
   - every evidence item maps to a resolvable fact.

   Anything else is rejected before the merchant sees it.
10. **FR10** The merchant can edit the message, amount and evidence selection, then approve or reject. Approval sends exactly one Disputes API call and records the request, response, debug id, user and time.
11. **FR11** `provide-evidence` sends multipart: `PROOF_OF_FULFILLMENT` with `tracking_info` where tracking exists, and other evidence as `notes`, or as PDF/PNG under 10 MB.
12. **FR12** The settlement envelope holds `max_offer_amount`, `max_refund_pct`, a daily cumulative cap, the allowed actions (`make-offer` REFUND, `send-message`), the allowed reasons, `expires_at` and a hash.
    - Inside the envelope, the action runs without approval; outside it, it goes to the human.
    - The server checks the envelope before every call, and every use writes an append-only log row.
13. **FR13** The storefront sells 5-6 fictional products (shoes plus one digital plan) through PayPal JS SDK buttons (Orders v2 create and capture). The shop can add tracking with `/v2/checkout/orders/{id}/track`.
14. **FR14** The seeding script:
    - creates card-funded sandbox orders, captures them and adds tracking per case;
    - creates chargeback cases with `process-chargeback`;
    - writes a manifest of the cases that need a manual buyer-side dispute.
15. **FR15** The shopping agent takes an instruction (for example "black road shoes, size 10, under $120"), picks a product from the shop catalogue, pays, and stores the instruction as `shop:mandate:` with a hash.
    - Payment order of preference: a vaulted PayPal wallet, then a vaulted sandbox card, then an order the shopper approves in the PayPal popup.
    - The judge's output for these cases includes a fault split: agent misread / merchant error / buyer changed mind.
16. **FR16** The bench runs 12 recorded cases (3 per reason, including 1 injection attempt and 1 agent-purchase case) through the desk and the single-prompt baseline.
    - The baseline gets the same facts, quarantined claims and output schema.
    - Results report counts, not percentages: action accuracy against the expected action written before any run, flipped-claim changes, and claims checked vs failed.
    - Per-case traces are kept.
17. **FR17** The bench runs offline on recorded fact snapshots, so judges can reproduce it without PayPal access.
18. **FR18** Replay mode plays any stored debate with original timing. It is used for the video and when the LLM is unavailable.
19. **FR19** The sandbox panel shows the legal simulation calls (`require-evidence`, `adjudicate`) and the resulting status.
20. **FR20** The hosted demo has a "New dispute" button that creates a fresh chargeback case when that path works, or picks the next unused case from the pool. It also has a per-IP rate limit on LLM runs and a "waking up" screen.

## Non-functional requirements

- **Performance:** measure draft time (case opened to verdict) for local Qwen3-4B and the hosted model. No target is set until it's measured. Hosted mode uses one round to stay inside free-tier limits.
- **Security:**
  - Secrets go only in `.env` (local) or Render env vars, never in the repo or logs. Agents hold no credentials; code makes every PayPal call.
  - CORS is limited to the app's origin. The reset and new-case buttons are rate-limited.
  - Buyer emails and addresses are masked in logs and UI, and raw responses are only in the receipts drawer.
- **Reliability:** each step shows its status, with the PayPal error and debug id on failure and a one-click retry. A 429 from the LLM falls back to the next provider, then to replay.
- **Hosting:** Render free web service (Docker). The disk is ephemeral, so on boot the app rebuilds state from PayPal (dispute list) and from files in the repo (bench snapshots, replays). It must keep working through the judging period, which ends 15 Dec 2026.
- **Accessibility:** keyboard reachable, visible focus, colour is never the only signal (chips carry text), WCAG AA contrast.
- **Content:** English only, no third-party brands, royalty-free or no music in the video. PayPal appears only through its official buttons and sandbox screens.

## Success metrics

| Metric | Target | How measured |
|---|---|---|
| Action accuracy, desk vs baseline | Desk beats baseline on the 12-case bench. Real counts are reported either way, and if the desk loses, the design changes before the video. | Bench, temperature 0, then 3 repeats at temperature 0.6 for min/median/max |
| Unsupported claims reaching PayPal | 0 | The validator blocks them. Claims checked vs dropped are reported per case |
| Flipped-claim stability | Reported (no target) | Pre-written persuasive rewrite of each buyer text, counted as action changes out of 12 |
| End-to-end live demo | Works | In the video: storefront purchase, dispute, debate, approval, PayPal status change, ruling |

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Buyer-side dispute API needs the `DISPUTE_CREATE` scope | High | Bench can't be scripted | Use `process-chargeback` (merchant token) for scripted cases, plus manual buyer-side cases from a manifest. Tested on day 1 |
| Vaulting needs billing-agreement approval | Medium | Shopping agent can't pay alone | Fall back to a vaulted card, then to a popup approval. Tested on day 1 |
| Debate doesn't beat one prompt (the literature is mixed) | Medium | Headline claim fails | Fair baseline. The desk's edge is structural (code-verified claims, quarantine, swapped-order judge). Report honestly and fix before the video |
| Free-tier LLM limits during judging (1-15 Dec) | Medium | Hosted demo fails for judges | Precomputed bench and replays, 1-round hosted mode, provider chain, per-IP cap. Check the console limits |
| Small local model breaks JSON | Medium | Steps fail | Schema-constrained decoding in llama-server, thinking off, one retry |
| Render spin-down and ephemeral disk | High | Slow first load, lost local state | Rebuild at boot, "waking up" screen, no state that matters lives only on disk |
| Seller actions appear minutes late | High | Demo stalls | Gate on HATEOAS links. Seed cases hours before recording |
| Time: about 40 h of scope | High | Late or rushed | Cut in this order: shopping agent, envelope, flipped-claim set, round 2 |
| Prompt injection in buyer text | Medium | Wrong action | Quarantine, validator, human gate, and an injection case in the bench |

## Implementation hints

- **Layout:** `backend/` (FastAPI: `paypal.py`, `facts.py`, `rules.py`, `agents.py`, `verify.py`, `envelope.py`, `shop.py`, `bench/`, `store.py`), `frontend/` (React + Vite: Inbox, Case, Shop, Bench, Envelope, Shopper), `seed/`, `docs/`. One Dockerfile, with FastAPI serving the built frontend. `render.yaml`.
- **LLM:** one OpenAI-compatible client with `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`.
  - Local: `llama-server` with Qwen3-4B GGUF (Q5_K_M or Q6_K), JSON schema output, thinking off.
  - Hosted: Groq `json_schema` models, then Gemini's OpenAI-compatible endpoint.
- **Agent loop:** plain Python, no framework. PayPal reads go through `paypal-agent-toolkit` (`handle_tool_call`) and are labelled "PayPal tool call" in the UI. Writes go through our own httpx client behind the human gate.
- **Tests first:**
  - Unit tests on recorded PayPal JSON fixtures, with no network: rules, the verifier, the validator, envelope checks and fact building.
  - One opt-in live smoke test against the sandbox.
- **Day-1 spikes, in order:**
  1. OAuth and listing disputes;
  2. card-funded capture plus `process-chargeback`;
  3. a manual buyer-side INR case;
  4. the vault toggle;
  5. toolkit `handle_tool_call` with Qwen;
  6. `llama-bench` on Qwen3-4B.
- **Milestones (IST):**
  - Thu 5 Nov: spikes, then facts and rules.
  - Fri 6 to Sat 7: agents, verifier, case UI, approval flow.
  - Sun 8: bench.
  - Mon 9: envelope and shopping agent.
  - Tue 10: freeze, README, architecture note, Render deploy.
  - Wed 11: video and Devpost text.
  - Submitted by Thu 12 Nov 01:30 IST, 24 hours before the deadline.

## Open questions

- **Q1.** Product and shop names. "Both Sides" and "Northpace Running" are placeholders.
- **Q2.** Does `process-chargeback` work on a card-funded capture, and what dispute reason does each processor reason code map to? This decides how many bench cases can be scripted. Day-1 spike.
- **Q3.** Can UNAUTHORISED and CREDIT_NOT_PROCESSED cases be created in the sandbox at all? If not, the bench uses chargeback reason codes for them, or drops to 2 reasons and 12 cases split differently.
- **Q4.** Is vaulting a PayPal wallet available on this sandbox account? Day-1 spike.
- **Q5.** The real Groq and Gemini free limits for this account. Riteesh checks in the consoles (needs login).
- **Q6.** If the free tiers can't carry judging traffic, is a small paid key acceptable?
- **Q7.** The exact Seller Protection wording ($750 signature rule, the 2-day rule for unauthorised claims), to read on paypal.com before encoding the rules.
- **Q8.** PayPal brand guidance for showing PayPal in the video beyond the official buttons.

## Sources

- PayPal disputes testing: https://developer.paypal.com/disputes/test-go-live.md
- PayPal disputes setup (buyer JWT, scopes): https://developer.paypal.com/disputes/set-up.md
- Disputes API reference: https://developer.paypal.com/docs/api/customer-disputes/v1/ (schema https://developer.paypal.com/api/customer-disputes/v1/schema.json)
- Orders v2 schema (track): https://developer.paypal.com/api/orders/v2/schema.json
- Vault, save without purchase: https://developer.paypal.com/api/payment-tokens/save-without-purchase/paypal
- Vault limited release: https://developer.paypal.com/limited-release/vault-payment-methods/vault-api/
- Transaction Search schema (3-hour lag): https://developer.paypal.com/api/transaction-search/v1/schema.json
- Agent Toolkit: https://github.com/paypal/agent-toolkit; MCP server: https://github.com/paypal/paypal-mcp-server, https://developer.paypal.com/ai-tools/mcp-server.md
- PayPal on AP2 mandates: https://developer.paypal.com/community/blog/PayPal-Agent-Payments-Protocol/
- PayPal agentic commerce services (Oct 2025): https://newsroom.paypal-corp.com/2025-10-28-PayPal-Launches-Agentic-Commerce-Services-to-Power-AI-Driven-Shopping
- Seller Protection: https://www.paypal.com/us/legalhub/paypal/seller-protection (to read in a browser, Q7)
- Debate failure modes: https://arxiv.org/abs/2509.23055, https://arxiv.org/html/2509.05396v2; debate helping judges: https://arxiv.org/abs/2402.06782; judge bias: https://arxiv.org/html/2306.05685v4
- Spotlighting (prompt injection): https://arxiv.org/abs/2403.14720; OWASP LLM01: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- Eval error bars: https://arxiv.org/abs/2411.00640
- Qwen3 GGUF: https://huggingface.co/bartowski/Qwen_Qwen3-4B-GGUF; Qwen on llama.cpp: https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html
- Groq structured outputs: https://console.groq.com/docs/structured-outputs; Gemini OpenAI compatibility: https://aistudio.google.com/docs/openai
- Stripe dispute UX: https://docs.stripe.com/disputes/responding
- Render free tier: https://render.com/docs/free
- Hackathon rules (judging equally weighted; judging 1-15 Dec 2026; license visible in About): https://paypalaihackathon.devpost.com/rules

## Out-of-scope follow-ups

- Webhooks instead of polling.
- Appeals and escalation.
- A pre-dispute risk feed (run the buyer advocate on orders before any dispute).
- A root-cause grid across disputes (AG Grid).
- Multi-merchant onboarding.
- A different model per role, which the literature suggests helps debate.
