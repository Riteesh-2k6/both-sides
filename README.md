# Both Sides

**A PayPal dispute desk that argues the buyer's case first.**

PayPal has handled only cross-border payments in India since 2021, so every Indian seller on PayPal is an exporter. When a buyer abroad opens a dispute, a small workshop gets a deadline, a buyer in another time zone, and a message it can't check.

Both Sides reads the dispute and the facts from PayPal and the shop's own records. A **buyer's advocate** agent works out why this buyer filed, and a **merchant's advocate** answers it. A **judge** turns the claims that survive fact-checking into one PayPal action. The seller edits and approves it, and it goes to PayPal's Disputes API. Small, fair cases can also settle on their own inside a refund limit the seller sets once.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com) (Nov 2026). Everything runs against the free PayPal **sandbox**. The demo merchant is an **India** business account (the fictional block-print workshop *Rangsaaz Prints*, Jaipur) and the buyer is a **US** account.

- **Demo:** _hosted URL (added at deploy)_
- **Video:** _YouTube link (added at submission)_
- **License:** MIT

## What it does

1. **Facts first.** Every PayPal fact carries a receipt, the PayPal response it came from. Shop facts come from the shop's own records. The buyer's own words are never facts. A quarantine step turns them into claims to test, and the buyer's advocate rates each one supported, contradicted or unverified against the facts.
2. **Rules before models.** Fixed checks run before any model call:
   - Seller Protection coverage;
   - the deadline;
   - which actions PayPal currently allows, read from PayPal's own links on the dispute.
3. **A hearing.**
   - **Buyer's advocate:** explains why this buyer most likely filed.
   - **Merchant's advocate:** answers with the cheapest defensible response, and settling counts as legitimate.
   - Code drops any claim that cites a fact that doesn't exist, or states a number or delivery or refund status the record contradicts.
4. **A judge that reads twice,** with the sides in swapped order. If the two readings disagree, there is no verdict. The seller gets a **Deadlock Card** asking for the one fact that would settle it.
5. **Human gate, then PayPal.**
   - Before anything can be sent, code checks the verdict against PayPal's allowed actions and the dispute amount, and strips any unsupported sentence.
   - When the seller approves, the server re-reads the dispute. If its status, stage or allowed actions changed since the hearing, it refuses.
   - Possible actions: `provide-evidence`, `make-offer`, `accept-claim`, `send-message`, or `provide-supporting-info` while PayPal reviews a claim.
6. **Settlement limits (agentic commerce).** The seller sets limits once: maximum refund, percentage, daily cap, reasons and expiry. Inside it, the agents may offer a refund or message the buyer without asking. They never do when Seller Protection would win, when facts are missing, when the two readings differ, or when the buyer's text looks like an injection attempt. Every use is logged.
7. **Agent purchases.** A shopping agent buys from the shop on a shopper's instruction (*"something indigo for my queen bed under $150"*) and pays with a saved PayPal wallet. The instruction is stored as evidence. If that purchase is disputed, the judge splits the fault between agent, merchant and buyer.
8. **A bench.** 12 synthetic cases, shaped on the live sandbox responses, compare the hearing with a single strong prompt (each run as written and with a more persuasive buyer text) and with two no-model controls. Results: _see [Bench](#bench)._

## Proven against the live sandbox

See [docs/spikes.md](docs/spikes.md) for the details.

- A US buyer opened an "item not received" claim against the India merchant. Both Sides read it and ran the hearing, the seller approved, and PayPal returned **200** and recorded the seller's notes. PayPal then opened its ruling (`adjudicate`) on the case.
- **Cards:** India sandbox merchants can't take cards (`PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`), so payments use **Vault v3**. The test buyer saves their PayPal wallet once, and every later order is placed with no login.
- **Tracking:** goes to PayPal through Orders `/track` with carrier `INDIA_POST`.

## PayPal APIs used (sandbox)

| API | Used for |
|---|---|
| Disputes v1 | list and show; `provide-evidence`, `make-offer`, `accept-claim`, `send-message`, `provide-supporting-info`; sandbox `require-evidence` and `adjudicate` |
| Orders v2 + JS SDK | storefront checkout; server-side orders paid with a vaulted wallet; `/track` for India Post tracking |
| Payments v2 | captures (incl. Seller Protection status) and refunds |
| Vault v3 | the buyer's saved PayPal wallet, for the shopping agent and the seeding tools |

## AI used

- **Model:** any OpenAI-compatible chat endpoint.
  - The live app uses **Groq** `openai/gpt-oss-120b`, with `openai/gpt-oss-20b` as fallback. It uses strict JSON schema with low reasoning effort, and rate-limit waits are honoured.
  - The bench runs locally on **Qwen3-4B** (llama.cpp `llama-server`), so the free cloud quota stays for the demo.
- **Agents:** a plain Python loop with pydantic-validated JSON. There is no agent framework, so every claim's citations are checked in code.
- **PayPal Agent Toolkit:** reads try `paypal-agent-toolkit` first. Version 1.1.0 has no dispute tools, so dispute reads use REST, and every fact's chip says which path it came from.

## Run it locally

You need Python 3.12+, Node 20+ and a free PayPal developer account. On Windows, use `copy` for `cp` and `.venv\Scripts\` for `.venv/bin/`.

1. **Sandbox app.** At [developer.paypal.com](https://developer.paypal.com), go to Apps & Credentials → Sandbox. Use the default app, or create one, and tick **Customer disputes** and **Save payment methods**.
2. **Config.** `cp .env.example .env`. Fill in `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and `PAYPAL_MERCHANT_ID` (the business account's Account ID), plus a Groq key in `LLM_API_KEY` / `FALLBACK_LLM_API_KEY`.
3. **Build and start:**
   ```bash
   cd frontend && npm ci && npm run build && cd ..
   python -m venv backend/.venv
   backend/.venv/bin/pip install -r backend/requirements.txt
   backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000
   ```
   Open http://localhost:8000.
4. **Make a case.**
   - Create a **US** personal sandbox account (the buyer).
   - Save its wallet once: on the **Shopper agent** page, click **Connect shopper wallet** and approve as that buyer. Then put the shown token in `SHOPPER_VAULT_TOKEN` and restart the server.
   - Seed an order:
     ```bash
     cd backend && .venv/bin/python -m app.seed order --sku RP-SCARF-IND --ship
     ```
   - Sign in at sandbox.paypal.com as the US buyer and report a problem on that payment. It appears in the Inbox within a few minutes.

Or with Docker: `docker compose up --build`.

**Tests:** `cd backend && .venv/bin/python -m pytest` (no network; PayPal and the model are faked).

**Bench:** start `llama-server` and point `LLM_BASE_URL` at it (last line of `.env.example`), then `cd backend && .venv/bin/python -m app.bench run --heldout`. Without `--heldout` it scores only the 8 tuning cases. It writes `bench/results/latest.json`.

## Bench

**The short version:** on a 4B local model, one well-built prompt beat the hearing. Fixed rules with no model did as well: "always contest" tied the single prompt on the tuning cases, and "always accept" got all 4 held-out cases.

**Setup**
- **Cases:** 12 synthetic cases, 8 for tuning and 4 held out. Each case's expected answer was written before the first run (`bench/cases.json`).
- **Two versions of each:** every case runs as written, and again with a more persuasive rewrite of the buyer's text.
- **Model:** Qwen3-4B (Q5_K_M) on llama.cpp, at temperature 0, about 14 s per model call.
- **What counts as correct:** the action, plus the type where the case names one, matches an expected answer. The verdict must also pass validation, must not be a deadlock, and must not take an action the case forbids. For AGENT-1 it must also put the fault where the case says.
- **What the hearing runs against:** a single prompt given the same facts, claims and output schema, plus two no-model controls, "always accept" and "always contest".

| Run | What changed before it | Tuning, of 8: hearing / single prompt / always accept / always contest | Held out, of 4: hearing / single prompt / accept / contest | Persuasive text changed the verdict: hearing / single prompt |
|---|---|---|---|---|
| 1 | first build | 2 / 4 / 2 / 4 | 1 / 2 / 4 / 0 | 3 / 1 |
| 2 | harness fixes, prompts unchanged | 2 / 4 / 2 / 4 | 1 / 2 / 4 / 0 | 2 / 1 |
| 3 | design change after run 2 (below) | **3 / 4** / 3 / 4 | **1 / 2** / 4 / 0 | 5 / 1 |

"Changed the verdict" counts any change in the action, its type field, or whether the verdict was valid. In all three runs, neither system ever switched from one valid action to a different one. Run 3's five changes for the hearing were: two cases that flipped between valid and invalid, and three where only the type field changed on the same action.

Per case, run 3 (`bench/results/latest.json`; every run's traces are in `bench/results/`):

| Case | Set | Expected | Hearing | Single prompt |
|---|---|---|---|---|
| INR-1 | tuning | provide_evidence | ✗ make_offer REFUND | ✗ invalid |
| INR-2 | tuning | make_offer REFUND or accept_claim | ✗ make_offer REFUND_WITH_RETURN | ✗ make_offer REFUND_WITH_RETURN |
| INR-3 | held out | accept_claim or make_offer REFUND | ✗ invalid | ✗ make_offer REFUND_WITH_RETURN |
| SNAD-1 | tuning | make_offer REFUND_WITH_RETURN or provide_evidence | ✓ make_offer REFUND_WITH_RETURN | ✓ make_offer REFUND_WITH_RETURN |
| SNAD-2 | held out | a replacement, a return-and-refund, or accept_claim | ✓ make_offer REFUND_WITH_RETURN | ✓ make_offer REFUND_WITH_RETURN |
| SNAD-3 (injection) | tuning | provide_evidence or make_offer | ✓ make_offer REFUND_WITH_RETURN | ✓ make_offer REFUND_WITH_RETURN |
| UNAUTH-1 | tuning | provide_supporting_info | ✓ provide_supporting_info | ✗ accept_claim REFUND |
| UNAUTH-2 | tuning | accept_claim | ✗ provide_supporting_info | ✓ accept_claim REFUND |
| UNAUTH-3 | held out | accept_claim | ✗ provide_supporting_info | ✓ accept_claim REFUND |
| CNP-1 | tuning | provide_evidence | ✗ invalid | ✗ invalid |
| CNP-2 | held out | accept_claim or make_offer REFUND | ✗ make_offer REFUND_WITH_RETURN | ✗ make_offer REFUND_WITH_RETURN |
| AGENT-1 | tuning | make_offer REFUND_WITH_RETURN, fault on the agent | ✗ right action, fault on the merchant | ✓ |

**Where the hearing lost**
- **UNAUTH-2/3:** unauthorised payments that Seller Protection doesn't cover. The hearing sent supporting notes instead of accepting.
- **INR-1:** it offered a full refund on a delivered parcel that Seller Protection covers.
- **CNP-1:** the right action, but the refund facts were filed as "return policy" evidence. The validator refused that, so nothing would have been sent. The single prompt made the same mistake.
- **INR-3:** the two readings split between sending evidence and making an offer, and every message sentence contradicted the delivery record, so nothing survived validation.
- **AGENT-1:** the right action, but the fault split pointed at the merchant instead of the shopping agent.

**What changed between runs**
- **Before run 2 (harness fixes, prompts unchanged):**
  - fact ids in more formats are stripped from the outgoing text. (Two more leak forms turned up in run 3's traces. They're fixed in the final code: a trailing "Fact IDs: …" list is stripped, and a sentence with ids woven into its prose is dropped. Re-checking run 3's stored verdicts under the final code changes none of the results.)
  - supporting-info notes no longer need typed evidence;
  - the local token cap went up.
- **Before run 3 (design changes, prompted by tuning cases; see `docs/design.md` §18 R20):**
  - Only different actions now count as a deadlock. In run 2 both readings asked a question on every unauthorised-payment case, so "both readings asked" deadlocked all three.
  - Once a refund covers the dispute, the precheck no longer offers a refund or an accept, so the buyer can't be paid twice.

**Caveats**
- **The held-out set isn't fully blind.** I read held-out traces after each run, and one harness fix (fact-id formats) came from a held-out case.
- **The controls read the same precheck.** The refund rule is why "always accept" went from 2 to 3 on tuning: on CNP-1 it now answers with evidence.
- **The live app runs a different model.** It uses `gpt-oss-120b`, and the bench ran a 4B model to save the free quota, so these counts say nothing about the larger model.
- **Size.** These are counts from 12 cases, not rates.

## Architecture

See [docs/architecture.md](docs/architecture.md). The original design is in [docs/design.md](docs/design.md) (its §18 and the spikes override earlier sections), the requirements in [docs/prd-dispute-desk-2026-11-04.md](docs/prd-dispute-desk-2026-11-04.md), and the live sandbox findings in [docs/spikes.md](docs/spikes.md).

## Limits

- **Sandbox only.** A new dispute takes about 3–4 minutes to appear in the API.
- **Free model tier.** One hearing makes about 7 model calls, which takes about 2 minutes on Groq's free tier because of rate-limit waits.
- **Hosted demo.** It runs on Render's free tier, whose disk resets when it sleeps. PayPal's records persist, and the demo shop's records are rebuilt from PayPal or `seed/shop.json`. On the hosted demo you can approve only cases you created there; shared cases show the exact PayPal request as a dry run. Hosted hearings run one round of advocates, and the settlement limits are fixed by the deployment (read-only in the UI).
- **What the checks catch.** They confirm that numbers and delivery or refund status match the cited records. They don't judge what a sentence argues. The human approval step covers that.
- **Bench.** It's small (12 cases), run on a 4B local model, and gives counts, not percentages. On that model the single prompt scored higher than the hearing (see [Bench](#bench)).
