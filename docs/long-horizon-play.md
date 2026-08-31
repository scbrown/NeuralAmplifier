# Long-horizon play — a game as the unit of reasoning, and combat as its own problem

> **Status: design. Nothing here is built.** The measurements in §1 are real and taken from
> committed runs; everything from §3 onward is a proposal. Tracked as `aegis-n8zmq`.

Stiwi, 2026-08-30: *"na is too focused on single decisions; these runs need to encapsulate
everything and truly needs to be reasoned about how to make decisions longer than a turn and
tactical combat."*

This document is the reframing that asks for. It supersedes the turn-local framing of the
`na-xb1` ladder's M1/M2 — not the ladder itself, whose definition of done (2 of 3 seeded wins)
is already a whole-game criterion. What changes is what we build and measure on the way there.

Companions: [turn-scoped-play.md](turn-scoped-play.md) built the ability to see and act across a
whole turn; this is the tier above it. [directives.md](directives.md) built the one carrier that
already crosses turns. [learned-memory.md](learned-memory.md) is the tier above *this* one —
across games rather than within one — and is unbuilt for different reasons.

---

## 1. The critique is measurable, and the measurement agrees

Three numbers from committed runs, before any argument.

**Zero decisions in the only real game we have ever measured were answered by reasoning produced
on an earlier turn.** Across all three M1 arms — 3,563 adapter records, turns 101–140 — every
record is `tier` `llm` or `deterministic`. Not one is `plan`, `queued` or `deferred`, the three
tiers that exist precisely to mark an answer a previous turn decided.

```text
evals/runs/na-6db/baseline.faction7.jsonl          720 records   deterministic 720
evals/runs/na-6db/brain.faction7.jsonl            1400 records   llm 700 · deterministic 700
evals/runs/na-6db/brain-directive.faction7.jsonl  1443 records   llm 723 · deterministic 720
                                                                 plan/queued/deferred: 0
```text

**The agent has never issued a directive in a measured game.** `evals/runs/na-mmp/decisions.jsonl`,
723 orchestrator records from the brain-directive arm: `plan.issued` is empty on every one, and
`plan.rejected` is too, so this is not a validator turning them away. The only directive that ever
existed in a real game was hand-written before the run.

**And the mechanism is not the reason** — that is worth stating precisely, because "nothing issues
a directive" reads as unbuilt and it is not:

| bead | what it established | state |
| --- | --- | --- |
| `na-43h` | the path works end to end in a live game — issued on `faction.tech` at turn 135, shown on a later `base.production`, `plan.json` created by the issue | closed, acceptance met |
| `na-j2w` | a real model, invited, issues **zero directives in twenty runs** | closed as *run and reported, not answered* — there was no treatment to measure |
| `na-1gl` | why: the model-facing schema said *"Empty unless setting direction"*, nine words whose only actionable clause points at empty. Fixed in `25b2a72` to *"Issue when this choice binds later turns; else empty"* | closed, and it says in its own close line that **measuring an actual directive is na-j2w's rerun, which this unblocks** |

That rerun was never done, so it was run for this bead — **and the answer is that the wording is
not the blocker.** `evals/runs/aegis-n8zmq`, pre-registered before either arm was read:

| arm | runs | degraded | directives issued |
| --- | --- | --- | --- |
| the wording as it ships (suppressor first) | 10 | 0 | **0** |
| na-1gl's ordering (trigger first) | 10 | 2 | **0** |

With na-j2w's twenty that is **0 directives in 40 runs of `faction.tech` across three prompt
configurations and two lanes.** The hypothesis was a good one — na-1gl fixed the structured-output
schema in `response.py`, and `claude -p` has no structured-output mode, so
`claude_code_brain._JSON_INSTRUCTION` states the shape in words and still leads with *"`directives`
is usually `[]`"*. One lane fixed, and the lane the game actually uses not. Reordering the clauses
changed nothing.

What survives is na-j2w's *other* hypothesis: **the world view does not show a horizon worth
planning over.** `faction.tech` fires every five to ten turns and its world view is one turn deep,
so setting faction policy is optional extra work with no visible payoff inside the turn.

That is not a prompt change, and it is — arrived at from the opposite direction — the trajectory
block in §6. Which reorders this document's own build order: §10 step 2 was placed before step 3
because it was cheap, and the measurement says it is not merely cheap but **the step the other one
depends on**. Do not re-word and re-run.

**The brain changes its mind two to three times as often as the deterministic tier does, on
the same board and the same turns.** Consecutive-turn pairs on one base whose answer changed
(`scripts/carry_report.py`):

| arm | surface | tier | rework |
| --- | --- | --- | --- |
| brain | `base.hurry` | llm | 78/668 = **0.117** |
| brain | `base.defend_goal` | deterministic | 41/669 = 0.061 |
| brain + directive | `base.hurry` | llm | 87/670 = **0.130** |
| brain + directive | `base.defend_goal` | deterministic | 26/670 = 0.039 |

Read as a bound rather than a matched pair — the two rows are different surfaces with different
answer spaces, so this compares how much two different questions moved on one board, not one
question answered two ways. A matched control needs both tiers on a single surface and does not
exist yet. What it does establish is that the brain's churn is not obviously explained by the
board changing under it.

**The one hand-written directive changed forty turns of play.** `hold-reserve-floor` was in force
on all 723 decisions and followed on 661 — attention 0.91, zero overrides. Its arm ended the run
with more energy than it started:

| arm | hurry decisions | `hurry:now` | credits spent | reserves t101 → t140 |
| --- | --- | --- | --- | --- |
| brain | 699 | 54 | 2,042 | 673 → **185** |
| brain + one directive | 720 | 51 | 1,435 | 647 → **1,189** |

Nearly the same number of rush-buys, 30% less spent, and a reserve curve that compounds upward
instead of collapsing. *(confidence:extracted for the figures — recomputed from the committed
logs. confidence:inferred for the causal reading: the arms differ in decision count and starting
reserves, so this is one save with one uncontrolled variable, not an A/B. It is suggestive, and
`na-clk`'s seeded harness is what would settle it.)*

Put together these say something sharper than "we should plan more". **The carrier works and the
writer is missing.** A long-horizon constraint, injected from outside, was read, obeyed, and
visibly changed the trajectory. Nothing in the system has ever produced one.

### The M1 result is this defect, diagnosed independently

`na-6db` asked whether the brain's `base.hurry` overrides beat Thinker's, and found they do not.
Its conclusion, written before this document existed:

> Thinker declining a hurry is not conservatism. It is the correct call about compounding, and
> **the brain has no representation of that anywhere in its world view.**

That is the whole thesis in one sentence, arrived at from the other end. The brain was shown
"81 credits buys 7 turns" and answered correctly *for the turn in front of it*. The cost it could
not see was the compounding — a quantity that only exists across turns, that no field of the
world view carries, and that the deterministic tier encodes implicitly in a rule the brain was
never given.

So the redesign is not a new ambition bolted onto a working system. It is the fix for the failure
the project's first outcome measurement already found.

---

## 2. Four defects, separable

**(a) The unit of reasoning is one decision.** Four carriers already outlive a decision —
directives, queued answers (`queued.py`), unit intents (`intents.py`), the turn plan table
(`turnplan.py`). Every one is filled, if at all, as a side effect of answering a turn-local
question. There is no moment in the loop whose job is *"what are we doing over the next thirty
turns"*. Nothing is ever asked that question, so nothing ever answers it.

**(b) The state a decision reasons over is a snapshot.** `WorldView.metrics` carries this turn's
thirteen numbers. It carries no trajectory (was `mineral_surplus` rising or falling for ten
turns?), no ledger of what we already committed to, no forecast. `na-61c2` measured that adding
*recent build history* to one base moved flip-flopping from 0.10 to 1.00 continued — and 0.00
when the case genuinely changed, so it is memory rather than an anchor. That result was taken as
a fix for one surface. It is the general case, and it was never generalised.

**(c) Combat is not reasoned about — and, on our slot, does not happen.** See §6. This is worse
than it has been written down as, and it is not a gap in the brain.

**(d) The measurement unit is a decision too.** Every eval scores a distribution over one
captured world view: stability, citation rank, whether a fact was read. `domain_eval` scores
*presence* gates — "at least one offensive engagement" — which asks whether a thing happened at
all, never whether it was any good. `ab-outcomes` compares two trajectories at snapshots and its
own write-up flags the limit: *"Snapshots, not integrals."* Nothing scores a game. So even if
(a)–(c) were fixed tomorrow, no committed instrument would show it.

---

## 3. The architecture: four cadences, and the slower one is the faster one's input

```text
DOCTRINE   once per game, + on discontinuity      whole game     posture + war aims
   |
CAMPAIGN   every ~10 turns, + on trigger          10-40 turns    commitments
   |
TURN       every turn                             1 turn         plan table, orders
   |
DECISION   per raised decision                    now            an action id
```

Each tier's artifact is an **input** to the tier below. That single property is what makes a
decision compound instead of being re-derived: a base production choice at turn 60 is not asked
"what should this base build", it is asked "what should this base build, given that we are
eleven turns into a committed expansion arc that wants ten bases by turn 80 and currently has
seven".

The bottom two tiers are built. The top two do not exist.

**This is deliberately not a new planning system.** A commitment is not a new grammar with its
own validator and its own record; it **compiles down** to the carriers already built, already
validated, already instrumented:

| a commitment says | compiles to | which already |
| --- | --- | --- |
| "keep reserves above 300 until turn 60" | a `Directive` | refuses an unmeasurable metric at issue time |
| "keep building formers here while surplus holds" | a `QueuedAnswer` | refuses an uncheckable predicate |
| "this rover is going to the isthmus by turn 44" | a `UnitIntent` | refuses a missing horizon or trigger |
| "here is this turn's whole answer" | a `TurnPlan` | answers in milliseconds at `tier=plan` |

The value of compiling rather than inventing is that each of those carriers **refuses what it
cannot check**. A commitment that cannot be checked is therefore refused at plan time, by
machinery that already exists, for reasons already argued. Building a parallel plan object with
its own looser rules is how this design would fail: it would accumulate unfalsifiable intentions
and read, in the record, exactly like strategy.

---

## 4. The Campaign Ledger

One new state object, faction-scoped, versioned by turn. `campaign.py`.

```jsonc
{
  "faction_id": 7,
  "revision": 4,
  "revised_turn": 62,
  "posture": "expand",              // closed set — see below
  "commitments": [
    {
      "id": "coast-arc",
      "goal": "Ten bases by turn 80, coastal first",
      "rationale": "Three unclaimed river mouths west; Hive is landlocked and slow to sea.",
      "opened_turn": 41,
      "horizon_turn": 80,
      "status": "active",           // active | achieved | abandoned | lapsed
      "compiles_to": [
        {"kind": "directive", "id": "bases-by-80"},        // base_count at_least 10 by turn 80
        {"kind": "directive", "id": "bases-by-60"},        // the intermediate checkpoint
        {"kind": "queued",    "id": "coastal-pods"}
      ],
      "reviews": [
        {"turn": 51, "observed": {"base_count": 6}, "verdict": "keep",
         "why": "on the curve; 6 against a 5.5 checkpoint"},
        {"turn": 61, "observed": {"base_count": 7}, "verdict": "revise",
         "why": "one pod lost to mind worms; horizon 80 -> 88 rather than abandon"}
      ]
    }
  ]
}
```

Three things about this shape are load-bearing.

**`posture` is a closed set** — `expand`, `consolidate`, `tech`, `war`, `defend` — for the same
reason `metrics.py` is a closed vocabulary. An open string is unscoreable; "play adaptively" can
never be confirmed, refuted, or compared between games. A closed set can be counted, and a
posture that never once changed across a 250-turn game is a finding.

**`compiles_to` is the whole enforcement story.** The ledger holds the *reason*; the carriers
hold the *mechanism*. Nothing reads the ledger to steer a decision — the existing retrieval walk
reads the directives it compiled to, exactly as it does today. This keeps one enforcement path
rather than two that can disagree, and it means the ledger cannot silently become the thing that
looks like a plan while steering nothing.

**`reviews` is the record that makes compounding measurable, and it is the part most likely to be
dropped as bookkeeping.** A decision that persists is only interesting if it was *re-affirmed
against evidence*. A commitment nobody revisited and a commitment kept on purpose are
indistinguishable from the outside, and the first one is the failure. So a review that ends in
`keep` writes a row too — with what the board said at the time. Without that, "commitment held to
its horizon" is a statistic about nobody looking.

`status` distinguishes `abandoned` (reviewed, dropped, reason recorded) from `lapsed` (horizon
passed with no review). **Lapsed is the failure mode to score against.** It is the plan-shaped
version of the unmeasurable directive that reads as compliance: a plan that quietly expired looks
in any summary exactly like one that was kept.

---

## 5. The strategic review: a second reason to wake

Today an agent is woken by exactly one thing — a decision the engine raised. Add a second:
the **strategic review**, raised at `mod_turn_upkeep` (the between-turns seam `turns.py` already
uses) when any of these hold:

- the review cadence has elapsed (~10 turns; a knob, not a constant);
- a commitment's `horizon_turn` has arrived;
- a commitment's review trigger fired — the predicate grammar `queued.py` already validates;
- a **discontinuity**: war declared or ended, a base lost or captured, HQ relocated, first
  contact with a faction, a project completed by anyone.

The discontinuity list is the interesting one. A cadence alone reviews strategy on a timetable
that has nothing to do with the game; the events above are the moments a human player stops and
re-thinks, and every one of them is something the adapter already observes or can.

### Its contract shape is genuinely new, and small

A review is **not** a decision, and forcing it into `WorldView` + `action_space` would be the
wrong shape twice over: there is no action space, and the answer is not an action id.

| | decision | strategic review |
| --- | --- | --- |
| scope | base / unit / turn | **faction** |
| state | this turn's snapshot | **trajectory** (§6) + the current ledger |
| action space | pick one of N | **none** |
| answer | an `action_id` | **a ledger revision** |
| validation | `validate()` against the space | **compile** every commitment; a carrier that refuses, refuses |
| blocking | the game is stopped | the game is stopped — same seam, same deadline discipline |
| record | one decision record | one **review record**, same log, `tier="review"` |

`validate()` does not apply, and the substitute is not weaker: a revision is accepted only if
every commitment in it compiles to carriers that all accept. A commitment naming a metric nobody
reports is refused while the model that wrote it is still in the loop — the exact discipline
`directives.py` argues for, applied one tier up.

**A review must be able to answer "no change".** An empty revision is a legitimate and common
answer, and it still writes a review record with what the board said. A design in which the model
is asked every ten turns and can only respond by changing something will change something every
ten turns, which is churn wearing a plan's clothes.

---

## 6. Trajectory, not snapshot — and it costs no adapter work

The missing representation `na-6db` named. Add a **trajectory block** to the world view: the same
metric vocabulary, as a short series rather than a scalar.

```jsonc
"trajectory": {
  "energy_reserves": {"now": 185, "t-5": 402, "t-10": 588, "t-20": 673, "slope_per_turn": -12.2},
  "base_count":      {"now": 18,  "t-5": 18,  "t-10": 18,  "t-20": 18,  "slope_per_turn": 0.0}
}
```

Two properties make this cheap and worth doing first.

**It needs nothing from the adapter.** The orchestrator already receives every world view and
every outcome, and `turns.py` already folds them into a per-turn view. The series is derivable
from records the service already holds. No new measurement, no new contract field from the
engine, no invariant-2 question about the orchestrator learning where an engine files its
economy — these are the adapter's own numbers, kept.

**It is the shortest path to the M1 defect.** Shown a reserve curve falling 12 credits a turn,
a hurry decision can see the compounding it is contributing to. That is not a guarantee it will
weigh it correctly; it is the difference between weighing it wrongly and being unable to weigh it
at all.

Two rules, both inherited from mechanisms that already got this right:

- **A metric absent from a turn is a gap in the series, never a zero.** `metrics.py` already has
  the `accumulated` flag for exactly this — `energy_income` is genuinely unreadable inside the
  production phase, so every base-scope world view omits it, and a series that filled it with 0
  would manufacture a collapse from an adapter's honest silence.
- **A short series says so.** At turn 4 there is no `t-20`. The field is absent; it does not
  read `0` and it does not read `null` in a way that invites arithmetic.

---

## 7. Combat as its own reasoned sub-problem

### 7.1 The thing that must be said first: on our slot, nothing moves our units

This is not "combat is delegated to Thinker's unit AI". It is that **Thinker's unit AI does not
run for the faction we drive**, and the fork has measured this five separate times.

The LLM faction is pinned to the **human slot** (`na-61c`, and correctly — an AI slot inherits
difficulty handicaps that make an outcome uninterpretable). The engine invokes its per-faction AI
passes for AI factions only. From `neural.cpp`, which calls this "the third instance of one
pattern, and the largest":

```text
mod_bases_reset        never called for a human faction -> no production is chosen
mod_enemy_move         never called for a player unit   -> nothing moves our units
move_upkeep(UM_Player) reachable only from inside mod_enemy_move's plr_unit branch,
                       so for the faction we drive it never runs at all
mod_enemy_turn         the per-faction unit turn; engine calls it for AI factions only
```

Measured, not inferred: `move-stats` reports `enemy_move=0` and `upkeep_ok=0` for our faction
across a whole 122-turn game. `move_upkeep` is what fills `mapdata` (tile safety, roads, patrol
nodes) and `plans_upkeep` (main region, naval priority, enemy ranges, base-site scoring). **For
our faction, every downstream decision has been running on a cleared map and a zeroed plan.**

Three consequences the rest of this section is built on:

1. **We field an army that stands still.** Door 2's `move` / `skip` are not an optimisation over
   the engine's unit cycle — for our slot they are the only thing that moves anything.
2. **There is no deterministic tier to degrade to.** NA's whole safety architecture is
   engine-authoritative-with-a-fallback. On unit surfaces for the human slot there is no fallback
   to fall back to, whatever the registry says about the AI path — until `unit-turn` (below).
3. **`na-1lj` and `na-bg4` are not bugs beside the ladder, they are this.** A colony pod frozen
   for 23 turns with a valid waypoint, and a human slot that founds one base and never a second,
   are the same absence seen from two angles.

`na_run_player_unit_turn()` now exists in the fork — `mod_enemy_turn` for the human slot, gated
on `manage_player_units` and `unit_turn 1`, **off by default**. That is the deterministic tier
this section needs, and it is the prerequisite for everything in it: an LLM tier with no fallback
is not a tier, it is a single point of failure attached to an army.

### 7.2 Why combat gets its own hierarchy rather than more surfaces

The economy's decisions are *independent and repeated*: fifty bases, each answerable alone, the
same question every turn. Combat's are *coupled and spatial*: a stack's move is worthless or
fatal depending on three other stacks, and the same move is right at turn 40 and suicide at turn
41. Answering "should this rover attack" fifty times in isolation is the single-decision failure
in its purest form — and the surface registry has thirty-one unit-scope ids waiting to invite
exactly that.

So combat gets the same three cadences, with its own objects:

**War aims** (doctrine). Who we are at war with and what we want out of it: survive, take a named
base, take N bases, eliminate. A commitment in the ledger like any other, with a horizon.

**Theatres** (campaign). The new object, and deliberately coarse. A theatre is a named front:

```jsonc
{"id": "eastern-isthmus", "objective": {"base_id": 41}, "posture": "advance",
 "units": [812, 907, 913], "strength_budget": 6, "horizon_turn": 58,
 "abandon_if": [{"metric": "military_units", "comparator": "at_least", "target": 18}]}
```

Coarse **because the orchestrator has no tile model and must not grow one**. Door 2's `move`
refuses an unexplored destination using the engine's own `is_known`, and
[turn-scoped-play.md §5](turn-scoped-play.md) is explicit that this engine gate is the only tile
gate there is — by design, because a second map in the orchestrator is a second source of board
state and the fair-play story rests on there being one. A theatre is therefore defined by an
objective and a set of unit ids, not by a map the orchestrator maintains.

**Engagements** (turn). "Should this stack attack now" — the only genuinely turn-local combat
question, and therefore the only one that should be a decision surface. It inherits its frame
from its theatre, which is the entire point: the question stops being "is this a good attack" and
becomes "does this attack serve the advance on base 41, given a strength budget of 6".

### 7.3 What blocks it, in the order it has to be unblocked

Combat reasoning is gated on state the world view does not carry, and the project's own rule
applies — *emit it from the adapter first, add the name second*. `intents.py` already documents
three of these as triggers it wanted and could not express.

| # | blocker | who fixes it |
| --- | --- | --- |
| 1 | `unit-turn` is off by default; without it our units have no deterministic tier | fork: measure it on, then default it |
| 2 | no per-unit state in the world view — a theatre cannot be populated | adapter: emit units (id, type, health, position, orders) for our faction |
| 3 | no `at_war` — `factions_at_war` is a *count*, so a war aim cannot name the Hive | adapter emits, then `metrics.py` |
| 4 | no per-faction relation state (treaty, vendetta, contacted) | adapter, then vocabulary |
| 5 | no unit-scope surface is applied — `unit.attack` / `unit.dispatch` are registry entries with no hook | fork + orchestrator |

Item 2 is the one to be careful about. **Emitting our own units is a fog question even though
they are ours**, because a unit list carries positions, and positions of *enemy* units — the
thing a theatre most wants — must come from the engine's own visibility state and nowhere else.
The rule that has held so far is that the engine refuses what we should not see; the same rule
has to hold here, or the first genuinely military world view is also the first cheat.

**Nothing in §7.2 should be built before item 1 is measured.** An LLM combat tier layered over a
faction whose units do not move at all would be measured against a floor of zero, and would look
like a triumph regardless of whether it was any good.

---

## 8. Measurement: a game is the run

`game_eval` — deterministic, no model, no network, over one game's records. Same discipline as
`domain_eval`: the game is expensive to run, so reading what it already produced must be free.

Five families, and the first two are the ones that answer Stiwi directly.

**Carry rate.** The fraction of applied decisions whose answer was produced by reasoning from an
*earlier* turn, and the median age in turns of that reasoning. `tier ∈ {queued, deferred}`, plus
any decision whose `plan.followed` names a directive with an earlier `issued_turn`. **Today's
baseline is 0 for the first clause and 0.91 for the second on a hand-written directive** (§1) —
measured, so the design has a number to beat rather than a claim to make.

`plan` is deliberately **not** a carrying tier. `turnplan.py` is explicit that a plan table is
valid for exactly the turn it names and dies with it, so it is *bulk* reasoning rather than
*long-horizon* reasoning. Counting it would let a fast agent read as a strategic one, which is
the confusion this whole report exists to prevent — and bulk-turn mode is the feature most likely
to be pointed at as evidence that the problem is already solved.

**Rework rate.** How often a decision reverses one made for the same base and surface N turns
ago *with no triggering change on the board*. The qualifier is the whole measurement: reversing
because the board changed is good play, and reversing because nothing was remembered is the
defect — and today the report can only measure the reversal, not the qualifier, which is stated
in its output rather than left to be discovered. The M1 baseline is in §1: 0.117 and 0.130 on
`base.hurry` against a deterministic control of 0.061 and 0.039. It is reported **per surface and
per tier, never totalled**, because a total over a run mixing a brain-driven surface with an
engine-answered one moves when the surface mix does, which would make the headline respond to
configuration rather than to play.

**Commitment fidelity.** Of the commitments opened: kept to horizon, achieved early, abandoned
with a recorded reason, **lapsed silently**. The last column is the one to watch, and a design
that scores well on the first three while lapsing half its commitments has not worked.

**Trajectory integrals, not snapshots.** Area under the curve for `base_count`, `mineral_surplus`,
`labs_output`, `military_units` across the whole game, alongside the endpoints. A run that spikes
and collapses must not score like one that compounds — which is precisely the difference between
the two M1 arms in §1, and precisely what a pair of endpoint snapshots cannot express.

**Outcome.** Win/loss, victory type, final turn. `na-clk` already built the observability: the
command channel's `game-state` reports raw `GameState` bits and per-faction base counts, and it
reports bits rather than a verdict on purpose.

Two rules for the whole instrument:

- **Report the baseline before the treatment.** Carry rate and rework rate are computable from
  the *existing* committed logs, today, with no game and no changes. Measuring them first is what
  makes any later improvement a comparison rather than an announcement.
- **No verdict from one save.** `ab-outcomes` refuses to render one and says why; this inherits
  that. One game's trajectory is evidence. The ladder's N seeds are a result.

---

## 9. What this is not, and what it does not change

- **It is not learned memory.** [learned-memory.md](learned-memory.md) is *across* games — a
  tactic learned in game N surfacing in game N+1. This is *within* one game. They compose (a
  ledger is exactly what postgame extraction would read) and neither needs the other first.
- **It is not a second source of board state.** No tile model, no unit tracking the orchestrator
  maintains, no map. The trajectory block is a series of numbers the adapter already sent; a
  theatre is unit ids and an objective. The rule in
  [agent-play.md §5](agent-play.md) stands unchanged.
- **It does not change the contract for decisions.** Same world view plus two new blocks, same
  orders, same validation, same record. The review is a new record type in the same log, not a
  new log.
- **It does not relax fairness.** Human slot, `manage_player_bases`, declared handicaps,
  fog-gated foreign deltas. §7.3 item 2 is the only place this design goes near that line, and it
  is flagged there rather than left to be noticed.
- **It does not replace the deterministic tier.** It presupposes one — most sharply in combat,
  where §7.1 shows we have been running without.

## 10. Build order

Each step is measurable on its own, and the early ones need no game.

1. **Carry / rework over committed logs.** No game, no model, no network. Produces the baseline
   the rest is judged against. **Built — `scripts/carry_report.py`, `just carry-report`**, and
   its output is §1. Trajectory integrals, commitment fidelity and outcome are the remainder of
   §8 and are not built.
2. **Trajectory block.** Orchestrator-only, no adapter change. Exit: a `base.hurry` world view
   carries the reserve slope, and the M1 save re-run shows whether the brain's spending changes.
3. **Directives issued by the agent** — and this is **downstream of step 2, measured, not merely
   after it**. The `na-j2w` rerun ran for this bead and returned 0 issued in 20 runs across both
   wordings (§1), so the remaining lever is the horizon step 2 adds, not the prompt. Exit: a run
   in which `plan.issued` is non-empty and a later decision follows a directive the agent wrote.
   Prerequisite for anything called a commitment.
4. **The ledger and the strategic review.** Exit: a commitment opened at turn T, reviewed at
   T+10 against the board, and a decision at T+11 answered under it — the `>1 turn` carry the
   bead asks for, demonstrated rather than asserted.
5. **`unit-turn` measured and defaulted on.** Exit: `move-stats` shows a non-zero unit turn for
   our faction, and the ladder's base count stops flatlining.
6. **Combat state in the world view** — units, `at_war`, relations. Adapter first.
7. **Theatres and one engagement surface.** Last, and only against a working deterministic tier.

Steps 1–3 are all buildable now and none of them needs the game to be running.

---

## See also

- [directives.md](directives.md) — the carrier that works and that nothing writes
- [turn-scoped-play.md](turn-scoped-play.md) — the tier below: one whole turn
- [learned-memory.md](learned-memory.md) — the tier above: across games
- [decision-inputs.md](decision-inputs.md) — what a world view carries and why
- [game-surface.md](game-surface.md) — the frozen registry §7 would extend
