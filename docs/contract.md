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
  "turn": 42,
  "year": 2142,
  "faction": "GAIANS",
  "scores": { "GAIANS": 310, "HIVE": 288 },
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
  "notes": "Still builder-focused; begin scouting east next turn."
}
```

- Orders reference `action_id`s from the world view's `action_space` — the adapter looks each
  up and applies it. Unknown/duplicate ids are ignored (belt-and-suspenders; the engine
  validates too).
- `notes` becomes next turn's `memory`.
- **Degradation:** if the orchestrator times out / errors / exceeds budget, it returns the
  safe fallback (`end_turn` where present, else the deterministic default) rather than
  failing — the game never stalls waiting on the brain.

## Two tiers

The adapter runs the **deterministic tier** locally (former automation, pathfinding, base
governors, default production). It only calls `/decide` for scopes the LLM should own —
policy each turn, plus any unit/base the LLM elects to **drill down** on. The orchestrator can
signal drill-down by returning a `focus` list in `notes`/orders (to be specified as the tiers
land). This keeps LLM calls to a handful per turn.

## Per-engine mapping (summary)

| Contract concept | Thinker / `terranx` | GLSMAC |
|---|---|---|
| World view source | game's in-memory structures at an AI hook | `um`/`bm`/`tm`/`fm` reads in a `.gls.js` mod |
| `action_space` | legal choices at the intercepted decision | a library of registered GSE events with `validate` |
| Applying orders | return/write the choice at the hook | fire the chosen GSE event (`apply`/`rollback`) |
| Transport | DLL → HTTP (libcurl or local socket) | GSE `http` builtin → HTTP |
| Fog | real | absent today (`fog: false`) |
| Economy/tech | present | omitted until built |

See [thinker-adapter-notes.md](thinker-adapter-notes.md) and
[glsmac-integration-notes.md](glsmac-integration-notes.md) for the grounded specifics.
