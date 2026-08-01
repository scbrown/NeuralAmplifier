# Directives — how a long-horizon decision steers a short-horizon one

> **Status: implemented and measured on a fixture; not yet exercised by a live game.**
> The mechanism, the retrieval walk and the record instrumentation all ship
> (`orchestrator/src/neural_amplifier/directives.py`, `metrics.py`). No decision has yet
> *issued* one — every directive measured so far was hand-written (`na-43h`).

## The problem

A long-horizon decision can reason about a path over many turns. `faction.tech` fires every
five to ten turns and can genuinely argue "we are boxed in on a small continent, so a
naval-plus-ecology path suits our terrain and our character".

That conclusion then died with the response. The next `base.production` call — firing once per
base per turn — started from nothing and had no idea a path had been chosen. The reasoning that
justifies this project was being thrown away immediately after it was produced.

## The constraint that makes it work

**A directive may only reference a metric the world view actually reports.**

Everything else follows from this. "Keep energy reserves above 300" is a claim a later turn can
confirm or refute. "Play aggressively" is not — it can never be checked, never be scored, and
would accumulate forever while meaning nothing. So the metric vocabulary (`metrics.py`) is a
closed set, and a directive naming anything outside it is **refused at issue time**, while the
model that wrote it is still in the loop and could be told why.

The second half of the same discipline: a metric that is *in* the vocabulary but *absent* from a
given world view yields `satisfied: null` — explicitly **unmeasurable**, never satisfied. This
is the failure mode that would quietly hollow the whole thing out. A directive that silently
passes on every decision forever is worse than no directive, because it costs prompt tokens and
buys the appearance of strategy.

## Anatomy

```jsonc
{
  "id": "fund-weather-paradigm",
  "intent": "Bank energy credits to buy The Weather Paradigm outright the turn it is available.",
  "metric": "energy_reserves",          // must be in the vocabulary
  "comparator": "at_least",             // or at_most / increase / decrease / hold
  "target": 300,
  "priority": 7,                        // 1-10, see below
  "entities": ["fac:the-weather-paradigm"],   // datalinks ids — the retrieval keys
  "issued_turn": 30,
  "horizon_turn": 60                    // a plan that cannot fail teaches nothing
}
```

`increase`/`decrease`/`hold` are measured against a **baseline** stamped by the orchestrator from
the world view that issued the directive — not supplied by the model, which was shown the number
and would only be paraphrasing it back.

### Priority is advice, not an order

A directive that can never be broken is a plan that loses games. The number exists so a minor
decision can weigh its own action against a standing plan:

| Priority | Meaning |
| --- | --- |
| 9–10 | Survival. The game is lost otherwise. |
| 7–8 | A committed plan. Break it only for something urgent. |
| 4–6 | A preference worth real cost. |
| 1–3 | A tie-breaker. |

Overriding is **recorded, not prevented** (`Orders.overrode`). A priority-7 directive overridden
on every decision was mispriced; one never overridden may be costing more than it admits. Only
counting makes either visible.

## Value trade-offs

A priority number alone is unusable. Telling a base "there is a priority-7 plan to save energy"
asks it to guess the cost of ignoring one. So the orchestrator computes the trade-off from
`Action.effects` and the directive's metric — arithmetic on declared numbers, not an opinion:

```text
hurry:now → fund-weather-paradigm [p7]: energy_reserves -81 → 1, setback 5.8 turns
```

`setback_turns` uses a rate metric (`energy_income` for `energy_reserves`) and is **omitted rather
than invented** when no rate is reported — a setback figure with a made-up denominator is worse
than none, because it looks like the one piece of hard arithmetic in the block.

## Retrieval: the multi-hop walk

A game can accumulate hundreds of directives. Injecting all of them into every world view would
cost more per decision than the whole grounding budget and bury the two that matter — so a
directive is **retrieved like a fact, not read like a setting**.

The walk starts from what the decision actually does:

- **Hop 0 — what this decision touches.** An action whose `effects` change a metric pulls every
  directive about that resource. An entity on offer pulls every directive naming it.
- **Hop 1+ — what those directives are for.** Each directive reached contributes its own
  `entities`, which pull the directives about *them*.

```text
hurry:now changes energy_reserves by -81
  └─ hop 0  [p7] fund-weather-paradigm
       └─ fac:the-weather-paradigm
            └─ hop 1  [p8] terraform-expansion
```

So spending energy credits surfaces not just "we are saving" but the project being saved for and
the higher-order plan that project serves. Two hops reaches that chain; beyond it the connection
is too weak to be worth prompt space.

**Expansion is through entities only after hop 0.** Following metrics transitively would pull
every directive sharing a common measure — half a plan hangs off `base_count` — turning a
targeted walk into a broadcast with extra steps.

The path travels with the directive as `via`/`hop`, because **the path is the argument**. A
decision told "here are three directives" has to guess why they are in front of it.

Two rules keep the block honest:

- **No padding.** A directive the walk never reached is not shown even when there is room.
  Measured: with 16 directives in the plan, half of a four-slot block went to entries whose own
  explanation read *"not related to this decision"*. That teaches a model the block is mostly
  noise, which is the fastest way to have it stop reading the entries that matter.
- **No silent caps.** What the limit excluded is recorded in `PlanBlock.not_shown`. With hundreds
  of directives, "not mentioned" and "never offered" are different failures.

## Measurement

`PlanBlock` on every decision record, mirroring `KnowledgeBlock`:

| Field | Question it answers |
| --- | --- |
| `in_force` vs `followed` | Attention rate — is the plan read at all? |
| `overrode` | Was this directive mispriced? |
| `unmeasurable` | Adapter gap — a metric nobody reports |
| `unsatisfied` | Checked and failing, which is a different problem |
| `not_shown` | Did relevance selection hide it? |
| `conflicts` | Actions that would break a currently satisfied directive |
| `entities_cited` | Did the decision reason from an entity only the plan showed it? |

### Two id spaces that are deliberately the same

A directive's `entities` **are** grounding fact ids — that is what makes the walk above work at
all. But they reach the brain through the `directives` block, not the `grounding` block, and the
two readers of "what was offered" originally disagreed about that:

- The **citation guard** (`hank.py`) read the offered set from `grounding` alone, so citing an
  entity a directive had shown looked like a fabricated citation. Measured at three to four runs
  in five carrying `cited facts that were never offered: fac:the-weather-paradigm` for an id the
  world view had genuinely put in front of the model.
- **`summarise`** filters citations to grounding before recording them, so the same citation was
  dropped from `quipu_cited` — the plan's contribution was invisible in both directions at once.

The rule now: both sources count as **offered**, and only grounding counts as **retrieved**.
`utilisation` stays a statement about retrieval — folding entities into it would push it above
1.0 the moment a decision cited one, and make a retrieval metric drift whenever the plan changed
shape — and a citation the plan alone offered lands in `entities_cited` instead of vanishing.
Where both offered an id, grounding wins; there it is already counted as retrieval doing its job.

## What this bought

On the turn-35 `base.hurry` observation (University Base, Colony Pod, 81 credits to save seven
turns, 82 in reserve), Haiku 4.5, ten runs each:

| Plan | runs | stability | plan attention |
| --- | --- | --- | --- |
| none | 10 | **0.60** (6/4 split) | — |
| one priority-7 saving directive (`at_least 300`) | 10 | **1.00** (unanimous `hurry:none`) | 0.90 |
| two directives, the second reached at hop 1 | 5 | **0.80** | 0.60 |

The third row is the honest counterweight to the second: a different plan on the same
observation moves the answer strongly toward `hurry:none` without pinning it. What replicates
across both is the *direction* and the attention — the saving directive was followed on every
run of both configurations. What does not replicate is the exact 1.00, so it should not be
quoted as though the mechanism makes a contested surface deterministic.

The surface was never short of *rules*. It was short of the opportunity cost of spending 81 of
82 credits, and nothing in the world view had ever said what else that energy was for. See
`na-56o`.

## Known gaps

- Nothing issues a directive yet (`na-43h`).
- `faction_state` is built but unverified against a running game (`na-b4v`).
- The vocabulary is eleven names, six of them reported. Military posture, terraforming progress
  and diplomatic standing cannot be expressed at all yet (`na-c17`). `drone_total` is in the
  vocabulary and *not* reported, which is exactly the mistake the vocabulary exists to prevent —
  it needs emitting or removing.
- Attention and override rates come from ten replays of one fixture. They show the mechanism
  works; they say nothing about whether directives help (`na-mmp`).
