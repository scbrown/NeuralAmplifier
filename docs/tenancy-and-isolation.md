# Tenancy & Isolation — Representing Opponents as Principals

> **Status: design / architecture (pre-alpha).** This describes how Neural Amplifier represents
> multiple opponents as isolated principals across [Hank](https://github.com/scbrown/hank) (hot
> state) and [Quipu](https://github.com/scbrown/quipu) (durable memory). The Hank side rests on
> its shipped shared-base + per-tenant-overlay architecture; the per-game/per-faction tenancy *as
> an isolation boundary* is net-new (a new Hank FR). Grounded in
> [knowledge-architecture.md](knowledge-architecture.md) §"Tenancy & isolation"; this doc does not
> contradict it. See also [policy-harness.md](policy-harness.md) for the scoped surfaces.

**Tenancy is a security boundary, not just organization.** A game may host several LLM-driven
factions, and one principal must never read a sibling's hidden intel, private memory, or plans.
Isolation must be **at least as strict as in-game fog-of-war**.

## Principals & modes

- **Autonomous mode** — several LLM factions play in one game, each a separate brain. Each is a
  distinct **principal** with its own private state, memory, and reasoning.
- **Copilot mode** — a human and a brain share *one* faction. They are one principal (one
  tenant); the human is not a sibling to be isolated from.

The isolation contract: a principal's private board intel, its learned memory, and its in-flight
reasoning are readable only by that principal. This is stricter than "don't leak the answer" — it
is "a principal cannot even *observe* what a sibling knows," matching fog-of-war.

## Tenant keys

Different concerns key on different identities:

| Concern | Tenant key | Lifetime |
|---|---|---|
| Hot board state (role d) + per-game memory | `(game_id, faction_id)` | one game |
| Durable cross-game learned memory | a persistent `player_identity` ("an NA-Gaians book") | across all games |
| Datalinks + canonical policies | **global, read-only, shared by all** | permanent |

`player_identity` is independent of any one game — the durable book of a recurring brain — while
`(game_id, faction_id)` scopes everything ephemeral. Datalinks and canonical `aegis:Policy` rows
are common knowledge; sharing them read-only leaks nothing.

## Hank's fog-isolation model

This *is* Hank's shipped shared-base + per-tenant-COW-overlay architecture, repurposed. The map
onto fog-of-war is exact:

- **Shared BASE graph = public / common-knowledge facts** — map size, public treaties, tech known
  to ≥3 factions, observed sightings. Anything every player legitimately knows.
- **Per-faction COW OVERLAY = that faction's private intel** — own units and bases, unexplored
  fog, in-flight plans. One overlay per `(game_id, faction_id)`.
- **A tenant reads base + its own overlay, never a sibling's.** The same isolation that keeps two
  developers' concurrent edits from corrupting each other's view keeps two faction brains from
  seeing each other's intel. Fog isolation comes *for free* from the overlay model.

The net-new game-state surfaces — `hank_ingest`, `hank_guard`, `hank_whatif` (see
[policy-harness.md](policy-harness.md)) — all operate **strictly within `(game_id, faction_id)`**.
An ingest writes only that faction's overlay; a guard or what-if reads only base + that faction's
overlay.

## Quipu — and the honest limit

Durable memory lives in Quipu, and here the isolation story needs care:

- **Datalinks = shared read-only.** One canonical store, mounted read-only for every principal.
- **Memory is scoped per principal — BUT `group_id` is best-effort provenance, NOT an isolation
  boundary.** Quipu's `group_id` organizes facts; it does **not** stop a crafted SPARQL query from
  reading another `group_id`'s memory. Presenting it as a security boundary would be wrong.
- **So each `player_identity` gets its OWN Quipu database** for durable memory. This is true
  isolation — cheap given Quipu's "SQLite energy" (a database per recurring brain is a file, not a
  cluster). A shared **read-only datalinks db** is mounted alongside each.
- `group_id` organizes *within* a principal's own database (per-game vs. durable, per the
  bitemporal split), **never across** principals. **Never present `group_id` as a security
  boundary.**

So isolation across adversarial brains rests on: **separate Quipu dbs per `player_identity` +
Hank's per-faction overlays + orchestrator scoping** — never on `group_id`.

## Opponent modeling

Modeling an opponent must not breach the opponent's isolation:

- **"My dossier on the Hive" is a `mem:OpponentPattern` in *my* store** — my observations of the
  Hive's behavior, written to my own Quipu database. It is never a read of the Hive-brain's own
  memory.
- **If the opponent is also a Neural Amplifier brain, it is a separate principal with a separate
  store.** I model it from the outside, from what I can observe.
- **The game engine is the only legitimate channel between two brains** — and it speaks only each
  one's fog-limited `world_view`. There is no back channel through shared memory, because there is
  no shared memory: my dossier and its self-knowledge live in different databases.

## Fog honesty

The isolation boundary is correct in the design regardless of engine; whether the *input* is fair
depends on the engine:

- **Thinker has real, per-faction fog** (`MAP::visibility` → `is_visible`/`is_known`; see
  [thinker-adapter-notes.md](thinker-adapter-notes.md) §3). The `world_view` handed to a brain is
  already fog-limited, so the tenancy boundary and the input agree — **fair from day one**.
- **GLSMAC today has `fog: false`** (full ground truth; see [contract.md](contract.md)). The
  knowledge-layer isolation boundary is still *correct* — each brain still reads only its own
  overlay — **but the upstream `world_view` input is unfair** until GLSMAC has fog, because the
  engine hands over ground truth before Hank ever partitions it. The orchestrator may note this in
  the log as "unfair mode" and still play; the isolation machinery is not the leak, the engine
  input is.

## See also

- [knowledge-architecture.md](knowledge-architecture.md) — the umbrella design and the full
  tenancy section this doc expands.
- [policy-harness.md](policy-harness.md) — the `(game_id, faction_id)`-scoped surfaces
  (`hank_ingest` / `hank_guard` / `hank_whatif`) and the hot state graph they isolate.
- [hank-integration.md](hank-integration.md) — the code-facing roles (grounding, dev guardrails).
