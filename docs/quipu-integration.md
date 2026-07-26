# Quipu Integration

> **Status: design / architecture (pre-alpha).** No orchestrator code exists yet.
> This describes how the Neural Amplifier orchestrator *will* talk to Quipu. It is
> written honestly about what is designed here versus what is net-new or blocked
> upstream. See [knowledge-architecture.md](knowledge-architecture.md) for the
> umbrella design this refines.

Quipu is the **persisted, governed store** behind the knowledge layer: the static
datalinks plane (canonical SMAC rules) and the learned-memory plane
(bitemporal per-game and cross-game strategy) both live in
[Quipu](https://github.com/scbrown/quipu), a governed bitemporal knowledge graph.
Hank holds the *hot, ephemeral* board; Quipu holds what must survive the turn — see
the hot-vs-persisted split in [knowledge-architecture.md](knowledge-architecture.md).
This doc covers only the orchestrator↔Quipu wire: interfaces, the retrieval calls at
`/decide`, budgeting, `group_id` conventions, and degradation.

## Interfaces: which to use

Quipu exposes four interfaces (Rust crate, `quipu` CLI, `quipu-server` REST, and 27
MCP tools). The orchestrator uses two of them:

- **MCP tools — the agent loop.** The per-turn retrieval calls (`quipu_context`,
  `quipu_query`, `quipu_hybrid_search`, `quipu_impact`) and the memory writes
  (`quipu_episode`, `quipu_set`, `quipu_retract`) are MCP tools. This is the primary
  path: the orchestrator is already an MCP client, and the agent-friendly feedback
  (structured validation errors, ranked context) is shaped for exactly this loop.
- **`quipu-server` REST — out-of-process.** When Quipu runs as a separate process
  (the expected deployment: one store per `player_identity`, mounted read-only
  datalinks alongside), the same operations are reachable over HTTP
  (`POST /query`, `/context`, `/impact`, `/episode`, …). Use REST when the store is
  not embedded in the orchestrator process.

### Build and backend requirements

- **`quipu-server` needs `--features shacl,onnx`.** A plain `cargo build --release`
  builds only the CLI and *skips* the server — `onnx` supplies the embedding runtime
  and `shacl` supplies the write-time guardrail that enforces the anti-masquerade
  tier predicates. Both are mandatory for our use: without `shacl` the tier tags are
  unenforced; without `onnx` there is no auto-embed, so `quipu_context` and
  `quipu_hybrid_search` degrade to SPARQL `CONTAINS`.
- **Vector search is SQLite brute-force by default.** The shipped CLI/server use the
  default SQLite backend (brute-force cosine similarity). The faster LanceDB ANN
  backend is a library primitive only — it is **not** selectable from config; an
  embedder installs it via `Store::set_local_vector_backend`, and `vector.backend`
  is not read by the binaries. Budget latency for brute-force similarity until an
  embedder wires LanceDB (planned).

## The retrieval calls at `/decide`

These mirror step 3 of the retrieval flow in
[knowledge-architecture.md](knowledge-architecture.md) exactly. Two classes of call:
a **cached-once static briefing** paid at game start, and **per-turn fetches** bounded
to this turn's scope.

### Cached-once static briefing (paid at game start)

Assembled once and **prompt-cached** for the whole game:

- **Engine-filtered datalinks digest.** A SPARQL pull of the canonical rule facts for
  this engine, filtered `smac:appliesToEngine ∈ {smac, <current engine>}` so a
  GLSMAC-only or Thinker house-rule deviation can never surface as canonical SMAC.
- **Opponent dossiers.** Each opponent's `mem:OpponentPattern` set (edges
  `mem:aboutFaction`, `mem:counteredBy`) drawn from durable memory — "what I know
  about the Hive" from prior games (see [learned-memory.md](learned-memory.md)).

Because the engine and opponent set are fixed at game start, this whole block is
static: assemble once, mark it prompt-cached, and pay its tokens a single time.

### Per-turn fetch (bounded to this turn's scope)

Run each turn, kept small:

- **`quipu_context` on a situation string.** A natural-language digest of the current
  turn (`world_view` summary) → ranked facts. One call; NL→ranked entities with
  relevance scores.
- **Action-space grounding — one batched SPARQL query.** A **single** SPARQL `VALUES`
  query enumerating *only* the items in this turn's `action_space`, so the model gets
  the canonical rule fact behind each legal move (cost, prerequisites, effect) without
  a per-item round-trip. Scope is exactly the `action_space` — nothing wider.
- **`quipu_hybrid_search` tactics, k≈3, confidence-gated.** SPARQL filters candidate
  `mem:Tactic` facts by trigger relevance, vector similarity ranks them; take the top
  ~3, and only above a confidence gate so a weakly-held tactic never crowds out a rule.
- **`quipu_impact remove=true` — drill-down only.** The counterfactual BFS
  ("if I skip Ecology, what do I lose?", `smac:requiresTech+`) is expensive; run it
  **only** when the model drills into a specific `unit`/`base` scope, never on the
  default per-turn pass.

```json
{
  "tool": "quipu_query",
  "input": {
    "query": "SELECT ?item ?cost ?req WHERE { VALUES ?item { smac:Former smac:RecyclingTanks } ?item smac:cost ?cost . OPTIONAL { ?item smac:requiresTech ?req } . ?item smac:appliesToEngine ?e . FILTER(?e IN ('smac','thinker')) }"
  }
}
```

## Token budget discipline

The live risk is latency and token cost (see the honesty section below). The rules:

- **Static briefing: cached and paid once.** Never re-fetch or re-inject it per turn.
- **Per-turn calls: bounded to action-space scope.** The grounding query enumerates
  only this turn's `action_space`; `quipu_context` gets a bounded situation string;
  tactics are capped at k≈3.
- **Under budget, drop tactics before rules.** Rules are correctness (a canonical cost
  or prerequisite the model must not get wrong); tactics are optimization. Shed the
  optimization first. This ordering also matches the retrieval **precedence** the
  prompt enforces — canonical datalinks outrank learned tactics
  ([knowledge-architecture.md](knowledge-architecture.md), *Precedence*).

## `group_id` conventions

Quipu `group_id` **organizes facts within a store**; it is **not** a security
boundary (see below and [tenancy-and-isolation.md](tenancy-and-isolation.md)).

| `group_id` | Plane | Contents |
|---|---|---|
| `datalinks:smac` | Datalinks | Canonical stock `alphax.txt` rules |
| `datalinks:thinker` | Datalinks | Thinker house-rule overrides |
| `datalinks:glsmac` | Datalinks | GLSMAC deviations (all `aspirational`) |
| `memory:game:<id>` | Memory | Per-game learned facts, valid-time = in-game turn |
| `memory:durable` | Memory | Cross-game learned strategy, wall-clock valid-time |

Per-engine datalinks groups allow clean bulk re-sync when an engine's rules change.
The memory groups carry the bitemporal split described in
[learned-memory.md](learned-memory.md).

### Isolation is per-database, not per-group

Each persistent `player_identity` gets its **own Quipu database** for durable memory
(true isolation — cheap given Quipu's "SQLite energy"), with a shared **read-only
datalinks db** mounted alongside. `group_id` organizes *within* a principal, never
across principals — a crafted SPARQL query can read across `group_id`s, so it must
never be presented as the isolation boundary. See
[tenancy-and-isolation.md](tenancy-and-isolation.md) for the full model.

## Degradation

If Quipu is unreachable, slow past budget, or errors, the orchestrator **falls back to
the cached static briefing only** and proceeds. The turn is never blocked on a Quipu
round-trip:

- Static briefing is already in the prompt cache → available with zero further calls.
- Skip the per-turn fetches (`quipu_context`, action-space grounding, tactics).
- The engine's `action_space` remains the hard legality gate regardless, so a
  briefing-only turn is still legal — just less informed.

This composes with the contract's own degradation ([contract.md](contract.md)): if the
orchestrator as a whole times out or exceeds budget, it returns the safe fallback
(`end_turn` where present). Knowledge-layer degradation is the softer inner ring — lose
annotation, keep playing.

## What's blocked or aspirational

- **`quipu-server` `onnx` feature is mandatory but off by default** — deployment must
  build `--features shacl,onnx`, or the server does not exist and semantic retrieval
  silently degrades to SPARQL `CONTAINS`.
- **Vector search is brute-force** until an embedder installs the LanceDB backend via
  `Store::set_local_vector_backend`; `vector.backend` config is inert in the binaries
  today. Latency must be **measured, not assumed**.
- **Latency and token cost are the live risk.** Each `/decide` adds Quipu round-trips
  on top of the Hank ingest/guard round-trips; mitigate with hard static-briefing
  caching, action-space-bounded fetches, and the drop-tactics-first rule above. This
  is the number to watch as the integration lands (rollout phases K1–K3 in
  [knowledge-architecture.md](knowledge-architecture.md)).
