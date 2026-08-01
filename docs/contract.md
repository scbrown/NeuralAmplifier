# The Contract

The platform-agnostic interface between the **orchestrator** (the LLM brain) and an
**adapter** (engine-side code for Thinker or GLSMAC). The orchestrator speaks only this
contract and never knows which game it's driving. Both adapters implement it.

Transport is **HTTP + JSON**: the adapter sends a *world view* and receives *orders*. Keeping
it JSON-over-HTTP is what makes every decision inspectable, loggable, and replayable.

## Flow

```text
adapter  ── POST /decide  { world_view } ──▶  orchestrator
adapter  ◀── 200          { orders }      ──   orchestrator
```

One request per decision point (a full turn, or a single unit/base when the LLM drills down).
The adapter applies each returned order through the engine's own validation, so anything
illegal is rejected engine-side — the contract never has to be the last line of defense.

## World view (adapter → orchestrator)

Versioned; starts from what an engine exposes today and grows. Fields absent on an engine are
omitted (the orchestrator treats missing sections as "not available on this engine").

```json
{
  "schema_version": "0.1",
  "engine": "thinker",              // or "glsmac"
  "scope": "turn",                  // "turn" | "unit" | "base"  (drill-down granularity)
  "surface_id": "base.production",  // optional: stable decision id (see game-surface.md)
  "trace": { "traceparent": "00-4bf92f35…-00f067aa…-01" },  // optional: W3C trace context
  "turn": 42,
  "year": 2142,
  "faction": "GAIANS",
  "scores": { "GAIANS": 310, "HIVE": 288 },
  "fairness": {                     // declared rule asymmetries — see game-surface.md §5
    "slot": "ai",                   // "ai" | "human"  (is_human for this faction)
    "difficulty": "transcend",
    "handicaps": [
      { "id": "retool_penalty",   "favours": "self", "selected_by": "structural",
        "detail": "AI pays no retool penalty at any difficulty" },
      { "id": "tech_cost_factor", "favours": "self", "selected_by": "difficulty",
        "detail": "tech cost x0.8 at transcend" }
    ]
  },
  "economy": {                      // omitted on engines without it (e.g. GLSMAC today)
    "energy_credits": 74,
    "research": { "current": "Ecological Engineering", "progress": 0.6 },
    "social_engineering": { "economics": "Planned", "values": "Green" },
    "techs_known": ["Centauri Ecology", "Biogenetics"]
  },
  "map": {
    "width": 64, "height": 32,
    "fog": true,                    // false when the engine can't fog (GLSMAC today)
    "visible_tiles": [
      { "x": 12, "y": 8, "terrain": "rolling", "altitude": "land",
        "moisture": 1, "rockiness": 0,
        "resources": { "nutrients": 2, "minerals": 1, "energy": 0 },
        "features": ["river"], "improvements": ["road"], "owner": "GAIANS" }
    ]
  },
  "units": [
    { "id": 101, "type": "former", "x": 12, "y": 8, "hp": 10, "morale": "disciplined",
      "moves_left": 1, "orders": "idle" }
  ],
  "bases": [
    { "name": "Gaia's Landing", "x": 11, "y": 9, "pop": 4,
      "yields": { "nutrients": 6, "minerals": 3, "energy": 5 },
      "producing": "Recycling Tanks",   // omitted where production doesn't exist yet
      "garrison": [102] }
  ],
  "deltas": [ { "type": "tech_discovered", "tech": "Centauri Ecology" } ],
  "action_space": [
    { "id": "a1", "action": "move_unit",      "unit": 101, "to": [13, 8] },
    { "id": "a2", "action": "found_base",     "unit": 103, "at": [20, 14] },
    { "id": "a3", "action": "set_production", "base": "Gaia's Landing", "item": "Former" },
    { "id": "a4", "action": "end_turn" }
  ],
  "contacts": ["HIVE", "UNIVERSITY"],  // factions met — gates the diplomacy feed
  "memory": "Builder game; watching the Hive to my east."
}
```

- **`action_space` is the guardrail.** It's the *complete* set of legal moves for this scope,
  supplied by the engine. The orchestrator returns choices **from this set** — it never
  invents actions. Each entry has an `id` the orders reference.
- **Fog.** `map.fog` tells the orchestrator whether visibility is real. When `false`, the
  world view is full ground truth (GLSMAC today) — the orchestrator may note this in the log
  as "unfair mode" but still plays.

## Orders (orchestrator → adapter)

```json
{
  "schema_version": "0.1",
  "choices": [
    { "action_id": "a1", "reason": "Terraform the ridge for minerals." },
    { "action_id": "a3", "reason": "Recycling Tanks — economy first." },
    { "action_id": "a4", "reason": "Nothing else worth doing this turn." }
  ],
  "notes": "Still builder-focused; begin scouting east next turn.",
  "degraded": false                 // optional: true when this is the safe fallback, not a decision
}
```

- Orders reference `action_id`s from the world view's `action_space` — the adapter looks each
  up and applies it. Unknown/duplicate ids are ignored (belt-and-suspenders; the engine
  validates too).
- `notes` is a **volatile scratchpad, not the store of record.** The orchestrator distills it
  into the Quipu knowledge layer each turn; next turn's `memory` is *synthesized by retrieval*
  from that governed bitemporal store rather than carried verbatim. The wire field is unchanged —
  only its semantics move (see [knowledge-architecture.md](knowledge-architecture.md) and
  [learned-memory.md](learned-memory.md)).
- **Degradation:** if the orchestrator times out / errors / exceeds budget, it returns the
  safe fallback (`end_turn` where present, else the deterministic default) rather than
  failing — the game never stalls waiting on the brain. It sets `degraded: true` when it does,
  so a run of pure fallbacks is distinguishable from a run of real decisions — see
  [observability.md](observability.md) §5.4.

## Standing plan (orchestrator-injected)

Four fields carry directives across the contract. Full design in
[directives.md](directives.md).

**On the world view**, all orchestrator-injected — an adapter never sets them:

| Field | Meaning |
| --- | --- |
| `directives` | `DirectiveStatus[]` — the standing plan relevant to *this* decision, each with its current measured value, whether it is satisfied, and the `via`/`hop` path that reached it |
| `tradeoffs` | `Tradeoff[]` — what each action would cost each directive: the metric delta, the projected value, whether it `would_violate`, and `setback_turns` where a rate is known |

**Adapter-supplied**, and the reason any of the above can exist:

| Field | Meaning |
| --- | --- |
| `subjects` | `string[]` — the datalinks entities this decision is **about**, as distinct from the ones it chooses *between*. Required on any surface whose action labels are not themselves entities |
| `metrics` | `{name: number}` — engine-neutral named measurements, using the vocabulary in `metrics.py`. This is the one place the orchestrator reads numbers by name, and it is safe precisely because the adapter did the engine-specific work of naming them (invariant 2) |
| `Action.effects` | `{metric: delta}` — an action's immediate, known effect on named metrics. Without it a trade-off cannot be computed and a priority number has nothing to be weighed against |

**`subjects` is what makes a surface groundable when its options are not entities.** Retrieval
keys off action labels, which is right for a surface that picks among named things —
`base.production` offers "Colony Pod" and the graph has a node called "Colony Pod". `base.hurry`
offers "Hurry production" and "Do not hurry"; neither is in any datalinks, so without `subjects`
the surface retrieves **nothing** and the brain decides it on state alone. Measured at 0.60
stability, the least stable surface there is.

Naming a subject cannot bias the choice the way grounding one *option* would, and that is the
whole reason it is a separate field: every option on such a surface concerns the same entity, so
explaining it explains all of them equally.

**`metrics` is the only key the orchestrator reads measurements from**, and a measurement under
any other key is invisible. Emitting the right numbers under the wrong name is not a partial
success: every directive then evaluates `unmeasurable` — never checked, as distinct from checked
and failing — while still appearing in the record's `in_force`, so a plan that steers nothing
reads as a plan being served. The Thinker adapter shipped exactly that for a while, under a
`faction_state` key.

Each number belongs in exactly one place. `metrics` carries every name the vocabulary has; the
engine's own blocks (`base_state` and friends) carry what it has no name for. Repeating one
measurement in both is a correctness problem rather than a token cost — a hook that observes
*after* the engine has acted holds a snapshot that its live fields no longer agree with, and a
record carrying both cannot say which the decision was made on.

**On orders**, from the model:

| Field | Meaning |
| --- | --- |
| `directives` | Directives this decision places on future ones. Empty for almost every decision |
| `followed` | Ids of standing directives that changed this choice — the attention signal, filtered against what was actually offered, exactly as `cited` is |
| `overrode` | Ids knowingly worked against. Recorded, not prevented: a plan that can never be broken is a plan that loses games |

## Telemetry fields

Three optional, additive fields carry observability without changing the shape of the exchange
(full design in [observability.md](observability.md)):

| Field | Direction | Purpose |
| --- | --- | --- |
| `surface_id` | adapter → orchestrator | Which decision this is, from [game-surface.md](game-surface.md). Drives coverage measurement. |
| `trace.traceparent` | adapter → orchestrator | W3C trace context. The **adapter is the root** — the game is the root of the causality — and the orchestrator continues it across Quipu/Hank. |
| `degraded` | orchestrator → adapter | This response is the safe fallback, not a decision. |

## Fairness

`fairness` declares the rule asymmetries in force for this faction. SMAC gives non-human
factions a systematic bonus layer; the project's decision is to **record** it rather than patch
it out ([game-surface.md](game-surface.md) §5). An empty `handicaps` list means the faction plays
by unmodified rules — the expected case on a human slot.

`selected_by` distinguishes **`difficulty`** (scales with `*DiffLevel` — a deliberate user
choice, and what difficulty means in SMAC) from **`structural`** (flat `is_human` branches
present at every difficulty, which no one selected). Only the structural set needs defending in
a result. The orchestrator does not act on this field as a rule engine; it passes it into the prompt (so
Claude reasons honestly about its own advantages) and onto the decision record (so a result is
interpretable after the fact). `slot: "human"` with an empty list is the only configuration that
supports an unqualified fair-play claim.

An adapter that omits them still works; it just cannot be measured. The orchestrator owns
telemetry export — the adapter only stamps these fields, because it must never block the game's
message pump.

The block is **derivable**: `neural_amplifier.fairness_profile(slot, difficulty, config)` computes
what the rules actually produce, and `handicap_drift()` compares a stamped block against it. Use
it — an adapter that quietly stops stamping otherwise looks exactly like a clean run, which is the
fairness twin of the all-fallback failure in [observability.md](observability.md) §5.4. Whether an
entry is in force depends on difficulty and `thinker.ini`, not on the entry existing, so `favours`
is computed rather than copied from a table ([game-surface.md](game-surface.md) §5.1).

## Two tiers

The adapter runs the **deterministic tier** locally (former automation, pathfinding, base
governors, default production). It only calls `/decide` for scopes the LLM should own —
policy each turn, plus any unit/base the LLM elects to **drill down** on. The orchestrator can
signal drill-down by returning a `focus` list in `notes`/orders (to be specified as the tiers
land). This keeps LLM calls to a handful per turn.

## Knowledge layer & guardrails

The `action_space` is the **legality guardrail** and stays the last word on what's *possible*.
Layered on top, without changing the wire format:

- **Knowledge (Quipu).** Before building the prompt, the orchestrator retrieves engine-filtered
  datalinks (rules), learned tactics, and opponent patterns from a governed bitemporal knowledge
  graph and *annotates* the existing options — it never adds or removes actions. The static
  briefing is sourced from this KB; per-turn fetches are bounded to the items in this turn's
  `action_space`.
- **Policy guard (Hank).** After the LLM proposes orders (already drawn from `action_space`), the
  orchestrator evaluates them against governed strategic/house-rule policies over a hot in-memory
  copy of the board: `deny` violations are stripped and returned for bounded repair, `warn`
  violations become advisories. This is a *strategic* guardrail that **complements, never
  replaces** the engine's legality gate — it can only subtract or annotate *legal* orders.
- **Precedence** (highest wins): engine legality (`action_space`) > Hank deny-policies >
  canonical datalinks > engine-observed (Hank-promoted) > house-rule > `strat:` doctrine >
  learned tactic.

Both layers are optional and degrade safely: if Quipu/Hank are unreachable, the orchestrator
plays from the cached static briefing and the engine's `action_space` alone. Full design in
[knowledge-architecture.md](knowledge-architecture.md).

## Per-engine mapping (summary)

| Contract concept | Thinker / `terranx` | GLSMAC |
| --- | --- | --- |
| World view source | game's in-memory structures at an AI hook | `um`/`bm`/`tm`/`fm` reads in a `.gls.js` mod |
| `action_space` | legal choices at the intercepted decision | a library of registered GSE events with `validate` |
| Applying orders | return/write the choice at the hook | fire the chosen GSE event (`apply`/`rollback`) |
| Transport | DLL → HTTP (libcurl or local socket) | GSE `http` builtin → HTTP |
| Fog | real | absent today (`fog: false`) |
| Economy/tech | present | omitted until built |

See [thinker-adapter-notes.md](thinker-adapter-notes.md) and
[glsmac-integration-notes.md](glsmac-integration-notes.md) for the grounded specifics.
