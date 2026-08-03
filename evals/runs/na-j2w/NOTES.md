# na-j2w — does asking a model to decide *and* set direction cost decision quality?

**The experiment cannot answer its own question, and the reason is the finding: the model never
set direction.** Zero directives were issued across twenty runs, including the ten where the
prompt explicitly invited them.

Same shape as [na-373](../na-373/NOTES.md): a plausible mechanism, measured, turns out not to be
what happened.

## Method

`scripts/decision_stability.py`, `faction.tech`, University at turn 135 (7 legal techs), 10 runs
per arm, `claude -p` one fresh process per run so samples stay independent.

| arm | prompt |
| --- | --- |
| `issuing` | `--brain claude-code` — the full system prompt |
| `ablated` | `--brain claude-code-no-directives` — `## Issuing directives` removed, `## Standing directives` kept |

The ablation is verified sound (na-43h): it cuts 2944 chars and removes only the issuing section,
so "not allowed to issue" is not confounded with "cannot see directives at all".

## What came back

| arm | stability | distinct | modal | directives issued | cost |
| --- | --- | --- | --- | --- | --- |
| `issuing` | 0.40 | 4 of 10 | `tech:81` ×4 | **0** | $1.03 |
| `ablated` | 0.60 | 3 of 10 | `tech:12` ×6 | **0** | $0.74 |

## Why this does not measure what it set out to

**1. The treatment never occurred.** The arms differ in prompt *text*, not in behaviour. Nothing
in the `issuing` run issued a directive, so the model was not "also planning" — it was reading a
longer prompt. Any stability difference is a prompt-content effect, and attributing it to the
cost of planning would be inventing a mechanism to fit a number.

**2. n=10 cannot separate 4/10 from 6/10.** A modal count of 4 against 6 is comfortably inside
binomial noise at this sample size. The 0.40/0.60 gap looks like a finding and is not one. The
harness's own docstring warns about exactly this — five samples reported 1.00 for `base.hurry`
and the next sample disagreed — and ten is not enough either when the effect being looked for is
this small.

Either defect alone would sink the result. Reporting "issuing costs 0.2 stability" would have
been a confident answer to a question this run never asked.

## What it *does* establish

**A model does not issue directives spontaneously, even when invited.** That is worth knowing and
it is not what the plumbing predicted. na-43h proved the directive path works end to end in a
live game — issue on `faction.tech`, shown on a later `base.production`, `plan.json` created by
the issue — but that proof used a *scripted* driver that issued deliberately. Offered to a real
model on a real world view, the capability went unused ten times out of ten.

So the gap is not in the mechanism. It is that nothing makes issuing *attractive* at the moment
of decision: the model is asked to choose a technology, and setting faction policy is optional
extra work with no visible payoff inside that turn. This is the same failure `Orders.cited`
already documents — a field explained in a prompt and left empty on every run — and the fix that
worked there was moving the explanation into the *schema*, where the model reads it.

**`faction.tech` is unstable at this world view**, 0.40–0.60 across twenty samples with three to
four distinct answers from seven options. Ungrounded (no `--quipu`), so this is the floor rather
than a verdict on the surface.

## To actually answer the question

1. Get directives issued at all — schema-level description, or a world view whose horizon makes
   direction obviously relevant. Until an arm issues, there is no treatment.
2. Then run n≥30 per arm, or score something less lossy than modal share.
3. Score the *choice*, not only stability. A model that becomes more consistent while choosing
   worse is not an improvement, and stability alone cannot tell those apart — the point na-373
   made about ranking, in a different costume.

Cost of learning this: **$1.77**.
