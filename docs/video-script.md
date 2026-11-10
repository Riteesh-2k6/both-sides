# Demo video script (target 2:45, hard limit 3:00)

**Recording setup:** 1080p screen recording of the app on `localhost:8000`, plus a sandbox.paypal.com tab logged in as the US buyer. Voice-over is Riteesh. Use no music, or a royalty-free track at low volume. Show PayPal only through its own sandbox pages; no logos in the edit.

**Before recording:**
1. The approve shot (1:40–2:10) needs a **live** case: seed a fresh order and have the US buyer dispute it (about 4 minutes to reach the API, then about 2 minutes of hearing; cut the wait in the edit). A recorded hearing can't be approved. For 0:35–1:40 you may use **Play** on the recorded hearing `inr-stalled-india-post` instead. Read the verdict's real date and tracking number off the live case and say those at 1:40.
2. Keep the settlement-limits page saved (max $20, INR, expires tomorrow).
3. Zoom the browser to 110% so the text is readable.

---

### 0:00–0:15 · Hook (Inbox on screen)

> "Since 2021, PayPal in India only moves money across borders. In 2020, over 360,000 Indian sellers used it to get paid by buyers abroad. When one of those buyers opens a dispute, a small workshop in Jaipur has a deadline, a buyer in another time zone, and no idea what that buyer is really thinking."

### 0:15–0:35 · The case (open the dispute)

> "This is Rangsaaz Prints, a block-print workshop. A US buyer says their scarf never arrived. Both Sides is a dispute desk. It reads the dispute from PayPal's Disputes API, and the order, payment and tracking from PayPal and the shop's own records."

**Show:** the header chips (INR, PayPal claim, Seller Protection: unclear), and click one **PayPal** fact chip to open its receipt.

### 0:35–1:00 · Facts first, claims second

> "Every PayPal fact has a receipt: the response it came from. The buyer's own words are never facts. They go through a quarantine step, come out as claims, and get tested. 'I ordered on Nov 5': supported. 'Tracking hasn't moved': unverified."

**Show:** the "Buyer says (untested)" box with the Supported and Unverified chips.

### 1:00–1:40 · The hearing

> "Then two agents argue it out. The buyer's advocate starts by asking why this person filed. Here: the parcel left India five days ago and tracking went quiet, so they're worried. The merchant's advocate answers from the same facts. Any claim citing a fact that doesn't exist, or a number that doesn't match, is struck out in code before the judge sees it. The judge reads the case twice, with the sides in swapped order. If the two readings disagree, there's no verdict, and the seller is asked for the one fact that would settle it."

**Show:** the step timeline, both advocate cards, and the "Both readings agreed" chip.

### 1:40–2:10 · Verdict → PayPal

> "The verdict is a message the seller can stand behind. Shipped on November 5th by India Post, tracking EE264875961IN, and inside the 7-to-21-day window. Every sentence is tied to a fact. Nothing goes to PayPal until the seller approves."

**Show:** click **Approve & send to PayPal**, then the PayPal 200 response and debug id. Then the dispute in sandbox showing the seller's notes.
_Optional (if time):_ use the sandbox panel's **adjudicate → SELLER_FAVOR**, and show the case resolved.

### 2:10–2:35 · Agentic commerce

> "Two more things. First, settlement limits: the seller sets them once, say full refunds up to twenty dollars for item-not-received, and inside them the agents may make that offer on their own. They're blocked when Seller Protection would win, when the facts are incomplete, or when the buyer's text looks like an injection attempt. Second, more and more purchases are made by AI shopping agents. When one of those is disputed, the shopper's instruction to their agent is evidence, and the judge splits the fault between agent, merchant and buyer."

**Show:** the Settlement limits page, then the shopper agent's instruction and pick.

### 2:35–2:50 · Proof and close

> "I benchmarked it on 12 cases with a small local model, and a single strong prompt won, six correct to four. The bench, every trace and what went wrong are in the repo. What the hearing adds is that nothing reaches PayPal unless it's tied to a record and a person approves it. Both Sides: a dispute desk that hears both sides before it answers PayPal."

_If you'd rather not feature the bench in the video, cut the first two sentences; the README carries the full result._

**Show:** the Bench page totals, then the README's bench table.

---

**Cut list if over 3:00:** drop the adjudicate step, shorten the shopper-agent part, and keep the hearing section to about 25 s with jump cuts.
