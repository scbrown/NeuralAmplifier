# na-htm — does ranking predict citation, measured across decisions?

**State: built, self-tested, NOT RUN.** No answers are committed. `just eval score na-htm`
prints `NOT RUN` and measures nothing, which is the honest output and not a null result.

## What this replaces

na-373 asked whether grounding could be cut by ranking facts on information value. It reported
that the ranking predicted citation *worse than chance*, then withdrew that on re-measurement,
and the reversal was no better. Both numbers reduced to one fact's rank: 19 of its 27 baseline
citations were `fac:network-node`, so "citations in the top k" was really "did network-node make
the cut". The choice half had the same defect from the other side — `base.production` at turn 35
sat 19/20 for one option, so an arm could only move it down, and a rule that kept the winner's
fact looked harmless whatever else it did.

One world view cannot measure a ranking rule. That is the finding na-373 actually produced.

## The design, and what each part is for

**Four decisions, not one.** Real captures, three surfaces, two turns, three factions:

| decision | surface | turn | faction | options | facts |
| --- | --- | --- | --- | --- | --- |
| `base_production_turn135` | `base.production` | 135 | University | 48 | 7 (`unit:`) |
| `base_production_turn42` | `base.production` | 42 | Morganites | 8 | 7 (`fac:`/`res:`/`unit:`) |
| `faction_se_turn135` | `faction.se` | 135 | University | 9 | 8 (`soc:`) |
| `faction_tech_turn135` | `faction.tech` | 135 | Hive | 5 | 5 (`tech:`) |

The fact pools are near-disjoint — **one** id (`unit:colony-pod`) appears in two decisions, and
no other is shared. That is what stops a single fact from owning the pooled number, and it is
measured and printed by `score` rather than asserted here.

`faction_se_turn42` is excluded: it offers **one** option, and a decision with one option cannot
be contested. It grounds to nothing either way.

**Aggregate citation rank, not a choice flip.** Each citation contributes its rank inside its
own decision's ranking; the headline pools those. Two numbers, mean reciprocal rank and the
fraction landing in the top 4, each against the null a random ranking would give — computed
*per decision* and pooled the same way, because the decisions carry 5 to 8 facts and one shared
chance baseline would be wrong for all of them. The nulls are analytic (`H_n/n` for MRR), so no
verdict here has a random number in its denominator.

**A dominance gate.** `score` reports the largest share held by any single fact and by any
single decision, and **refuses to print a headline** past 0.50. Feeding it na-373's own shape
reproduces the refusal — that case is a committed control, below. This is the part that must not
be quietly relaxed: without it this eval can fail exactly the way the one it replaces did.

**Unanswered, uncited and measured are three states.** A cell with no answers prints `NOT RUN`.
Answers that cite nothing print `ANSWERED, BUT NOTHING CITED` and say it is evidence about the
grounding being read, not about the ranking. Only a pool with citations in it gets a verdict.
Rendering the first two as "no effect found" is how a dead measurement passes for a null result.

**The interval decides, not the point estimate.** Wilson, not normal — the normal approximation
is worst exactly here (few observations, rates near the ends) and reports intervals off the
scale. When the interval covers the null, `score` says *cannot distinguish from chance at n=…*
and prints how many pooled citations would be needed. An eval filed because an underpowered
number was believed does not get to print a bare point estimate.

## The controls

`just eval selftest na-htm` — no model, no server, no run. Component arithmetic (both nulls,
Wilson at the 0/5 boundary, the sample-size rule), plus four end-to-end cases where the right
answer is known before the scorer sees the data:

| control | fabricated run | must print |
| --- | --- | --- |
| `perfect` | every citation at rank 0 | `= 1.00`, higher than chance, **no** refusal |
| `na-373` | one fact of one decision, ×20 | `REFUSING THE HEADLINE`, no verdict |
| `inverted` | every citation last | `anti-predictive`, `= 0.00`, no refusal |
| `uncited` | answers present, nothing cited | `ANSWERED, BUT NOTHING CITED`, no verdict |

`inverted` is there because anti-predictive is the direction na-373 originally reported, so it
is the branch most worth being certain about. `uncited` is there because it is the case that
most resembles a finding while being none.

## Why the grounding is pinned

`grounding.json` holds the exact facts the arms were built from, harvested once from a live
`quipu-server` on 2026-08-03 (`.quipu/na.db`, 3987 triples from `datalinks/thinker/alphax.ttl`).
Deriving them at score time is how na-61c2's arms changed underneath it: a retriever behaviour
was reverted, the same call silently started meaning a different set of facts, and the old
answers stayed put and kept being scored.

`just eval harvest na-htm` re-runs the retriever and **prints the diff without applying it**.
Re-pinning means re-answering; making that automatic would hide the one event that invalidates
a committed run.

## Two things observed while building this, worth their own attention

**The 48-option decision grounds 7 of its options.** `QuipuRetriever` truncates to `limit=12`
*labels before querying* (`quipu.py`: `labels = [...][: self.limit]`), so on
`base_production_turn135` only the first 12 of 48 options are asked about and 7 resolve. The
`all` arm there is already a heavy truncation of the action space, and the `ranked` arm
truncates a truncation. It does not invalidate the rank measurement — every citation is still
scored against the ranking of what was actually offered — but "every fact the action space
pulls" is not what happens on a large action space, and na-373's stated mechanism for why
truncation hurts (an unexplained option loses) applies to 41 options there before any arm acts.

**`just eval check` already exits 1 on the three older evals.** Their committed answers were
measured against a system prompt that has since gained `turns_to_completion`, and a contract
that has since gained `decision_deadline_ms` and `run_id`. That is pre-existing drift, not
introduced here, and it is one more reason na-373's numbers are not evidence about what ships
today. na-htm's own prompts are current — and it has no answers, which is the honest half of
the same statement.

## What running it costs

Measured on this surface at $0.088/run (`--brain claude-code`, na-htm pre-check, 2026-08-03):

    4 decisions x 2 arms x 10 runs  =  80 calls  ~ $7

The pre-check also measured the number that governs how big this has to be: **two of three runs
cited nothing at all**. At roughly a 1-in-3 citation rate, 80 calls yields on the order of 25-30
pooled citations, which is thin — `score` will say so itself rather than leaving the reader to
work it out, and the `_needed` line prints the n that would settle the observed gap.

Whether to spend it is a decision for a human. What is no longer true is that spending it buys a
debugging session: the arms, the pin, the scorer and its controls all run today, for free.
