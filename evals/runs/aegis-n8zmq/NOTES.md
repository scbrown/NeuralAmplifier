# aegis-n8zmq — does a model ever issue a directive, and does one carry to a later turn?

**Pre-registered before any arm was read.** The reading below was written while both arms were
still running, because na-373's post-mortem in this repository is what happens when a mechanism
is invented to fit a number after the number arrives.

## Why this run exists

`na-1gl` closed on 2026-08-03 with: *"Measuring an actual directive is na-j2w's rerun, which this
unblocks."* Nobody ran it. Twenty-seven days later `scripts/carry_report.py` measured the
consequence over the only real game NA has: **carry rate 0/1400**, and `plan.issued` empty on all
723 orchestrator records. Not one decision was answered by reasoning a previous turn produced.

## The thing na-1gl did not reach

`na-1gl` fixed `response.py` — the **structured-output schema** the Anthropic SDK lane fills in —
from `"Empty unless setting direction"` to `"Issue when this choice binds later turns; else
empty"`. Trigger first, suppressor second.

`claude -p` has no structured-output mode, so `claude_code_brain._JSON_INSTRUCTION` states the
shape in words instead. That block still reads:

    `directives` is usually `[]`. Issue one only on a decision whose reasoning should outlive
    the turn — see the section above for when that applies and what makes a plan checkable.

Suppressor first, trigger hedged with "only". Two lanes, one fixed. And the unfixed one is the
lane `decision_stability.py` describes as *"the lane that matches how the game is now played"* —
also the only lane runnable without an API key, so it is the one every measurement here uses.

## Arms

One observation — `faction.tech`, University at turn 135, 7 legal techs, the same one na-j2w
used, so the two results are comparable. `claude -p`, one fresh process per run, so samples stay
independent.

| arm | wording |
| --- | --- |
| `current` | as it ships: suppressor first |
| `trigger` | the same two clauses, reordered — na-1gl's ordering, nothing else changed |

The treatment is deliberately **not a stronger instruction**. A trigger-first arm that also
argued harder could not tell "order matters" from "we pushed more".

`issue_wording.py` asserts the substitution actually happened before spending anything, and
refuses if the shipped wording it pins has drifted. na-j2w's own post-mortem is the reason: its
arms could have differed in nothing but length and it would still have printed a confident table.

## How to read the result — written in advance

| outcome | reading |
| --- | --- |
| `current` 0, `trigger` ≥1 | the wording is a live cause, and na-1gl's fix needs applying to this lane. Then run `carry_across_turns.py`. |
| both 0 | the wording is **not** the blocker in this lane. na-j2w's deeper answer stands — nothing makes issuing attractive at the moment of decision — and the fix is a world view with a horizon, not a sentence. Do not re-word and re-run. |
| both ≥1 | the fix already reached this lane by some other route, or issuing is observation-dependent. Either way the wording claim is withdrawn and the carry run proceeds. |
| `current` ≥1, `trigger` 0 | treat as noise at n=10, not as a reversal. na-j2w warns that ten samples cannot separate 4/10 from 6/10, and this table must not launch a story about suppressors helping. |

**n=10 per arm distinguishes 0 from "sometimes", and nothing finer.** Any claim here about a
*rate* rather than about *ever* is out of scope for this sample size.

## Cost and wall clock

~$0.16 and ~5 minutes per run, measured on a 2-run smoke test. So a 10-run arm is roughly $1.60
and the better part of an hour, and wall clock is the binding constraint rather than spend. A
first attempt at both arms concurrently was killed by a 50-minute timeout with nothing written,
which is why each run is now flushed to a trail file as it completes.

## Results

| arm | runs | degraded | **directives issued** | stability | modal choice |
| --- | --- | --- | --- | --- | --- |
| `current` — suppressor first | 10 | 0 | **0** | 0.60 | `tech:12` x6 |
| `trigger` — trigger first | 10 | 2 | **0** | 0.40 | `tech:12` / `tech:59` x4 |

**Both zero.** The pre-registered reading applies, unchanged: *the wording is not the blocker in
this lane.*

Data: `current.json`, `trigger.json`, `current.trail.jsonl` (per-run, and the reason `trigger.json`
has no trail is that the flush was added after that arm had already been killed once by a
timeout — the fix arrived between the two arms).

### What this settles, and what it does not

**Settles:** re-wording is not the fix, and nobody should spend another arm on it. Counting
na-j2w's twenty runs in the SDK lane, that is now **0 directives in 40 runs of `faction.tech`
across three prompt configurations and two lanes**. The hypothesis this run was built to test —
that the `claude-code` block's "usually `[]`" is the mechanism behind the 0/1400 carry rate — is
**measured false at n=10**, and it was a good hypothesis: it named a real difference between two
lanes, one of which na-1gl had already judged worth fixing.

**Does not settle:** whether *anything* would make a model issue. n=10 distinguishes zero from
sometimes and nothing finer, and both arms could be zero for a reason neither wording touches.

**And the stability numbers are not a finding.** 0.60 against 0.40 on n=10 is inside binomial
noise — na-j2w's own post-mortem says exactly this, about this exact surface. Worse, the trigger
arm degraded twice, so its distribution is over 8 clean runs and 2 fallbacks. Do not read a
direction into these two numbers.

### Where it points instead

na-j2w's second hypothesis was that *"the world view may simply not show a horizon worth planning
over"* — `faction.tech` fires every 5-10 turns and its world view is one turn deep. That is the
surviving explanation, and it is not a prompt change.

It is also, independently, what `docs/long-horizon-play.md` §6 proposes for a different reason:
a **trajectory block**, the same metric vocabulary as a short series with slope, derivable from
records the orchestrator already holds and needing no adapter change. The design put it at step 2
because it was cheap. This result moves it: it is not the cheap step before the interesting one,
it **is** the one the measurement points at, and issuing is downstream of it.

So the build order changes. Not "make issuing attractive with better words, then measure", but
"give the decision a horizon it can see, then ask again whether it plans".
