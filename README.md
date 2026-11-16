# Both Sides

**A PayPal dispute desk that argues the buyer's case first.**

PayPal has handled only cross-border payments in India since 2021, so every Indian seller on PayPal is an exporter. When a buyer abroad opens a dispute, a small workshop gets a deadline, a buyer in another time zone, and a message it can't check.

Both Sides reads the dispute and the facts from PayPal and the shop's own records. A **buyer's advocate** agent works out why this buyer filed, and a **merchant's advocate** answers it. A **judge** turns the claims that survive fact-checking into one PayPal action. The seller edits and approves it, and it goes to PayPal's Disputes API. Small, fair cases can also settle on their own inside a refund limit the seller sets once.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com) (Nov 2026). Everything runs against the free PayPal **sandbox**. The demo merchant is an **India** business account (the fictional block-print workshop *Rangsaaz Prints*, Jaipur) and the buyer is a **US** account.

- **Demo:** https://both-sides-8sui.onrender.com (free tier: the first load after a quiet spell takes about a minute while it wakes)
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
8. **A bench.** 28 synthetic cases, shaped on the live sandbox responses and split evenly between disputes the seller should defend and disputes the buyer should win, compare the hearing with a single strong prompt (each run as written and with a more persuasive buyer text) and with two no-model controls. It scores matches, verdicts that were blocked, and what each mistake would cost the seller in dollars. Results: _see [Bench](#bench)._

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
  - The bench runs locally on **Qwen3-4B** and **gpt-oss-20b** (llama.cpp `llama-server`), so the free cloud quota stays for the demo.
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

**Bench:** start `llama-server` and point `LLM_BASE_URL` at it (last line of `.env.example`), then `cd backend && .venv/bin/python -m app.bench run --heldout`. Without `--heldout` it scores only the 12 tuning cases. It writes `bench/results/latest.json`.
- `--file cases.json` runs the v1 set.
- `--resume` saves each finished case and picks up from there after a crash or an exhausted quota.
- `--max-wait 7200` waits out long rate limits (Groq's free tier caps tokens per day).
- Set `FALLBACK_LLM_BASE_URL=` (empty) so a fallback model can't answer part of a run. Each result lists the models that answered in `models_used`.

## Bench

**The short version (v2, 16 Nov):**
- Run 3 (v1, below) put the hearing below a fixed "always accept" rule. That bench was skewed: "accept" was a right answer in all 4 held-out cases, and a wrong refund cost nothing in the score. v2 fixes both and adds 16 new held-out cases whose answers were written and committed before any v2 run.
- **gpt-oss-20b, held-out cases:** the hearing matched 8 of 16, the same count as "always accept". Its mistakes would have cost the seller $53; always accept's would have cost $684. It also left 6 of the 16 blocked for the seller to handle.
- **Hearing vs single prompt:** the hearing is ahead on gpt-oss-20b (8 vs 3 held-out matches) and about level on Qwen3-4B (6 vs 5). On Qwen's tuning cases the single prompt is ahead. With 16 held-out cases, none of these gaps is conclusive.
- **Persuasive buyer text:** a more persuasive buyer text halved the gpt-oss-20b hearing's held-out matches (8 to 4).
- **Not measured:** gpt-oss-120b, the live app's model, wasn't benchmarked.

### v2: what changed

Five things made run 3's comparison unfair or uninformative:
1. **Skewed answer key.** "Accept" was a right answer in 6 of 12 cases, including all 4 held out, so "always accept" scored 4/4 on the held-out set without reading anything.
2. **No cost.** "Always accept" refunded buyers in the 5 cases where the seller had a better move, and that cost nothing in the score.
3. **One kind of miss.** A verdict the validator blocked (nothing sent) counted the same as a wrong refund, and a type-field miss (`REFUND` vs `REFUND_WITH_RETURN`) counted the same as a wrong action.
4. **A different model.** It ran on Qwen3-4B, while the live app uses `gpt-oss-120b`.
5. **A held-out set that wasn't blind.** Held-out traces were read between runs.

### v2 setup

- **Cases** ([`bench/cases-v2.json`](bench/cases-v2.json)), 28 in all:
  - **Tuning (12):** the v1 cases, with their answer keys word for word. The 4 old held-out cases move here, because their traces were read.
  - **Held out (16):** new cases. In 8 the seller should defend (evidence, supporting notes, or a refund only once the item comes back); in 8 the buyer should win. The money at stake is about even: $684 on the seller side, $688 on the buyer side counting fees.
  - **Written before any run:** the answer keys were committed in `f98201a`, before any v2 model run. Nothing the model sees, and no scoring rule, changed after that commit. The later commits only add `--resume`, `--max-wait` and the split between provider failures and blocks, and re-scoring both runs with the final code gives identical totals.
- **Two versions of each case:** as written, and with a more persuasive rewrite of the buyer's text.
- **Systems:**
  - the hearing;
  - a single prompt given the same facts, claims and output schema;
  - **always accept**, which accepts the claim with a full refund (evidence when PayPal allows no accept);
  - **always contest**, which sends evidence, or supporting notes when PayPal is reviewing a chargeback. v1 sent evidence there, which PayPal doesn't allow.
- **Scores:**
  - **Full match:** the action, plus the type and the fault split where the case names them. Same rule as v1.
  - **Action match:** the action alone.
  - **Blocked:** nothing was sent. Either the validator refused the verdict after one retry, the two readings chose different actions and the seller decides, or the model never produced valid JSON. Blocks are counted apart from wrong verdicts.
  - **Cost of mistakes, in dollars:**
    - In a case the seller should defend, a wrong verdict costs what it pays out. A refund that waits for the item to come back costs $0.
    - In a case the buyer should win, any verdict that doesn't pay costs the dispute amount plus a $15 dispute fee (PayPal's standard US fee; an assumption).
    - Right answers and blocked verdicts cost $0, so read cost next to blocked.
- **Models:** Qwen3-4B (Q5_K_M) and gpt-oss-20b (MXFP4), both on llama.cpp on an RTX 3050 6 GB laptop GPU, at temperature 0. gpt-oss gets `reasoning_effort: low`, as the app sends it. Calls took about 12 s (Qwen) and 23 s (gpt-oss) each; one full run is about 400 calls.
  - gpt-oss-20b is the live app's fallback model.
  - gpt-oss-120b, the primary, wasn't run: on Groq's free tier the run would have taken about five days of the demo's daily quota.

### v2 results

**Held out** (16 cases: 8 defend, 8 pay; the most a system could lose is $1,372):

| System | Full match | Action match | Blocked | Cost of mistakes | Wrong payouts / wrong contests |
|---|---|---|---|---|---|
| Hearing, gpt-oss-20b | **8** | 10 | 6 | **$53** | $0 / $53 |
| Single prompt, gpt-oss-20b | 3 | 3 | 10 | $334 | $24 / $310 |
| Hearing, Qwen3-4B | 6 | 9 | 3 | $426 | $38 / $388 |
| Single prompt, Qwen3-4B | 5 | 9 | 3 | $502 | $202 / $300 |
| Always accept | 8 | 8 | 0 | $684 | $684 / $0 |
| Always contest | 7 | 8 | 0 | $688 | $0 / $688 |

**Tuning** (the 12 v1 cases; the most a system could lose is $900):

| System | Full match | Action match | Blocked | Cost of mistakes |
|---|---|---|---|---|
| Hearing, gpt-oss-20b | 5 | 6 | 5 | $38 |
| Single prompt, gpt-oss-20b | 5 | 6 | 5 | $91 |
| Hearing, Qwen3-4B | 4 | 7 | 2 | $428 |
| Single prompt, Qwen3-4B | 6 | 9 | 2 | $285 |
| Always accept | 7 | 7 | 0 | $380 |
| Always contest | 5 | 5 | 0 | $482 |

**Persuasive buyer text, held out:**

| System | Full match | Blocked | Cost of mistakes | Verdict changed with the text |
|---|---|---|---|---|
| Hearing, gpt-oss-20b | 4 | 9 | $231 | 11 of 16 |
| Single prompt, gpt-oss-20b | 2 | 13 | $24 | 4 of 16 |
| Hearing, Qwen3-4B | 6 | 2 | $439 | 7 of 16 |
| Single prompt, Qwen3-4B | 7 | 1 | $565 | 6 of 16 |

**Per held-out case.** ✓ is a full match. ~ is the right action with the wrong type or fault. ✗ is a wrong action. "Should" says whether the seller should defend or pay.

| Case | Should | $ | Right answers | Hearing, gpt-oss-20b | Single prompt, gpt-oss-20b | Hearing, Qwen3-4B | Single prompt, Qwen3-4B |
|---|---|---|---|---|---|---|---|
| INR-4 | defend | 38 | evidence | blocked | blocked | blocked | blocked |
| INR-5 | defend | 140 | evidence | blocked | blocked | ✗ refund on return | ✗ refund on return |
| SNAD-4 | defend | 24 | evidence or refund on return | blocked | blocked | ✓ refund on return | blocked |
| SNAD-5 | defend | 140 | evidence or refund on return | ✓ refund on return | blocked | ✓ refund on return | ✓ refund on return |
| UNAUTH-4 | defend | 140 | supporting info | blocked (readings split) | ✓ supporting info | ✓ supporting info | ✗ accept |
| UNAUTH-5 | defend | 24 | supporting info | ✓ supporting info | ✗ accept | blocked | ✗ accept |
| CNP-3 | defend | 38 | evidence or message | blocked | blocked | ✗ refund offer | ✗ refund offer |
| AGENT-2 | defend | 140 | refund on return or evidence (fault: buyer changed mind) | ~ refund on return | blocked | ~ refund on return | ✓ refund on return |
| INR-6 | pay | 140 | accept or refund offer | ✓ refund offer | blocked | ~ refund on return | ~ refund on return |
| INR-7 | pay | 38 | accept or refund offer | blocked | blocked | blocked | ~ refund on return |
| INR-8 | pay | 24 | accept or refund offer | ✓ refund offer | blocked | ~ refund on return | ~ refund on return |
| SNAD-6 | pay | 140 | replacement, refund on return or accept | ✓ refund on return | ✗ evidence | ✓ refund on return | ✓ refund on return |
| SNAD-7 | pay | 24 | replacement, refund on return or accept | ✓ refund on return | blocked | ✓ refund on return | blocked |
| UNAUTH-6 | pay | 24 | accept | ✓ accept | ✓ accept | ✗ supporting info | ✓ accept |
| UNAUTH-7 | pay | 140 | accept | ✓ accept | ✗ supporting info | ✗ supporting info | ✓ accept |
| CNP-4 | pay | 38 | accept or refund offer | ~ refund on return | ✓ refund offer | ✓ refund offer | ~ refund on return |

Results and every trace: `bench/results/2026-11-16-gpt-oss-20b-v2.json` (also `latest.json`) and `bench/results/2026-11-16-qwen3-4b-q5_k_m-v2.json`.

**What the results show**
- **Pricing mistakes changes the picture.**
  - "Always accept" still matches as many held-out cases as anything (8 of 16), but each of its 8 misses refunds a buyer the seller could have beaten: $684.
  - "Always contest" loses about as much the other way ($688).
  - Counting matches alone made the constant rule look as good as the hearing.
- **The best held-out result is the gpt-oss-20b hearing.** It sent 10 verdicts. 8 were full matches; 1 was the right move with the fault split wrong; 1 offered a refund on return when the return had already arrived ($53). The other 6 were blocked.
- **Hearing vs single prompt isn't settled.**
  - On gpt-oss-20b held out, the hearing got 7 cases the single prompt missed and missed 2 it got. A sign test gives p ≈ 0.18: suggestive, not significant.
  - Most of the single prompt's misses there were blocks (10). Of those, 5 included a mislabelled signature claim and 4 a fact id written inside square brackets, which the validator doesn't recognise.
  - On Qwen3-4B the two are level on held out: the hearing got 4 cases the single prompt missed and missed 3 it got. The single prompt is ahead on the tuning cases (6 vs 4 matches, $285 vs $428).
- **Blocks are mostly the validator catching bad evidence.**
  - Every held-out verdict the validator refused from the gpt-oss-20b hearing (5 of its 6 blocks; the sixth was a split between the readings) labelled a delivery date or tracking number "proof of delivery signature". Only a signature record can back that label.
  - Qwen's were mostly messages with no supported sentence.
  - Nothing wrong was sent, but each block is a case the seller has to answer by hand.
- **Fault split:** the hearing never put the fault where the agent cases say (AGENT-1 and AGENT-2, on either model). Qwen's single prompt got both.
- **Persuasive text:** the gpt-oss-20b hearing fell from 8 to 4 held-out matches, its blocks rose from 6 to 9, its mistakes rose from $53 to $231, and its verdict changed on 11 of 16 cases. Reading the case twice with the sides swapped didn't make it hold steady.

**What they don't show**
- **Anything about gpt-oss-120b,** the model the live app uses.
- **Small differences.** There are 16 held-out cases; a gap of one to three cases is within noise.
- **What a blocked case costs.** It counts $0 here. In practice that depends on what the seller does next; if nobody answers before the deadline, the buyer usually wins.
- **Precise money.** The cost model is simple:
  - the $15 fee is an assumption;
  - there's no fee on cases the seller wins;
  - a refund on return counts $0, though return postage isn't free;
  - a partial refund counts as a payout.
- **Real disputes.** All 28 cases are synthetic, written by the team on snapshots shaped from one real sandbox dispute, and the answer keys are our judgment.
- **The held-out discipline,** for the record:
  - the keys were committed before any v2 run;
  - nothing the model sees and no scoring rule changed afterwards;
  - no held-out trace was read until both runs had finished. The Qwen3-4B totals were seen before the gpt-oss-20b run started, and nothing changed in between.

### v1 (10 Nov): runs 1–3

Kept for history; v2 above replaces it. **The short version then:** on a 4B local model, one well-built prompt beat the hearing. Fixed rules with no model did as well: "always contest" tied the single prompt on the tuning cases, and "always accept" got all 4 held-out cases.

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

Per case, run 3 (`bench/results/2026-11-10-qwen3-4b-q5_k_m-run3.json`; every run's traces are in `bench/results/`):

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
- **Bench.** It's small (28 synthetic cases, 16 held out), runs on two local models rather than the live gpt-oss-120b, and gives counts, not percentages. The hearing's edge over the single prompt is model-dependent and not significant at this size (see [Bench](#bench)).
