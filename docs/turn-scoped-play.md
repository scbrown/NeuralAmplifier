# Turn-scoped play — seeing the turn, learning the outcome, and choosing the order

An agent playing today answers **one decision at a time, blind to its siblings, and never learns
what happened**. It cannot see the other fifty bases waiting behind the one in front of it, it is
never told whether the order it gave was actually applied, and it cannot say "hold this one, ask
me again once I have moved the units".

Companion to [agent-play.md](agent-play.md), which describes the mode as **built**; this describes
what it cannot yet do and what the code actually permits. Tracked as `na-8ja`.

**None of this is built.** Said plainly and up front, in the manner of
[policy-harness.md](policy-harness.md), because a design doc that reads like a feature list is how
a plan becomes a claim.

---

## 1. What is being asked for

1. **See all events for a turn** — the whole picture, not the oldest single decision.
2. **Decide against that picture** — spend a limited pool where it matters most, across bases.
3. **Receive feedback from execution** — did the order apply, and did the engine keep it.
4. **Issue our own movement orders** — pick a unit or a base directly and command it, rather than
   waiting for the engine's cycle to offer it, so dependent moves can be chained.

## 2. There are two doors into the game, and we have only ever used one

This is the reframing the rest of the document rests on.

| | **Door 1 — the decision hook** | **Door 2 — the command channel** |
| --- | --- | --- |
| Who starts it | the engine asks | **the agent acts** |
| When | inside `mod_base_build`, mid-turn-processing | on the window procedure, while the game waits |
| Must answer | **now** — it returns an item id | no — nothing is blocked |
| What it can address | the one thing being asked about | **any unit, any base, any time** |
| Status | built, and all agent play goes through it | built, and used only for `shot` / `click` / `key` / `observe` |

Door 2 is how a *human* plays. A player does not wait for the unit cycle to reach a former — they
click the former they care about and give it an order. The engine has always allowed this; we have
simply never used it for orders.

**Everything asked for in §1 gets easier through door 2**, and requirement 4 becomes almost
trivial: an agent that can address any unit directly does not need to defer a decision inside a
cycle, because it was never trapped in the cycle to begin with.

## 3. Door 1: why it cannot be made to wait

```c
int na_decide_base_production(int base_id, int native_choice, int has_gov) {
    ...
    return applied;          // an item id, consumed by the engine immediately
}
```

The hook POSTs `/decide`, blocks up to `conf.llm_timeout_ms`, and hands an item id straight back.
**There is no return value meaning "ask me later"** — the adapter's own comment says
*"Synchronous because the engine is not thread-safe"*.

There is one relaxation available and it is worth knowing, because it is nearly free. The engine
asks the same base **several times per turn and the last answer wins** — measured, 21 of 24
base-turns fired twice, 11 of those pairs disagreeing. But the adapter suppresses the second ask:

```c
const int call_seq = na_next_call_seq(base_id);
if (call_seq > 1 && na_cache_get(base_id, &cached)) {
    if (na_item_is_legal(base_id, cached)) {
        return cached;              // replay — the agent is never asked again
    }
```

So a *revision* of a build choice costs one branch: don't cache, and `call_seq == 2` falls through
to a real `/decide`. Useful, but it is a patch on door 1 — it only ever revisits a decision the
engine chose to raise, on the engine's schedule.

## 4. Door 2: the command channel already exists, and it already has a result channel

`na_command_tick` (called from `gui.cpp:575`, inside the window procedure):

- polls the file `na-command` at **4 Hz**;
- **consumes the file before acting** — deliberately, so a command that crashes the game cannot
  re-run and crash it again on the next launch;
- writes `na-command-result` as JSON: `{"command", "detail", "ok", "turn", "halted"}`.

Today it carries `shot`, `click <x> <y>`, `key <vk>`, `load`, `enter`, `audit <faction>`,
`observe`, `observe-tech`. It is a **complete request/response loop that already reports success
and failure** — which is most of requirement 3, built and unused for orders.

### It is already safe against the re-entrancy hazard

[agent-play.md §4](agent-play.md) explains that the decision wait calls `PeekMessage(PM_NOREMOVE)`
and **dispatches nothing**, because dispatching re-enters the window procedure and mutating game
state from inside a decision hook corrupts turns.

`na_command_tick` runs *from* that window procedure. So while a decision is outstanding, no
message is dispatched, and **commands do not run**. Orders and decisions are serialised against
each other by a property the code already has, for a reason that had nothing to do with this.
That is a load-bearing accident and it should be asserted by a test before anything depends on it.

## 5. Building our own movement orders

The engine primitives are present and heavily used inside Thinker already:

| primitive | uses | for |
| --- | --- | --- |
| `order_veh` | 352 | general unit orders |
| `set_move_to` | 56 | move a unit to a tile |
| `veh_at` | 46 | find the unit on a tile |
| `veh_skip` / `mod_veh_skip` | 43 / 35 | end a unit's turn |
| `action_build` | 16 | former/colony actions |

So the work is a set of **semantic verbs on door 2**, each a thin wrapper over a primitive the
engine already trusts:

```text
move  <veh_id> <x> <y>      -> set_move_to
skip  <veh_id>              -> veh_skip
build <base_id> <item_id>   -> the base queue setter
hurry <base_id>             -> the hurry path, with its own affordability gate
```

with the existing result file reporting `ok` plus a reason. The orchestrator exposes them, and the
MCP surface gains the matching tools.

**The engine stays authoritative.** These wrappers must call the engine's own validators — the
same `na_item_is_legal` discipline door 1 already applies — so an illegal move is refused by the
engine rather than by our opinion of the rules. A wrapper that bypassed a validator would be the
one place in this design capable of corrupting a game.

### Three constraints to design against

- **Throughput.** 4 Hz, and `na_command_tick` reads the file with a single `fgets` — **one command
  per file**. Fifty units is twelve seconds of round trips. Batching (multiple lines, or a
  newline-delimited body) is a real change to the intake, not a tuning knob.
- **Fog.** A world view is fog-gated by the orchestrator before an agent sees it. A direct order
  does not travel that path, so an agent could command a unit toward a tile it should not know
  about. Fair play is a project invariant; ordering must be gated too, not just seeing.
- **Whose turn it is.** Commands run whenever the window pumps, including during another
  faction's turn. An order must be refused unless it is our faction's turn and the game is not
  halted — the result record already carries `turn` and `halted`, which is the check half-built.

## 6. Reversibility still differs per surface

| surface | revisable? |
| --- | --- |
| `base.production` | **Yes** — a queue setting; last write wins |
| `base.hurry` | **No** — spends energy credits irreversibly at apply |
| `faction.tech`, `faction.se` | Need checking; fire once per faction-turn |

`na.toml` already singles out `base.hurry` as *"the one that spends something irreversibly once it
applies"*. This matters less under door 2 than door 1 — an agent that chooses its own order of
operations can simply do the irreversible things last — but it must still be recorded per surface
in the frozen registry rather than assumed.

## 7. Execution feedback exists twice over, and is discarded both times

Door 2 has `na-command-result`, described above. Door 1 computes the outcome and writes it only to
a local file the orchestrator never reads:

- per decision: `tier`, `applied`, `applied_item`, `applied_item_name`, `fallback_reason`;
- and `na_verify_base_production`, which reads the base's item back **after** the apply and emits a
  separate event when they disagree:

```json
{"surface_id":"base.production","event":"divergence",
 "intended_item_name":"Recycling Tanks","applied_item_name":"Scout Patrol",
 "fallback_reason":"engine did not keep the applied item"}
```

Its comment states the point exactly, and it is the thing an agent can least infer:

> not "should the engine accept this" but "did it" … it is the one check that covers rules we have
> not learned yet.

The orchestrator's picture of its own effect is **absent, not incomplete**, while the adapter holds
it in full.

## 8. The turn view needs the adapter to speak first

When base #1 is asked, bases #2..#51 **have not been POSTed** and do not exist to the queue.
`/agent/next` returning "the oldest pending decision" is not an API preference — it is all the
orchestrator knows.

So the adapter must announce the turn's set up front. `mod_turn_upkeep` is the seam it already
describes as *"the engine's between-turns seam ... hooked into control_turn"*.

An announced set is a **forecast, not a promise**: the engine may raise fewer decisions than
expected. The view must distinguish `expected` from `raised`, or an agent will wait for a decision
that never comes.

## 9. Build order

**Phase 1 — outcome feedback.** Route what both doors already compute. Independent, changes no
blocking semantics, satisfies requirement 3.

**Phase 2 — order verbs on door 2.** `move` / `skip` / `build`, engine validators enforced, gated
on our-turn-and-not-halted. This is what delivers requirement 4, and it delivers it *properly* —
by giving the agent the affordance a human has, rather than by bending the decision cycle.

**Phase 3 — turn view.** Announce at `mod_turn_upkeep`, with `expected` vs `raised`.

**Phase 4 — batching and fog gating on orders.** Both are correctness work that only matters once
Phase 2 is real.

Door 1's no-cache revision (§3) is deliberately **not** on this list. Once an agent can command
units and bases directly, revising a build inside the engine's cycle is a second way to do
something Phase 2 already does better. Build it only if a case appears that door 2 cannot reach.

## 10. The failure mode to design against

**An order that silently becomes no order.**

Door 2's channel consumes the command file before acting, and writes exactly one result file that
the next command overwrites. Fire two commands quickly and the first result is gone; fire one
while the game is mid-animation and it may be consumed with nothing done. Neither leaves a trace
in the decision record.

That is the shape this project keeps meeting and keeps writing down: a knob that reports success
and does nothing (na-wzw, na-t3h, the plan store in a4deef7), a zero that means "not computed" and
reads as a measurement, an export that proves no record was lost and says nothing about the
values. An order channel with a single-slot result file is the same trap with a new surface.

So: **every issued order gets its own record**, correlated by an id the agent supplied, and an
order whose result is never observed is reported as *unknown* rather than assumed applied.

---

## See also

- [agent-play.md](agent-play.md) — the mode as it exists today
- [contract.md](contract.md) — the world view and orders this does not change
- [observability.md](observability.md) — where decision records go
- [game-surface.md](game-surface.md) — the frozen surface registry §6 would extend
- [headless-harness.md](headless-harness.md) — the command channel's other users
