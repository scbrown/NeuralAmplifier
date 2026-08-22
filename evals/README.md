# Evals — behavioural questions about the brain

`just test` asserts values. These ask what a decision *does* over many runs, which is not a
value: "is the grounding read", "is the standing plan followed", "does the choice hold still".
The answer is a distribution over a model's outputs, so it cannot be an assertion — it has to be
measured, and re-measured when the prompt or the world view changes.

```bash
just eval list              # what exists, what each asked, what it found
just eval score na-373      # re-derive a finding from the committed run
just eval check             # do the committed answers still match the current prompt?
just eval prompts na-373    # regenerate the inputs (needs the Thinker rulebook)
just eval selftest na-htm   # does the scorer recover answers that are known in advance?
just eval harvest na-htm    # has the eval's pinned input drifted from the live retriever?
```

The last two are optional per eval and both come out of na-htm's post-mortem of na-373 — a
statistic nobody had checked against a case with a known result, computed over arms that had
drifted from what the retriever did.

**`score` needs nothing.** No model, no game, no sibling checkout, no tokens — the prompts and
the answers are both committed, so any number quoted in the docs can be recomputed on a fresh
clone by whoever doubts it. `prompts` is the only command that needs `THINKER_DIR`.

## The evals

| id | Asks | Found |
| --- | --- | --- |
| `na-373` | Can we offer fewer grounding facts by ranking them? | **Withdrawn.** The refutation was fixture-bound, and the reversal is no better — both numbers are one fact's rank. Ranking stays out as *unmeasured*. See its `NOTES.md`. |
| `na-61c2` | Does recent build history stop a base flip-flopping? | **Yes**, 0.10 → 1.00 continued — and 0.00 when the case actually changed, so it is not an anchor. |
| `na-vbe` | Can grounding drop what the action space already carries? | **Yes.** Grounding 43% smaller, choice held 20/20 vs 19/20 (Fisher p=1.0). The first run measured a fixture bug — see its `NOTES.md`. |
| `na-qu8` | Does grounding base.hurry's subject move its stability, and toward what? | **No move — both arms at ceiling.** Stability 1.00/1.00 (floor 0.50, CI [0.84, 1.00], n=20/arm), so the 0.60 that motivated the eval did not reproduce on the regenerated prompt. Treatment was real (18/20 cited the offered fact) and changed nothing. The datum worth keeping is agreement: 0/20 with the deterministic tier in *both* arms — perfectly stable and perfectly unaligned, which the scorer names as the case to read carefully, not as "grounding helped". |
| `na-htm` | Does ranking predict citation, pooled across decisions instead of within one? | **Yes.** Pooled over all four decisions, cited facts land in the ranking's top 4 at 0.74 (95% CI [0.57, 0.86]; chance 0.50) and MRR 0.574 vs 0.327 chance — with every decision contributing and no single fact or decision carrying it (largest fact 0.23, largest decision 0.29). Citation rate came in at 31/80, well above the ~1-in-3 pre-check that threatened the power, so the CI excludes chance at n=80. Truncating to 4 moved no decision's modal choice. See its `NOTES.md`. |

Write-ups: [quipu-integration.md](../docs/quipu-integration.md) and
[decision-inputs.md](../docs/decision-inputs.md) §2.

## How one is built

Three steps, and the middle one is deliberately not the harness's business.

1. **`prompts`** builds one real world view per arm and writes the exact bytes the brain would
   send, plus a manifest hashing each prompt.
2. **Something answers them.** A paid API run, or agents each given one `<arm>.task.txt`. Both
   runs here were answered by Haiku 4.5, one independent agent per decision — independence
   matters, since answering several in one context makes the later ones conditional on the
   earlier and destroys the distribution being measured.
3. **`score`** reads `<arm>.answers.jsonl` — one `{"choice": ..., "cited": [...]}` per line.

The manifest records `prompt_sha256` per arm, and `just eval check` compares it against what the
code produces now. Answers sitting beside a prompt that has since changed are not evidence about
the current prompt, and without this nobody can tell that they aren't — `score` goes on printing
the same confident table either way.

It has already happened three times, in three different ways:

- **The system prompt changed.** `na-373` was measured, then `_SYSTEM` gained a section about
  `history` for na-61c2. Ordinary drift, and the check names the added lines.
- **An eval's own arms changed underneath it.** `na-61c2` built its contested world view from
  `ground(view, retriever, 4)`, which at the time meant the top four by information value —
  the retriever ranked, briefly, before na-373 refuted it. Reverting that silently turned the
  same call into "the first four in action-space order": a different set of facts, a different
  experiment, the same name, and the old answers still sitting beside it.
- **The fixture was never the payload.** `na-vbe` compared grounding with and without cost on a
  world view whose `action_space` had no cost in it, so the arm that was supposed to be dropping
  a *duplicate* was dropping the only copy. `Action` declares three fields; `_Model` sets
  `extra="allow"` and the brain sends `model_dump_json()`, so the adapter's `cost`, `turns` and
  `role` reach the prompt without ever being named in the contract. A fixture built from the
  declared fields is a smaller payload than the real one, and nothing anywhere fails.

The second is the worse one and the lesson is in the fix: **an eval's arms are part of the
eval.** `build_history` now names the four facts it was measured with instead of deriving them
from whatever a retriever currently does, so the experiment stops changing whenever the
retriever does.

The third is not drift at all — `check` would have passed it, because the prompt matched the code
that produced it. It is the same class of error one level down: **build a fixture from what the
writer emits, not from what the reader declares.**

`check` deliberately does not judge whether drift *matters*. It says what moved and leaves that
to a person — some prompt changes cannot affect a given arm, and asserting which is a claim
needing its own evidence.

### Recording that judgement: `drift_reviewed`

A person who has adjudicated a drift records it in the manifest, and `check` then prints
`ok (drift reviewed …)` with the reason attached:

```json
"drift_reviewed": {
  "from": "d9209f53e11c", "to": "ed422cd51144",
  "on": "2026-08-03", "by": "sattler", "bead": "na-og3", "re_run": false,
  "reason": "Common-mode inert addition — …"
}
```

**It pins both hashes, and that is the entire design.** The original `prompt_sha256` is never
overwritten, and a later change produces a third hash that matches no review and goes STALE
again — naming the review that failed to cover it, so nobody reads a stale adjudication as a
current one.

The two obvious alternatives are both worse, in opposite directions. Re-hashing the manifest
until the check goes quiet **erases the only signal that the answers are old**, and it is most
tempting exactly when the drift looks harmless. Leaving the check permanently red gets it
ignored — which is how na-og3's seven stale cells went unnoticed for an unknown period in the
first place. A pinned adjudication is the only version that can go green *and* go red again.

`re_run` is not decoration: it says whether the arms were re-answered against a model, or the
drift was reasoned about from measurements. Those are very different warrants and the record has
to say which. All seven na-og3 reviews are `re_run: false`.

## Two rules, both learned the hard way

**An arm that cannot fail measures nothing.** `na-61c2` runs on a *contested* decision and puts
the brain's **least**-favoured option into the history, because history that merely reinforced an
already-unanimous choice would have looked like a success no matter what it did.

**A number needs the arm that falsifies it.** `na-61c2`'s third arm exists because 1.00
continuation is equally consistent with "correctly continued" and "follows history regardless" —
and the second of those is worse than the flip-flopping it was meant to cure. Likewise `na-373`
never reports utilisation without the choice distribution beside it: utilisation is
cited ÷ offered, so it rises whenever you offer less, and optimising it directly rewards
starving the decision.

**A statistic needs to know what it is pooled over.** `na-373` reported where citations sat in a
ranking, over one world view whose citations were 70% a single fact. Every number it produced,
in both directions, was that fact's rank wearing a finding's clothes — and nothing in the eval
could say so, because it never counted how concentrated its own pool was. `na-htm` pools across
four decisions with near-disjoint fact pools, *measures* the concentration, and refuses to print
a headline past 0.50. An eval that cannot detect this failure in itself will publish it.

**An observation record is not a decision request.** They are the same bytes apart from the
outcome fields appended after the call returns, and for `base.hurry` those include
`native_choice` — the deterministic tier's answer. A capture harvested from
`na-observations.jsonl` therefore carries the answer key, and using it unmodified would put it in
front of a model you are about to ask for an independent choice. `na-qu8` strips
`native_choice`/`tier`/`applied` and asserts their absence in its selftest. This is the same
family as the fixture failures above, and the sharpest instance: the difference between the
fixture and the payload *was the answer*.

**Three states, not two.** No answers, answers that cite nothing, and a real measurement are
different things, and a scorer that renders the first two identically to "no effect" is the same
class of bug as a blank dashboard panel: idle and dead look alike. `na-htm` prints `NOT RUN`,
`ANSWERED, BUT NOTHING CITED` and a verdict, never one standing in for another.

## Adding one

Add a module with `arms(links) -> {name: WorldView}` and `score(out, links)`, register it in
`EVALS` in `run.py` with its question *and its answer*, and commit the run under `runs/<id>/`.
Shared world views and prompt/answer plumbing are in `harness.py`.

Optional, and worth it for anything whose scorer does more than count: `selftest()` returning an
exit code, and `NEEDS_LINKS = False` if the eval reads no rulebook. Add `harvest()` if the eval
pins an input that a live component also produces — it must print the drift and not apply it,
since re-pinning invalidates the committed answers.

Register the answer in `EVALS`, not only in a document. An eval whose result you have to go and
find is one nobody rereads before repeating the work.
