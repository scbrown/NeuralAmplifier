# Gameplay Policy Harness — Hot State, Guard & What-If

> **Landed ahead of Hank:** a local `StateGuard` (`orchestrator/hank.py`) now runs the part of
> role (c) that needs no board graph — it checks a chosen order's *declared* effects against the
> metrics the world view reports, denying what current state cannot pay for and warning on a
> violated directive. It sits behind the same `Guard` protocol, composed by `GuardChain`, so the
> verdict shape and the decision record do not change when Hank's `POST /guard` replaces it. See
> [agent-play.md](agent-play.md) §7 for why the agent-brain pivot made it urgent.
>
> **Status: design / architecture (pre-alpha), and net-new engineering.** These three roles
> generalize Hank from a code-structure engine into a general in-memory fact graph + policy /
> what-if harness over the live board. **Nothing here is built**, and it is gated twice: by Hank
> **Phase 4** and by a **non-code ingestion capability that does not exist today** (net-new Hank
> FRs). Role (c) depends on role (d). This doc does not contradict
> [knowledge-architecture.md](knowledge-architecture.md); for the code-facing roles (a) grounding
> and (b) dev guardrails, see [hank-integration.md](hank-integration.md).

Three roles, built bottom-up: **(d)** Hank holds the live board in memory; **(c)** it evaluates
Quipu-governed gameplay policies against that board plus the LLM's proposed orders; **(e)** it
speculatively applies orders to a throwaway overlay and reports the consequences. All three sit
inside a single `/decide` turn and complement — never replace — the engine's `action_space`.

## Role (d): Hot in-memory game-state graph (foundation, net-new)

Everything else rests on Hank holding the board. Hank's shipped strength is a shared base graph
plus per-tenant **copy-on-write (COW) overlays** kept correct while many editors touch it at once.
Role (d) reuses that machinery for a game board instead of a codebase:

- Hank holds the per-faction / per-game board graph **in memory**, rebuilt or patched each turn
  from the `world_view` + `deltas` that arrive at `/decide` (see [contract.md](contract.md)).
- One COW overlay per `(game_id, faction_id)` over a shared base graph — the same architecture
  that gives Hank concurrent-edit correctness gives fog isolation for free (see
  [tenancy-and-isolation.md](tenancy-and-isolation.md)).

What is **net-new** and does not exist in Hank today:

- **Generic non-code fact ingestion.** A `hank_ingest` MCP tool + `POST /ingest` mirroring the
  `quipu_episode` JSON shape (structured nodes/edges), *or* a `world_view` → graph adapter that
  translates the contract's `units`/`bases`/`visible_tiles` into graph nodes.
- **A `Node`/`Edge` type not tied to source spans.** Every Hank node today anchors to a
  `file:line` code span; a game node (`smac:BaseState`, `smac:UnitState`, `smac:TileState`)
  anchors to a board coordinate / entity id instead.
- **A new tier `"engine-state"`** — distinct from the `treesitter` | `lsp` | `cpg` code tiers —
  so a board fact is never confused with a code fact.
- **A `game-state` Cargo feature** gating all of the above (per repo convention: a feature that
  ships must land in the CI matrix in the same change).

### What lives where (copied from [knowledge-architecture.md](knowledge-architecture.md))

| Concern | Hank (hot / ephemeral) | Quipu (persisted / durable) |
|---|---|---|
| Live board graph (units/bases/tiles) | ✅ per-turn COW overlay | end-of-game snapshots only |
| Per-turn policy-guard + what-if | ✅ `hank_guard` / `hank_whatif` | authors/stores the policies |
| Static datalinks KB | read-cache (projected) | ✅ canonical store |
| Learned bitemporal memory | ✖ | ✅ `mem:` facts, valid-time |
| Canonical policies (`aegis:Policy`) | read-cache (projected) | ✅ governed source of truth |
| Provenance / audit / time-travel | ✖ | ✅ transaction + valid time |

The board is **ephemeral** — rebuilt each turn, never the durable record. Only end-of-game learned
state promotes to Quipu as `mem:` facts.

## Role (c): Gameplay policy-guardrail (net-new, depends on d)

Role (c) evaluates Quipu-governed strategic and house-rule policies against the **live state
(role d) + the proposed orders**. It reuses the governance atoms unchanged and generalizes only
the selector language.

**Reused 1:1** from `aegis:Policy` / `Selector` / `Predicate` and Hank's `rules::Rule`
(see [quipu/shapes/policies/treesitter.ttl](https://github.com/scbrown/quipu)):

| Governance field | Role (b) code meaning | Role (c) game-state meaning |
|---|---|---|
| `Predicate.matchType` | must-match / must-not-match over text | must-match / must-not-match over a graph binding |
| `Predicate.gate` | which captures the predicate applies to | which bindings the predicate applies to |
| `Policy.effect` | `warn` \| `deny` on an edit | `warn` (advisory) \| `deny` (strip the order) |
| `Policy.claim` / `targets` | human-readable claim + target class | same — claim + target board class |
| `Policy.boundary` | `"action"` (pre-edit) | **`"order"`** (new boundary) |

**New** for game state:

- **`selectorLang ∈ {tree-sitter, graph-pattern, sparql}`.** Code policies use `tree-sitter`;
  game-state policies use a compact **`graph-pattern`** selector over the board graph. Hank is
  **not a SPARQL store** — full SPARQL stays Quipu's job; the `sparql` value is reserved for
  policies Quipu evaluates, not Hank.
- **`boundary "order"`** — the guard seam, analogous to `boundary "action"` (the pre-edit seam).
- **`tier "game-state"`** on the evaluated fact.

### One concrete policy

`garrison-border-bases` — every base on a hostile border must hold at least one garrisoned unit.
A `deny` policy: an order-set that leaves a border base ungarrisoned is stripped.

```turtle
@prefix aegis: <http://aegis.gastown.local/ontology/> .
@prefix smac:  <http://neuralamplifier.local/ontology/smac/> .

aegis:sel_border_bases a aegis:Selector ;
    aegis:name "border-bases" ;
    aegis:selectorLang "graph-pattern" ;
    aegis:evidenceSource "?b a smac:BaseState ; smac:isBorderBase true" ;
    aegis:tier "game-state" .

aegis:pred_has_garrison a aegis:Predicate ;
    aegis:name "has-garrison" ;
    aegis:evidenceSource "?b smac:garrisonCount ?n | ?n >= 1" ;
    aegis:matchType "must-match" ;
    aegis:tier "game-state" .

aegis:policy_garrison_border_bases a aegis:Policy ;
    rdfs:label "garrison-border-bases" ;
    aegis:targets "BaseState" ;
    aegis:claim "every border BaseState has garrisonCount >= 1 after orders apply" ;
    aegis:boundary "order" ;
    aegis:effect "deny" ;
    aegis:selector aegis:sel_border_bases ;
    aegis:predicate aegis:pred_has_garrison .
```

How Hank evaluates it: after the LLM's orders are speculatively applied to a **post-order COW
overlay** (role e), the `graph-pattern` selector binds `?b` to every `smac:BaseState` where
`smac:isBorderBase` is true. For each binding the predicate checks `smac:garrisonCount >= 1`;
`matchType "must-match"` means a binding that fails is a violation. Because `effect "deny"`, the
specific order that left that base bare (e.g. moving its last garrison out) is returned as a
violation for bounded repair; the rest of the order-set survives. Evaluation is in-memory over the
overlay — no Quipu round-trip per check, because the policy was projected once at game start.

### New surface: `hank_guard`

- MCP tool `hank_guard` + `POST /guard`: `{game_state, proposed_orders, tenant} → {violations[],
  advisories[]}`. `violations[]` are `deny` failures (orders to strip); `advisories[]` are `warn`
  failures (tier-tagged notes handed back to the model).
- **It complements `action_space`, never replaces it.** Legality is the engine's alone. The guard
  operates only on *already-legal* orders and can only **subtract** (strip a legal order) or
  **annotate** (advise on a legal order) — it can never add, invent, or legalize an order.

Example policies beyond garrison-border-bases: don't-break-a-pact-without-casus-belli,
hold-expansion-under-threat, don't-trade-a-tech-that-unlocks-the-leader's-win.

## Role (e): What-if / impact over the live board (net-new)

Role (e) generalizes Hank's shipped `hank_impact` (code blast-radius) from the call graph to the
state graph:

- Speculatively apply a proposed order-set to a **throwaway COW overlay** and compute downstream
  consequences **without committing**: bases newly exposed, units entering an opponent's threat
  range, reachability / zone-of-control / supply shifts, the opponent's next-turn reach.
- Surface as `hank_whatif` (or a `speculate` flag on `hank_guard`, since the guard already needs a
  post-order overlay).

**Hank what-if vs. `quipu_impact remove=true`** — different tools for different questions:

| | Hank what-if (role e) | Quipu `quipu_impact remove=true` |
|---|---|---|
| Domain | ephemeral live board | persisted knowledge graph |
| Question | "if I issue these orders this turn…" | "if this tech / fact were removed…" |
| Speed / scope | fast, in-memory, this-turn, tactical | durable, cross-game, counterfactual |
| Commits? | never (throwaway overlay) | queries the persisted record |

## Where the roles sit in `/decide`

Mirroring the ordered flow in [knowledge-architecture.md](knowledge-architecture.md)
§"Retrieval + guardrail flow" — Hank bookends the turn:

- `world_view` + deltas arrive at `/decide`.
- **Ingest (role d):** Hank builds/patches the hot per-faction COW overlay — in memory, no
  round-trip.
- **Retrieve:** Quipu annotates the prompt (engine-filtered datalinks digest + per-turn context +
  action-space grounding + confidence-gated tactics).
- **LLM proposes orders**, already constrained to `action_space`.
- **What-if (role e):** speculatively apply the top candidate(s); surface consequences for an
  optional revise.
- **Guard (role c):** `hank_guard` over the post-order overlay — `deny` violations stripped and
  returned for bounded repair (≤2 retries, then `end_turn`); `warn` violations become tier-tagged
  advisories.
- **Apply:** surviving orders go to the adapter, which still runs the engine's own validation
  (belt and suspenders).

**Precedence** the guard enforces: engine legality (`action_space`) > Hank deny-policies >
canonical datalinks > engine-observed (Hank-promoted) > house-rule > learned tactic.

## Honesty — what's blocked or net-new

- **Roles (c)/(d)/(e) are net-new engineering**, a real expansion of Hank's mandate (code graph →
  general in-memory fact graph + policy/what-if harness). Gated twice: by Hank **Phase 4**
  (HTTP-only promotion; `quipu` crate dep commented out; verdict signing **unkeyed**, so
  engine-observed facts are trusted-advisory, not cryptographically trusted) and by the non-code
  ingestion capability that does not exist today. **Role (c) depends on role (d).**
- **Hank is not a SPARQL store.** Game-state selectors are a compact `graph-pattern` subset; full
  SPARQL stays in Quipu.
- **The guard sees an APPROXIMATED post-order board.** The COW overlay re-implements a slice of
  the engine's order-semantics *outside* the engine — a divergence risk. So **deny-policies stay
  conservative**, warn is preferred where the outcome is uncertain, and the **engine remains the
  sole authority** on legality and effects. This is exactly why the guard complements, never
  replaces, `action_space`.
- **These net-new Hank capabilities belong in Hank's own `docs/hank-spec.md`** as new FRs
  (non-code ingestion; the game-state selector model; `hank_guard`/`hank_whatif`; per-game/
  per-faction tenancy as an isolation boundary). How Neural Amplifier *consumes* them lives here.

## See also

- [hank-integration.md](hank-integration.md) — roles (a) grounding and (b) dev-time guardrails.
- [tenancy-and-isolation.md](tenancy-and-isolation.md) — how the COW overlays become the
  fog-of-war isolation boundary across opponents.
- [knowledge-architecture.md](knowledge-architecture.md) — the umbrella design.
