# na-373 — ranking grounding by information value

**The committed finding has been withdrawn.** It was measured on a fixture whose `action_space`
carried no cost (see `../na-vbe/NOTES.md`), and it does not survive re-measurement on the
corrected one. What replaces it is weaker than either answer: **the experiment cannot decide the
question, and it never could have.**

## What was found originally

Truncating to the top 4 by `information_value` moved `base.production` from Network Node 19/20
to Research Hospital 15/20, and the baseline's citations landed in the top 4 only 0.31 of the
time against 0.50 for chance — the rule predicted citation *worse* than a coin flip. Ranking was
not adopted, and the rule was left in `evals/` rather than in the retriever.

## What re-measurement gives

| arm | n | offered | mean cited | utilisation | choices |
| --- | --- | --- | --- | --- | --- |
| `all` | 20 | 8 | 1.35 | 0.17 | `build:3`×19, `build:1`×1 |
| `ranked` | 20 | 4 | 1.10 | 0.28 | `build:3`×18, `build:0`×2 |

Baseline citations in the top 4: **20/27 = 0.74**, against 0.50 for chance.

Read literally that reverses both halves: the choice now essentially holds (19/20 → 18/20) and
the rule predicts citation *better* than chance. Two things changed at once — the fixture gained
the adapter's cost fields, and the facts themselves became compact — and compaction changes the
ranking, because `information_value` scores fresh words and digits and compaction removes some
of both.

## Why the new numbers are not a finding either

Look at where the citations sit:

```text
rank 1  fac:recreation-commons  ×1
rank 3  fac:network-node        ×19
rank 6  fac:research-hospital   ×7
```

Nineteen of twenty-seven citations are one fact. "0.74 in the top 4" is not a measurement of a
ranking rule; it is the single statement **`fac:network-node` happened to rank 3rd**. Had it
ranked 5th, the same twenty decisions would have produced 0.26 and the opposite headline. The
original 0.31 was the same statistic with the same single fact on the other side of the cut.

The choice-stability half is no better. `all` is 19/20 for one option, so this world view has a
near-unanimous answer, and an arm can only move it downward. A rule that drops the winner's fact
looks catastrophic and a rule that keeps it looks harmless, with nothing in between and no way to
tell a good ranking from a lucky one.

## What this actually establishes

**One near-unanimous world view cannot evaluate a ranking rule.** Both the original refutation
and its reversal are a single fact's rank, dressed as a rate. The mechanism na-373 described is
still sound and still the reason to be careful — grounding is one fact per option, so dropping a
fact removes the argument for a *particular* option and an unexplained option loses. That
mechanism is exactly why a single-decision eval cannot measure it: the effect size depends
entirely on whether the option that was going to win is the one that got dropped.

Ranking stays out of the retriever. Not because it was refuted — that claim is withdrawn — but
because nothing here supports adopting it, and the eval that was believed to have settled it
had one degree of freedom.

Reinstating it needs a multi-decision eval: several world views, contested rather than
near-unanimous, scored on aggregate citation rank rather than on one option's fate. Tracked as
its own bead.

## Status of the arms

`all` is byte-identical to na-vbe's `compact` arm — the same prompt, so the same 20 answers serve
both, and `just eval check` verifies that rather than trusting it. `ranked` was re-run.

## The ranking rule changed under this run (na-5to, 2026-08-06)

`ranked`'s committed answers are **no longer evidence about the rule the code now runs**, and
`just eval check` says so on its own: it reports `ranked` STALE, notes that the existing drift
review does not cover this hash pair, and refuses to widen it. Recorded here rather than
adjudicated, because re-adjudicating would launder a change that genuinely alters the arm.

`information_value` scores a fact by the words it adds beyond the option's own name, and
`label_of` derived that name by splitting on an em dash — the separator used by *this* eval's
hand-written VERBOSE fixture and by nothing else. Every fact a real retriever emits separates
with `"; "`. So on real grounding the whole fact came back as the "name", `known` swallowed
every content word, and the only words left to score were the id tokens. Over the 40 facts
pinned for na-htm the rule produced **2 distinct scores** (score 2 ×36, score 1 ×4); `sorted` is
stable,
so the `ranked` arm was silently returning retriever order and looking like a ranking. The fix
ends the name at the earliest separator of *either* kind, which takes that to 12 distinct
scores and reorders all four of na-htm's decisions.

Why this eval is affected at all, given its fixture uses the em dash: its `all` arm is
byte-identical to na-vbe's `compact` arm, whose facts are **semicolon-separated**. So na-373's
`ranked` arm was ranking compact-format lines with an em-dash-only rule — the defect's home
ground. `all` is unaffected and still `ok`; only `ranked` moved.

This costs nothing that was not already withdrawn. The conclusions above rest on one
near-unanimous world view and were withdrawn on those grounds, not on the ranking's details;
na-og3 had already marked the cells stale. It is noted because a rule changing under a
committed run is exactly the thing this directory exists to make visible.
