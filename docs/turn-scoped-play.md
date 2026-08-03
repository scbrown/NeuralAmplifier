# Turn-scoped play — seeing the turn, learning the outcome, and choosing the order

An agent playing today answers **one decision at a time, blind to its siblings, and never learns
what happened**. It cannot see the other fifty bases waiting behind the one in front of it, it is
never told whether the order it gave was actually applied, and it cannot say "hold this one, ask
me again once I have moved the units".

Companion to [agent-play.md](agent-play.md), which describes the mode as **built**; this describes
what it cannot yet do and what the code actually permits. Tracked as `na-8ja`.

**Status: phases 1–4 are built.** Said plainly and up front, in the manner of
[policy-harness.md](policy-harness.md), because a design doc that reads like a feature list is how
a plan becomes a claim — and a doc that keeps saying "none of this is built" after some of it is
becomes its own kind of lie.

One honest limit across all three: **none of it has been exercised against a running game.** The
orchestrator halves are unit-tested, the adapter halves are in the deployed DLL and pass the wire
suite under Wine, and the JSON each emitter produces has been posted to a live service and read
back. That is not the same as a turn having been played through them.

| phase | what | status |
| --- | --- | --- |
| 1 | outcome feedback — the engine reports what it did | **built** (`outcomes.py`, `POST /outcome`) |
| 2 | order verbs on door 2 — `move` / `skip` / `build` | **built** (adapter `na_order_command`, `orders.py`, `POST /order`) |
| 3 | turn view announced at `mod_turn_upkeep` | **built** (`turns.py`, `POST /turn`, `GET /turn`) |
| 4 | batching, and tile-visibility gating | **built** (`na_order_batch`, `is_known` gate, `issue_batch`) |

All four phases are end-to-end: an agent can see the whole turn, command any unit or base
directly, batch orders into one round trip, and learn what the engine did with each. Ordering a
unit onto an unexplored tile is refused by the engine's own `is_known`.

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
- **Fog — and this document previously got it wrong.** It said a world view is fog-gated and an
  order bypasses that path. Measured: `fog.py` gates the **foreign-diplomacy feed only** — deltas
  naming a faction we have not contacted — and there is **no tile-visibility model anywhere in the
  orchestrator**. So orders do not slip past a map gate; that gate has never existed, for orders
  or for world views.

  The correction matters because it moves the work. "Route orders through the existing gate" is
  not a task — there is nothing to route through. The real question is whether the *engine* should
  refuse a move to a tile the faction has not explored, which is an adapter-side check against
  engine visibility state, and separately whether the world view leaks tile knowledge in the first
  place. Fair play remains a project invariant; what changed is that this is a gap to build rather
  than a bypass to close, and stating it the old way would have sent someone looking for a gate to
  reuse.
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

**Phase 1 — outcome feedback. BUILT.** The adapter now reports what the engine did, and the
orchestrator keeps it.

- `POST /outcome` — mounted in **every** mode, unlike `/agent/*`. This is the adapter reporting on
  the engine, which has nothing to do with which brain answered; gating it on `AgentBrain` would
  mean the measurement lanes could never see a divergence.
- `GET /outcomes?cursor=` and `GET /outcome/{traceparent}`, plus `POST /agent/outcomes` for an
  attached agent.
- Correlation is by **`traceparent`**, not a decision id: the adapter stamps one on every world
  view for every brain, whereas decision ids exist only when an agent queue is mounted. The
  adapter formats the traceparent once into `na_last_trace` and stashes it per base, so the
  divergence check — which runs long after the decide call returned — can still name the decision
  it diverged from.
- An unreported decision reads **`unknown`**, never `applied`, and `GET /outcome/{id}` returns 200
  rather than 404 for one, because a 404 invites a caller to treat "no answer yet" as "nothing
  went wrong".
- The adapter's POST is bounded at **250 ms** and its result discarded — deliberately *not*
  `llm_timeout_ms`. A decision may block the game for as long as an agent needs to think;
  reporting something that already happened has earned no such licence.

Verified: 491 orchestrator tests pass, the wire format is pinned from the emitters, and the
adapter's 43-check wire suite still passes under Wine against a live orchestrator.

**Phase 2 — order verbs on door 2. BUILT.** `move <veh_id> <x> <y>`, `skip <veh_id>`,
`build <base_id> <item_id>`, wrapping `set_move_to` / `mod_veh_skip` / `mod_base_change`. This is
what delivers requirement 4, and it delivers it *properly* — by giving the agent the affordance a
human has, rather than by bending the decision cycle.

- Adapter side (`na_order_command`) gates on **halted / routed / whose turn**, three distinct
  refusals because "refused" alone cannot tell an agent whether to fix something or simply wait.
  `build` **reads the item back** after `mod_base_change` rather than trusting the setter, and
  refreshes the per-base cache so a replay cannot quietly undo an order the agent was told
  succeeded.
- Orchestrator side (`orders.py`, `POST /order`) serialises orders under a lock — the channel is
  one command file and one result slot, so concurrency here does not go slowly wrong, it goes
  *silently* wrong — and **clears the result slot before writing**, or the previous order's
  success would be returned instantly and attributed to this one.
- **An unobserved result is `unknown`, never `ok`.** The adapter consumes the command *before*
  acting so a crash cannot replay it, which means "the file is gone" is evidence the order was
  read and never that it was carried out. Both the consumed and never-read cases report `unknown`,
  with different detail.
- `GET /order` says whether ordering is available and why not, so an agent need not issue an order
  to discover it cannot.

The orchestrator must share a filesystem with the game, because the channel is a file. Nothing
else in this service requires co-location; a remote orchestrator will order nothing while looking
healthy, which is why `NA_GAME_DIR` is unset by default and reports `unavailable` rather than
pretending.

**Phase 3 — turn view. BUILT.** The adapter announces the coming turn from `mod_turn_upkeep`;
`turns.py` keeps it and folds in every world view and outcome as they arrive, so the view stays
true without anyone maintaining it.

- **`expected` and `raised` are different words on purpose.** The announcement is built at the
  between-turns seam from the board as it stood when the *previous* turn ended. A base can be
  captured, starve, or finish a project, and the decision it was expected to raise never comes. A
  turn where 51 were forecast and 47 arrived is ordinary — and is also what a stuck adapter looks
  like, so `unraised` **names** the missing ones rather than leaving them to be inferred from a
  count.
- A decision that arrives **unforecast is added, not dropped**. The forecast is a guess; a
  decision that shows up unannounced is real, and discarding it would make the view a record of
  what we predicted rather than of what is happening.
- **Status never walks backwards.** A base is asked several times per turn, so a replay arriving
  after the answer must not reset the slot to `raised` — that would make an answered decision look
  outstanding and invite a second answer. Divergence is terminal for the same reason in reverse: a
  later `applied` must not paper over the engine disagreeing.
- The adapter forecasts **`base.production` only**. That surface fires for every base every turn,
  so it can be predicted honestly; `faction.tech` and `faction.se` fire conditionally, and a
  forecast entry that never arrives is indistinguishable from a stuck adapter. A wrong forecast is
  worse than a short one.

**Phase 4 — batching and tile-visibility gating. BUILT.**

- **Batching.** `na_order_batch` runs up to 32 order lines in one tick and answers with ONE
  envelope carrying every outcome. One result file per order would be pointless: the slot is
  overwritten, so all but the last would be destroyed before anyone read them. The envelope's
  `ok` is true only when *every* order succeeded — a batch that half-worked is not a success, and
  flattening it to one boolean is how a partial failure goes unseen, so `results` carries the
  per-order entries and `dropped` reports anything past the cap that was never executed. Only
  order verbs batch; `shot` / `click` / `key` / `load` stay one-per-file, because for an operator
  command a second line is far likelier to be a mistake than an intent.
- **Tile visibility.** `move` now refuses a destination the faction has not explored, using the
  engine's own `is_known(x, y, faction_id)`. Per the correction in §5 this is not a second line
  behind an orchestrator gate — for tiles it is the only gate there is. It does not make an
  agent's *knowledge* fog-clean (that is the world view's problem); it stops the concrete cheat
  available through this door.

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
