# Filing record — Provisional C (this repo's cluster)

**Status:** ✅ **FILED 2026-08-17.**

> This repo is public. Personal fields (full legal name, residence, mailing
> address) are deliberately **not** recorded here.

## C — this repository

| Field | Value |
| --- | --- |
| **Application number** | **`64/135,421`** |
| Confirmation number | `3156` |
| Attorney docket | `SCB-003-PRV` |
| Title | Deadline-Fenced Delegation of Decision Surfaces from a Synchronous Engine to an Asynchronous External Decision-Maker with Fallback-Gated Tiering |
| Specification filed | [`provisional-C-delegation.pdf`](provisional-C-delegation.pdf), 47 pp · source [`provisional-decision-delegation.md`](provisional-decision-delegation.md) |
| Filed | 2026-08-17, 4:22:43 PM ET |
| Type | Provisional under 35 U.S.C. 111(b), Utility |
| Entity status | Small · Sole inventor · **Unassigned** |
| **Expires** | **2027-08-17. Not extendable.** |

A provisional is never examined and never publishes. It grants nothing; it fixes
a priority date of **2026-08-17** for whatever the specification supports.

⚠️ **Cosmetic note.** The filed PDF's title page reads *"Inventor: Stephen C.
Brown (name pending legal-name verification; sole inventor)"* — a leftover
drafting note. It has no legal effect, because the Application Data Sheet
establishes the inventor of record, and a provisional's specification cannot be
amended anyway. Filed as-is by decision.

## 🔴 Disposition: C is planned to lapse

**Only B (`64/135,383`, bobbin) is being converted to a nonprovisional.** The goal
is a granted patent as a durable credential rather than revenue, and one
conversion delivers that as well as four.

C ranked **third of four**. Its single best idea is the run identifier composed
from process id, monotonic ticks since boot, and wall-clock seconds — deliberately
avoiding the RNG **because the engine's `rand()` shares the simulation seed and
perturbing simulation state to obtain a correlation identifier is an unacceptable
trade**. That is a genuinely elegant piece of engineering. It is also adjacent to
well-trodden distributed-systems ground: deadline propagation, fencing tokens and
generation numbers are all prior art. Combined with ten aspects across 47 pages,
finding the narrow claim would take more work than B's.

**If nothing changes, C lapses on 2027-08-17** and its mechanisms remain published
prior art that no one else can patent.

## ✅ Relevant to the planned yupana paper

`yupana/docs/design/paper.md` plans an evaluation using this project as its second
evidence domain — a three-arm guard experiment (off / advise / enforce) over one
pinned save, reusing the deterministic-replay pairing method from
[`../../evals/runs/na-s4e`](../../evals/runs/na-s4e).

**Publishing costs nothing.** US priority is locked at the filing date, C is
lapsing regardless, and non-US rights were foreclosed by the public repository
disclosures. **No patent reason to delay, redact, or embargo.**

⚠️ Note for that paper: *"the LLM beat the built-in AI"* is a capability result
about the brain and belongs to **this** cluster's thesis (decision delegation),
not to the yupana paper's thesis (policy enforcement at the action boundary).
Keep the two claims separate.

## Grace periods

`quipu/docs/patents/disclosure-timeline.md` (rev 3, adversarially re-derived)
carries the per-mechanism first-disclosure dates and US deadlines for this
cluster. The 2026-08-17 filing lands inside every window.

🔴 Non-US rights were foreclosed by the public repository disclosures — absolute
novelty, no grace period — and are not part of the plan.

## The sibling filings

| | Repo | Application | Disposition |
| --- | --- | --- | --- |
| A | quipu | `64/135,410` | backup |
| B | bobbin | `64/135,383` | 🎯 convert |
| C | **NeuralAmplifier** (this one) | **`64/135,421`** | lapse |
| D | camayoc | `64/135,436` | lapse |

## For agents working in this repo

- Do **not** describe C as conferring protection. A provisional grants nothing,
  and this one is expected to lapse.
- Mechanisms added after 2026-08-17 are **not** covered and carry their own fresh
  12-month disclosure clocks.
