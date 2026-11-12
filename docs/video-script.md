# Demo video: read-aloud script (about 2:40)

The screen footage is recorded separately, section by section, from the running app. Riteesh records the voice (and camera) in OBS. The two are joined so each section's footage lasts exactly as long as its narration.

## How to record the voice

- **OBS:** one scene with your webcam and mic only (no screen capture). Record to MP4 at 1080p or 720p.
- **Framing:** head and shoulders, light on your face. The camera appears as a small box in the bottom-right corner.
- **Read** the seven sections below in order. Don't read the headings aloud.
- **Pauses:** between sections, stay silent for about **3 seconds**. That's where the cuts go. Short pauses inside a section are fine.
- **Mistakes:** if you fluff a line, pause 3 seconds and re-read the whole section. Tell me which section you redid.
- **Pace:** calm, a little slower than normal speech. The whole read should take about 2:40.

---

### 1 · Hook (about 17 s)

> Since 2021, PayPal in India only moves money across borders. In 2020, over 360,000 Indian sellers used it to get paid by buyers abroad. When one of those buyers opens a dispute, a small workshop in Jaipur has a deadline, a buyer in another time zone, and no idea what that buyer is really thinking.

*Screen: the Inbox.*

### 2 · The case (about 22 s)

> This is Rangsaaz Prints, a block-print workshop. A US buyer says their indigo scarf never arrived. Both Sides is a dispute desk. It reads the dispute from PayPal's Disputes API, plus the order, payment and tracking from PayPal and the shop's own records. Every PayPal fact keeps a receipt: the exact response it came from.

*Screen: the case header, the facts list, and one fact's receipt.*

### 3 · Facts first (about 16 s)

> The buyer's own words are never facts. They go through a quarantine step and come out as claims. Then the buyer's advocate tests each one against the record. "I ordered on November 5th": supported. "The tracking hasn't moved": unverified.

*Screen: the "Buyer says (untested)" box.*

### 4 · The hearing (about 30 s)

> Then two agents argue it out. The buyer's advocate asks why this person filed: the parcel left India and the tracking went quiet, so they're worried. The merchant's advocate answers from the same facts. Code strikes out any claim that cites a missing fact or a wrong number. Then a judge reads the case twice, with the sides swapped. If the two readings pick different actions, there's no verdict, and the seller is asked for the one fact that would settle it.

*Screen: the recorded hearing playing, step by step.*

### 5 · Verdict to PayPal (about 23 s)

> Here, both readings agreed. The verdict is a short note to PayPal: shipped on November 5th by India Post, with tracking, and still inside the 7-to-21-day delivery window. Every sentence is tied to a fact. Nothing goes to PayPal until the seller approves. When the seller approved this one, PayPal answered 200 and recorded the notes.

*Screen: the verdict and its message, then the real send: "Sent to PayPal", HTTP 200, debug id.*

### 6 · Agentic commerce (about 27 s)

> Two more things. Settlement limits: the seller sets them once, say full refunds up to twenty dollars for item-not-received, and inside them the agents may settle on their own, but never when Seller Protection would win. And AI shopping agents: this one buys on a shopper's instruction, and the instruction is kept as evidence. So if the purchase is disputed, the judge can split the fault between agent, merchant and buyer.

*Screen: the Settlement limits page, then the shopping agent choosing and paying.*

### 7 · Close (about 22 s)

> I benchmarked it on 12 cases with a small local model, and a single strong prompt won, six correct to four. Every trace is in the repo. What the hearing adds is that nothing reaches PayPal unless it's tied to a record and a person approves it. Both Sides: a dispute desk that hears both sides before it answers PayPal.

*Screen: the Bench page, then the GitHub README.*
