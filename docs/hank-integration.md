# Hank Integration — Grounding & Dev-Time Guardrails

> **Status: design / architecture (pre-alpha).** No orchestrator code exists yet, and the Hank
> capabilities described here are partly built and partly net-new (marked below). This document
> covers the two roles where Hank works over **code** — its native domain. For the three roles
> where Hank is generalized to work over **live game state** (the policy harness, the hot state
> graph, and what-if), see [policy-harness.md](policy-harness.md). The umbrella design and the
> canonical vocabulary live in [knowledge-architecture.md](knowledge-architecture.md); this doc
> does not contradict it.

Hank is an in-memory code-structure engine (AST, symbols, call graph, dataflow) that serves
tier-tagged facts over MCP/HTTP and promotes committed structure into [Quipu](https://github.com/scbrown/quipu)
as governed RDF. Two of its five roles in Neural Amplifier use exactly that native capability:

- **Role (a) — engine-mechanics grounding:** read the Thinker/GLSMAC C++ scoring functions and
  promote them as `bobbin:CodeSymbol`s, so a `smac:` rule can cite *how the engine actually
  scores* the decision.
- **Role (b) — dev-time guardrail:** enforce Quipu-governed `aegis:Policy` rows against the
  orchestrator's *own* source at edit time.

Both reuse Hank's shipped machinery. The honest blockers are collected at the end.

## Role (a): Engine-mechanics grounding

[VISION.md](https://github.com/scbrown/NeuralAmplifier/blob/main/VISION.md) says we feed state, not rules — Claude already knows SMAC. But Claude
knows *stock* SMAC broadly; it cannot cite how a *specific engine* scores a specific decision, and
it cannot tell a Thinker house-rule from canonical behavior. Role (a) closes that gap by treating
the engine's C++ scoring functions as ground truth and promoting them into the knowledge graph.

The flow, per [knowledge-architecture.md](knowledge-architecture.md) §"Hank's five roles" (a):

- Point Hank's read tools (`hank_analyze`, `hank_symbols`, `hank_callers`, and `hank_callees`
  for the fan-out) at the engine source.
- `hank_promote` writes the resolved symbol into Quipu as a `bobbin:CodeSymbol`, SHACL-validated,
  with Hank's own `tier` (`treesitter` | `lsp` | `cpg`) attached.
- A `smac:` rule in the datalinks plane sets `smac:computedBy → bobbin:CodeSymbol`, the single
  deliberate join between the `smac:` (rules) and `bobbin:` (code) namespaces. "How the engine
  actually scores this" becomes one graph hop.
- The rule fact carries `smac:ruleTier "engine-observed"` — Hank's code-structure `tier` is
  distinct from the SMAC provenance tier, but the promotion is what *earns* the `engine-observed`
  label. It is never `canonical` (that is stock `alphax.txt`) and never `house-rule`.
- **Re-sync on engine change.** Because promotion is bitemporal, re-promoting after an engine
  edit supersedes the belief with an explicit new transaction-time record; time-travel still
  shows the old belief. A `smac:supersedes` edge keeps the diff auditable.

### Tool → source → Quipu

The scoring functions worth grounding, from the Thinker fork (paths verified in
[thinker-adapter-notes.md](thinker-adapter-notes.md)); GLSMAC has no equivalents yet (see blockers):

| Hank tool | Engine source (symbol) | Grounds which `smac:` fact | Promoted as |
| --- | --- | --- | --- |
| `hank_analyze` / `hank_symbols` | `plan.cpp` `facility_score`, `psi_score`, `design_units` | how a facility / psi unit / unit design is valued | `bobbin:CodeSymbol` |
| `hank_symbols` / `hank_callers` | `build.cpp` `select_build`, `unit_score` | production pick & per-unit build weighting | `bobbin:CodeSymbol` |
| `hank_symbols` | `tech.cpp` `mod_tech_val` | research valuation (tech AI weights) | `bobbin:CodeSymbol` |
| `hank_symbols` / `hank_callees` | `move.cpp` `base_tile_score`, `former_tile_score` | tile-value / terraform scoring | `bobbin:CodeSymbol` |
| `hank_symbols` | `veh_combat.cpp` (combat-odds calc) | combat resolution odds | `bobbin:CodeSymbol` |

The join, as data (illustrative — the `smac:` fact points at the promoted symbol):

```turtle
@prefix smac:   <http://neuralamplifier.local/ontology/smac/> .
@prefix bobbin: <http://aegis.gastown.local/ontology/> .

smac:facility_RecyclingTanks a smac:Facility ;
    smac:appliesToEngine "thinker" ;
    smac:ruleTier "engine-observed" ;
    smac:sourcedFrom bobbin:sym_plan_facility_score ;
    smac:computedBy  bobbin:sym_plan_facility_score .

bobbin:sym_plan_facility_score a bobbin:CodeSymbol ;
    bobbin:name "facility_score" ;
    bobbin:file "src/plan.cpp" ;
    bobbin:tier "treesitter" .          # Hank's structural tier, carried through
```

Retrieval filters `smac:appliesToEngine ∈ {smac, <current engine>}`, so a Thinker-observed
scoring rule never surfaces as canonical, and a GLSMAC deviation never surfaces in a Thinker game.
The rule *tags* the belief; the reader trusts the tag.

## Role (b): Dev-time guardrail on the orchestrator's own code

Role (b) is Hank doing exactly what it already ships for: enforcing Quipu-governed `aegis:Policy`
rows against source at edit time. Here the source is **Neural Amplifier's own orchestrator** —
the contract handler and the retrieval/guard code — not the game.

The mechanism, unchanged from Hank's shipped policy-edit-hook path:

- Quipu holds the canonical `aegis:Policy` rows (see
  [quipu/shapes/policies/treesitter.ttl](https://github.com/scbrown/quipu)): a `Selector`
  (tree-sitter `.scm` capture query) + a `Predicate` (regex + `matchType` + `gate`), bound at
  `boundary "action"` (the pre-edit seam).
- Hank holds only a **projected read cache** of those policies; the definitions live in Quipu.
  The field names map 1:1 onto Hank's `rules::Rule`: `Selector.evidenceSource → Rule.query`,
  `Predicate.evidenceSource → Rule.pattern`, `Predicate.matchType → Rule.match_type`,
  `Predicate.gate → Rule.gate`.
- Hank enforces them at `hank hook pre-edit` (wired into a Claude Code `PreToolUse` /
  `PostToolUse` hook). `effect "warn"` is advisory; `effect "deny"` blocks the edit before it
  lands.
- `hank_verify` returns the FR-23/FR-24 edit-buffer verdict; `hank_impact` gives per-tenant
  blast radius, so an edit to a shared contract type surfaces every call site it would break
  *before* it lands.

This is "policy-as-ontology": a new orchestrator invariant is a graph assertion in Quipu, not a
new bespoke linter. Example orchestrator policies (candidates — authored as `aegis:Policy` rows,
tree-sitter selectors over the orchestrator's own language):

- **`/decide` falls through to `end_turn` on exception** — every exception path in the decision
  handler must reach the safe fallback (`end_turn` where present, else the deterministic default),
  matching the contract's degradation rule in [contract.md](contract.md). A selector captures the
  `catch`/`except` blocks in the handler; a predicate requires the fallback call.
- **No order returned that isn't from `action_space`** — the order-assembly code must construct
  `choices` only from `action_id`s present in the incoming world view. A selector captures the
  order-construction site; a predicate gates that it references the validated action-space set,
  never a literal action.
- **Every `mem:` write goes through `quipu_episode`** — free Turtle writes to the memory plane
  are denied, so provenance (`prov:wasGeneratedBy`) is never skipped.

Because these are `hank_verify`/`hank_impact` over real code, role (b) is enforceable as soon as
Hank's dev-guardrail path (Phase 4 promotion / verdict acceptance) is wired — it does **not**
depend on the net-new game-state capabilities that roles (c)/(d)/(e) need.

## Honest blockers

Role (a) grounding and role (b) guardrails are gated on Hank **Phase 4** and on engine reality:

- **Promotion is HTTP-only.** The `quipu` crate dependency is commented out; `hank_promote`
  reaches Quipu over HTTP, not in-process. Batch-promoting a large engine's scoring surface is a
  round-trip cost, not a library call — budget accordingly.
- **Verdict signing is UNKEYED.** Hank's `VerifierRegistration` (see
  [treesitter.ttl](https://github.com/scbrown/quipu), `aegis:reg_hank_structural`) is a valid
  *authority* record, but `publicKey` is omitted until Hank's signing identity is provisioned. So
  an `engine-observed` fact promoted by role (a) is **trusted-advisory, not cryptographically
  trusted** — a verdict is *accepted* at write, and only *trusted* once a human registers Hank's
  key. Precedence (below) treats `engine-observed` accordingly: below canonical, above house-rule.
- **The `freshness` tag does not exist yet.** FR-3's `tier` half ships; its `freshness` half is
  Phase 3. Per repo convention, a response **omits** freshness rather than faking a `fresh` tag.
  Grounding facts therefore carry a `tier` but no freshness signal today.
- **GLSMAC has no scoring functions to ground.** GLSMAC lacks production, tech, social
  engineering, diplomacy, and combat, so role (a) has only the Thinker C++ surface to read today;
  all `glsmac`-scoped rules stay `ruleTier "aspirational"`.

**Precedence** (unchanged from [knowledge-architecture.md](knowledge-architecture.md)): engine
legality (`action_space`) > Hank deny-policies > canonical datalinks > **engine-observed
(Hank-promoted, role a)** > house-rule > learned tactic. Grounding informs and cites; it never
overrides the engine's own legality gate.

## See also

- [policy-harness.md](policy-harness.md) — roles (c) gameplay guard, (d) hot state graph, (e)
  what-if; the net-new non-code ingestion and `hank_guard`/`hank_whatif` surfaces.
- [tenancy-and-isolation.md](tenancy-and-isolation.md) — per-game/per-faction isolation, the Hank
  fog model, per-identity Quipu databases.
- [knowledge-architecture.md](knowledge-architecture.md) — the umbrella: two knowledge planes,
  the `smac:` ontology, the anti-masquerade guardrail, the `/decide` flow.
