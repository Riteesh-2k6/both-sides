# Devpost submission text (draft)

**Project name:** Both Sides
**Tagline (≤ 60 chars):** A PayPal dispute desk that argues the buyer's case first

## Inspiration

PayPal stopped domestic payments in India in 2021. It kept the cross-border side: in 2020 it carried $1.4 billion of international sales for over 360,000 (3.6 lakh) Indian merchants ([Business Today, Feb 2021](https://www.businesstoday.in/amp/technology/story/paypal-says-to-shut-domestic-payments-business-in-india-286762-2021-02-05)). So every Indian seller on PayPal is an exporter selling to buyers abroad. When one of those buyers opens a dispute, a small workshop gets a deadline, a buyer in another time zone, and a message it can't check. Most sellers either refund in a panic or argue against claims the records already settle. I wanted a desk that does what a careful person would: find out what actually happened, work out why this buyer filed, and only then answer PayPal.

## What it does

Both Sides reads a dispute from PayPal's Disputes API, plus the order, capture, tracking and the shop's own records. It runs a short hearing:

- **Facts first.** Every PayPal fact carries a receipt, the PayPal response it came from; shop facts come from the shop's own records. The buyer's text is never a fact. A quarantine step turns it into claims to test, and the buyer's advocate rates each one supported, contradicted or unverified.
- **Rules before models.** Deterministic checks decide Seller Protection coverage, the deadline, and which actions PayPal currently allows on this case.
- **Two advocates.** A buyer's advocate explains why this buyer most likely filed. A merchant's advocate answers with the cheapest defensible response, and settling counts as a legitimate choice. Code strikes out any claim whose cited facts don't exist or whose numbers or delivery status don't match.
- **A judge that reads twice.** It reads the case in both orders. If the two readings disagree, there's no verdict, and the seller is asked for the one fact that would settle it.
- **Human gate.** Code checks the verdict against PayPal's allowed actions and the dispute amount. The seller edits and approves, and the action goes to PayPal: provide evidence, make an offer, accept the claim, send a message, or send supporting notes while PayPal reviews a claim.
- **Settlement limits (agentic commerce).** The seller sets limits once: a maximum refund, a percentage, a daily cap, the dispute reasons it covers, and an expiry. Inside them, the agents may offer a full refund or message the buyer on their own. They can't when Seller Protection would win, when facts are missing, or when the buyer's text looks like an injection attempt.
- **Agent purchases.** A shopping agent buys from the shop on a shopper's instruction, and the instruction is stored as evidence. If the purchase is disputed, the judge splits the fault between agent, merchant and buyer.

## How we built it

- **PayPal (sandbox):**
  - Disputes v1: list, show, `provide-supporting-info`, `provide-evidence`, `make-offer`, `accept-claim`, `send-message`, and sandbox `adjudicate`.
  - Orders v2 plus the JS SDK for checkout.
  - Orders `/track` for India Post tracking.
  - Payments v2 for captures, refunds and Seller Protection status.
  - **Vault v3**: the US test buyer saves their PayPal wallet once, and the shopping agent and the seeding tools pay with it, with no login.
  - The merchant is an **India** sandbox business account and the buyer is a **US** account, the cross-border pairing Indian sellers actually have.
- **AI:** a plain Python loop with pydantic-validated JSON over any OpenAI-compatible endpoint.
  - The live app runs on **Groq** (`openai/gpt-oss-120b`, with `gpt-oss-20b` as fallback).
  - The benchmark runs on **Qwen3-4B**, a local model (llama.cpp), with the same prompts.
  - Prompts are fixed. Every claim and every outgoing sentence is checked in code.
- **App:** FastAPI, SQLite, React + TypeScript (Vite), and Docker, deployable to Render (`render.yaml`).

## Tools used (and how)

- **Groq, `openai/gpt-oss-120b` / `gpt-oss-20b`:** runs the quarantine step, the two advocates and the judge in the live app. It uses strict JSON-schema output with low reasoning effort.
- **Qwen3-4B (Q5_K_M) on llama.cpp `llama-server`:** the same pipeline and prompts for the 12-case benchmark, run locally against a single-prompt baseline, so the free cloud quota stays for the demo.
- **PayPal REST APIs (sandbox):** listed above. The Python PayPal Agent Toolkit has no dispute tools in version 1.1.0, so dispute reads use REST, and the UI labels every fact's source.

## Challenges we ran into

- **The sandbox didn't match the docs.** A buyer-opened "item not received" case arrived as a claim already under PayPal review (`stage CHARGEBACK`, `channel INTERNAL`). The only seller move was "provide supporting info". Because the app reads legal actions from PayPal's own links, it handled the case once I added that action. Sending the notes then unlocked PayPal's ruling endpoint.
- **India merchants can't take cards**, even in sandbox, and chargebacks need a card-funded payment. Vault v3 solved it: the test buyer approves a saved PayPal wallet once, and every later order is placed without a login.
- **Free-tier rate limits.** One hearing is about 23k tokens. Waiting out 429s using the exact "try again in N s" hint, plus low reasoning effort, made hearings finish reliably.
- **Making the model honest.** The validator dropped a sentence the judge had mislabelled as "courtesy". It also caught fact ids pasted into the text. Both became regression tests. A judge once claimed Seller Protection coverage the rules hadn't granted; that is now a prompt rule, and the human gate is the check.

## Accomplishments that we're proud of

- **A real dispute, end to end.** A live sandbox dispute went from hearing to approved PayPal action: PayPal returned 200 and recorded the seller's notes.
- **Everything checked in code.** Every number, delivery claim and refund claim in an outgoing message is checked against the record before it can be sent.
- **Tests:** 500 backend tests. Before the first commit, the code went through eight review rounds, each leaving regression tests (`backend/tests/test_review1-8.py`).
- **An honest bench.** 12 cases, with expected answers written before the first run, three labelled runs, and every trace in the repo. It reports the result even though the hearing lost.

## What we learned

A single prompt with the same facts is a strong baseline:

- **On a 4B local model it beat the hearing:** 4 of 8 against 3 on tuning cases, and 2 of 4 against 1 held out.
- **Fixed rules did as well.** "Always contest" tied the single prompt on tuning, and "always accept" got all 4 held-out cases.
- **More steps give a small model more places to slip.** One of the hearing's misses was the right action with refund facts filed as "return policy" evidence, which the validator refused. Another had its two readings split, and every sentence contradicted the delivery record.
- **The persuasive text never switched either system to a different valid action.** It only flipped validity or a type field.

So, at least on a small model, the hearing adds structure rather than accuracy: claims tied to records, a judge that can't see who argued first, and a validator that drops anything unsupported. The bench also changed the design. A deadlock now needs the two readings to pick different actions, and once a refund covers the dispute, the desk never offers another one. The live app runs `gpt-oss-120b`, which the bench didn't test.

## What's next

- Webhooks instead of polling.
- Appeals.
- A PDF evidence packet.
- Running the buyer's advocate *before* a dispute exists, to flag at-risk orders.
- A different model per role.

## Built with

`python` `fastapi` `sqlite` `react` `typescript` `vite` `docker` `render` `paypal-disputes-api` `paypal-orders-api` `paypal-vault` `groq` `gpt-oss` `llama.cpp` `qwen3`

## Links (fill in)

- Repo: https://github.com/Riteesh-2k6/both-sides
- Demo: https://both-sides-8sui.onrender.com
- Video: https://youtu.be/_id_
- **Testing instructions (private field):** the US sandbox buyer login, and "Shared cases are dry-run; create your own case via Shop → buy as the sandbox buyer → open a dispute at sandbox.paypal.com".
