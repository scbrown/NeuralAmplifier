# Learned Memory

> **Status: design / architecture (pre-alpha).** No orchestrator code exists yet.
> This describes the **memory plane** of the knowledge layer. See
> [knowledge-architecture.md](knowledge-architecture.md) for the umbrella and
> [quipu-integration.md](quipu-integration.md) for the wire calls.

The memory plane is where the brain accumulates strategy, tactics, and opponent
models across games. It is one of the two planes in the single Quipu graph (the other
being the static datalinks plane). Everything here is **fast-changing, episode-heavy,
and valid-time-versioned** — the opposite of the slow canonical rules.

## Written via episodes, never free Turtle

Learned knowledge is written **only** via `quipu_episode` — structured nodes/edges with
provenance, auto-embed, and entity-resolution — never as free Turtle. The reason is
governance: every fact ingested through an episode gets `prov:wasGeneratedBy` for free,
so we can always answer *which extraction wrote this belief and when*. A free-Turtle
write would skip that chain. (Overrides and confidence adjustments use `quipu_set` for
atomic supersede; retractions use `quipu_retract` / `quipu_retract_episode`.)

## The `mem:` vocabulary

The `mem:` sub-namespace holds the learned-memory classes. These mirror the model in
[knowledge-architecture.md](knowledge-architecture.md):

- **`mem:Game`** — one match: engine, faction, difficulty, seed, opponents, outcome.
- **`mem:GameState`** — a periodic turn snapshot for time-anchored recall.
- **`mem:Decision`** — a `/decide` call: chosen `action_id`s + reasons + turn.
- **`mem:Tactic`** — a learned pattern with `trigger`, `action`, and `confidence`
  (e.g. "rush Recycling Tanks before size-3 on high-rocky starts").
- **`mem:OpponentPattern`** — observed opponent behavior. Edges:
  `mem:aboutFaction → smac:Faction` (which faction this is a model *of*) and
  `mem:counteredBy → mem:Tactic` (what beat it).
- **`mem:Outcome`** — the result of applying a tactic. Edges `mem:confirmedBy` and
  `mem:refutedBy` adjust the tactic's `confidence` up or down.

The `mem:aboutFaction` edge deliberately joins the memory plane to the datalinks plane:
an opponent model points at the canonical `smac:Faction` it concerns, so "my dossier on
the Hive" resolves to the same faction entity the rules describe.

## Example episode: an opponent pattern

A concrete `quipu_episode` write. The brain observed the Hive opening with an early
Impact-Rover rush and learned that a perimeter-then-probe response countered it. Note
the two nodes (`mem:OpponentPattern`, `mem:Tactic`) and the edges wiring the pattern to
the canonical Hive faction and to the counter-tactic:

```json
{
  "tool": "quipu_episode",
  "input": {
    "name": "hive-impact-rover-rush-countered",
    "source": "orchestrator/postgame-extractor",
    "group_id": "memory:durable",
    "nodes": [
      {
        "name": "hive-early-impact-rover-rush",
        "type": "mem:OpponentPattern",
        "properties": {
          "observedBehavior": "Hive builds Impact Rovers by ~turn 25 and rushes the nearest border base",
          "confidence": 0.72,
          "gamesObserved": 3
        }
      },
      {
        "name": "perimeter-then-probe",
        "type": "mem:Tactic",
        "properties": {
          "trigger": "border base within 8 tiles of Hive, no perimeter defense, pre-turn-25",
          "action": "build Perimeter Defense then a Probe Team before offensive units",
          "confidence": 0.68
        }
      }
    ],
    "edges": [
      { "source": "hive-early-impact-rover-rush", "target": "HIVE",
        "relation": "mem:aboutFaction" },
      { "source": "hive-early-impact-rover-rush", "target": "perimeter-then-probe",
        "relation": "mem:counteredBy" }
    ]
  }
}
```

Entity resolution links the `HIVE` target to the existing `smac:Faction` node; the
auto-embed makes both nodes reachable from `quipu_hybrid_search` when a similar
situation recurs next game.

## Bitemporal usage

Quipu records **two** time axes on every fact: transaction-time (*when we learned it*)
and valid-time (*which game-world it was true in*). The memory plane uses them via the
`group_id` split:

- **`memory:game:<id>` — valid-time is the in-game turn.** Per-game facts are stamped
  with the turn they were true at, so we can recall *"what did I know at turn 30 of
  **this** game?"*. This group is **archived at game end** (its live belief promoted to
  durable memory via postgame extraction).
- **`memory:durable` — valid-time is wall-clock.** Cross-game strategy, with
  `confidence` rising and falling across games as `mem:Outcome` edges confirm or refute
  each tactic. Recall here is *"N games ago"* — a wall-clock time-travel.

A time-travel query with `valid_at` recovers the belief as it stood at a chosen moment.
For the per-game axis, `valid_at` is the in-game turn timestamp; for durable memory it
is a wall-clock instant:

```json
{
  "tool": "quipu_query",
  "input": {
    "query": "SELECT ?tactic ?conf WHERE { ?t a mem:Tactic ; mem:trigger ?trig ; mem:confidence ?conf . BIND(?t AS ?tactic) }",
    "valid_at": "2026-05-01T00:00:00Z"
  }
}
```

Because transaction-time is independent, we can also ask "what did we *believe* about
turn 30 as of the extraction run", separating a late correction from what was known
live — the audit property the episode provenance chain guarantees.

## Reconciling the contract's `memory` field

The [contract](contract.md) carries a single `memory` string in the `world_view` and a
`notes` string in the returned orders (`notes` becomes next turn's `memory`). The memory
plane reconciles with that wire format **without breaking it**:

- **`notes` becomes a volatile extraction buffer.** It is no longer the long-term store
  — it is a scratchpad distilled into `mem:` episodes each turn by the postgame/per-turn
  extractor. Its content is transient.
- **The injected `memory` is synthesized by Quipu retrieval**, not carried verbatim
  turn-to-turn. Next turn's `memory` is assembled from `quipu_context` +
  `quipu_hybrid_search` over the `mem:` graph (see
  [quipu-integration.md](quipu-integration.md)), not copied from last turn's `notes`.
- **The wire format stays backward-compatible.** Both fields remain plain strings; an
  adapter that knows nothing of Quipu still round-trips them. The knowledge layer only
  changes *how the strings are produced*, never their shape.

## Isolation

Durable memory is **per `player_identity`** — "my dossier on the Hive" lives in **my**
store as a `mem:OpponentPattern`, never in the Hive-brain's own memory. If the opponent
is itself a Neural Amplifier brain, it is a **separate principal with a separate Quipu
database**, and the game engine is the only legitimate channel between them (via each
one's fog-limited `world_view`).

**Quipu `group_id` is not a security boundary.** The `memory:durable` /
`memory:game:<id>` split organizes facts *within* one principal's store; it cannot stop
a crafted SPARQL query from reading across groups. Cross-principal isolation rests on
the separate-database-per-identity choice, not on `group_id`. See
[tenancy-and-isolation.md](tenancy-and-isolation.md) for the full principal /
tenant-key model.

## What's planned

- The whole memory plane is **rollout phase K3** in
  [knowledge-architecture.md](knowledge-architecture.md) (postgame extraction → `mem:`
  episodes; bitemporal per-game/durable; tactics into the prompt). Nothing here is built
  yet. *Exit criterion: a tactic learned in game N surfaces in game N+1.*
- Confidence adjustment via `mem:Outcome` (`confirmedBy`/`refutedBy`) is a designed
  loop, not an implemented one — the update policy (how much a single game moves
  `confidence`) is still to be specified.
