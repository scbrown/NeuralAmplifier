# SMAC SHACL Shapes — the write-time guardrail

> **Status: design / pre-alpha.** The shapes below are **illustrative**. The real
> `.ttl` lands in the K1 datalinks build phase (see
> [../knowledge-architecture.md](../knowledge-architecture.md) §Rollout). This
> doc is the reference for *what the shapes enforce and why*. The class and
> predicate vocabulary is defined in
> [smac-ontology.md](smac-ontology.md); every value here is grounded against the
> real `alphax.txt` rows quoted there.

## Posture: permissive on domain, strict on provenance

SHACL is the **write-time guardrail** on the datalinks plane — it validates
every rule fact *before* it is stored, the same role Quipu's
[`code-entities.ttl`](https://github.com/scbrown/quipu) plays for code entities
and Hank's `code-edges.ttl` plays for promoted edges.

The posture is deliberately lopsided, copied from those two files:

- **Permissive on domain shape.** A datalinks ontology over-constrained will
  reject legitimate facts from the messy real `alphax.txt` (deleted rows, empty
  facility slots, `Disable` prerequisites). A guardrail that silently refuses
  valid facts is worse than one that admits loose ones, because the refusal is
  invisible to the caller who never wrote them. So domain shapes constrain the
  handful of fields a fact is *meaningless* without, and leave the rest open.
- **Strict on provenance / tier.** This is the one place the shapes are
  unforgiving — exactly as `code-edges.ttl` is strict only on `hasTier`. The
  engine+tier tag is the reader's only signal of trust; an absent or free-text
  tier is worse than a missing fact, because it looks as authoritative as any
  other. So the provenance predicates are `sh:in` a closed vocabulary with
  `minCount`/`maxCount 1`, from the very first load.

## Example shapes

Three `sh:NodeShape`s in the `code-entities.ttl` house style — `sh:targetClass`,
`sh:property` blocks with `sh:path` / `sh:datatype` / `sh:minCount` /
`sh:maxCount`, header comments explaining intent.

### (1) `smac:RuleProvenanceShape` — the anti-masquerade gate

Targets the rule-bearing classes and requires all three provenance predicates.
This is the shape that stops a Thinker house-rule or a GLSMAC deviation from
being stored as canonical SMAC. It mirrors `code-edges.ttl`'s `HasTierShape`,
generalized from one tier predicate to three.

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix smac: <http://neuralamplifier.local/ontology/smac/> .

# ── RuleProvenance ─────────────────────────────────────────────
#
# THE anti-masquerade gate. Every rule fact MUST declare which engine
# it is true for, at which tier, sourced from where. Strict on all
# three — closed vocabularies, exactly one value each — because the
# tag is the reader's only signal of trust (cf. code-edges HasTierShape).
# Applied to the rule-bearing classes; components inherit via UnitProto.

smac:RuleProvenanceShape a sh:NodeShape ;
    sh:targetClass smac:Technology ;
    sh:targetClass smac:Facility ;
    sh:targetClass smac:SocialChoice ;
    sh:targetClass smac:UnitProto ;
    sh:property [
        sh:path smac:appliesToEngine ;
        sh:datatype xsd:string ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:in ( "smac" "thinker" "glsmac" ) ;
    ] ;
    sh:property [
        sh:path smac:ruleTier ;
        sh:datatype xsd:string ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:in ( "canonical" "house-rule" "engine-observed" "aspirational" ) ;
    ] ;
    sh:property [
        sh:path smac:sourcedFrom ;
        sh:nodeKind sh:IRI ;
        sh:minCount 1 ;
    ] .
```

Rationale: `appliesToEngine` and `ruleTier` are `sh:in` closed sets with
`min/maxCount 1` — a fact tagged `"THINKER-ish"` or missing a tier is refused,
not stored. `sourcedFrom` is `sh:nodeKind sh:IRI` (an `alphax` section node or a
Hank-promoted `bobbin:CodeSymbol`) with `minCount 1` but **no** `maxCount` — a
fact may cite more than one source. Note: a single `SecretProject` is targeted
here **through** `smac:Facility` (its superclass), so it too must carry
provenance.

### (2) `smac:TechnologyShape` — permissive domain shape

The domain half. Constrains only the fields a technology is meaningless without,
and caps `requiresTech` at the 0..2 the tech-graph model depends on.

```turtle
# ── Technology ─────────────────────────────────────────────────
#
# Domain shape, permissive on purpose. A tech needs an abbrev (it is the
# join key every other section uses as `requiresTech`). The four AI
# weights are optional 0..1 integers — many stock rows share values and
# a missing weight is not a reason to refuse the fact. requiresTech is
# capped at 2: that cap IS the tech-graph model (two prereq columns in
# #TECHNOLOGY), so a third edge is a parse error worth refusing.

smac:TechnologyShape a sh:NodeShape ;
    sh:targetClass smac:Technology ;
    sh:property [
        sh:path smac:abbrev ;
        sh:datatype xsd:string ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:aiWeightGrowth ;
        sh:datatype xsd:integer ;
        sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:requiresTech ;
        sh:class smac:Technology ;
        sh:maxCount 2 ;
    ] .
```

Rationale: `abbrev` is `min/maxCount 1` — it is the token every other section
cites (`Fusion Power` is referenced everywhere as `Fusion`), so a tech without
one cannot be wired into the graph. `aiWeightGrowth` (and its Tech/Wealth/Power
siblings, elided here) is `maxCount 1` with **no** `minCount` — permissive, a
tech missing a weight still validates. `requiresTech` uses `sh:class
smac:Technology` — this is one place a *class* constraint is safe, because a
prerequisite genuinely must be another `Technology` — with `maxCount 2`
enforcing the two-column reality of `#TECHNOLOGY` (e.g. `Fusion Power` requires
exactly `Algor` + `Super`).

### (3) `smac:SecretProjectShape` — all five AI ints or refuse

A `SecretProject` is only usable by the AI-priority model if **all five**
integers are present; a half-parsed SP that dropped a trailing column would
score wrong silently. So each of the five is `min/maxCount 1`.

```turtle
@prefix bobbin: <http://aegis.gastown.local/ontology/> .

# ── SecretProject ──────────────────────────────────────────────
#
# The one place the domain shape is STRICT: all five aiPriority ints
# are required (min/max 1 each). #FACILITIES SP rows carry exactly five
# trailing columns (ai-fight, -power, -tech, -wealth, -growth); if the
# parser drops one, the SP scores wrong forever and silently. Refuse it
# at write time instead. computedBy is the optional Hank bridge — an SP
# whose real behavior was read from engine C++ points at a CodeSymbol.

smac:SecretProjectShape a sh:NodeShape ;
    sh:targetClass smac:SecretProject ;
    sh:property [
        sh:path smac:aiPriorityFight ;
        sh:datatype xsd:integer ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:aiPriorityPower ;
        sh:datatype xsd:integer ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:aiPriorityTech ;
        sh:datatype xsd:integer ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:aiPriorityWealth ;
        sh:datatype xsd:integer ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:aiPriorityGrowth ;
        sh:datatype xsd:integer ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:computedBy ;
        sh:class bobbin:CodeSymbol ;
        sh:maxCount 1 ;
    ] .
```

Rationale: the five `aiPriority*` predicates are each `min/maxCount 1` — this is
the strict exception to the otherwise-permissive domain posture, justified
because `The Human Genome Project` (`−1, 0, 1, 1, 2`) and `The Neural Amplifier`
(`0, 2, 0, 0, 1`) are *only* AI-schedulable if the full five-tuple survived
parsing. `computedBy` is `sh:class bobbin:CodeSymbol` (the sanctioned
`smac:`→`bobbin:` crossing) with `maxCount 1` and no `minCount` — optional,
because most SPs are grounded in `alphax.txt` prose, not engine C++.

### (4) `smac:TurnMechanicShape` — reversibility is not optional

A `TurnMechanic` is retrieved to answer "can I take this back?", so the one predicate that must
never be absent is the one that answers it.

```turtle
# ── TurnMechanic ───────────────────────────────────────────────
#
# Behaviour rather than a data-file row (../../datalinks/mechanics.ttl).
# Strict on `reversible` for the same reason SecretProject is strict on
# its five ints: the fact is retrieved precisely to answer "can this be
# undone", and a mechanic that is silent on it does not merely say less
# — it reads as "no constraint" at the moment an agent is deciding
# whether to spend something it cannot recover.
#
# Deliberately NOT minCount 1, because most mechanics are neither
# reversible nor irreversible in any useful sense (a cycle is not an
# act). It is maxCount 1 and typed: state it once, or not at all.

smac:TurnMechanicShape a sh:NodeShape ;
    sh:targetClass smac:TurnMechanic ;
    sh:property [
        sh:path rdfs:label ;
        sh:datatype xsd:string ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:reversible ;
        sh:datatype xsd:boolean ;
        sh:maxCount 1 ;
    ] ;
    sh:property [
        sh:path smac:enables ;
        sh:class smac:TurnMechanic ;
    ] ;
    sh:property [
        sh:path smac:computedBy ;
        sh:class bobbin:CodeSymbol ;
        sh:maxCount 1 ;
    ] .
```

Rationale, and one deliberate omission worth defending. `reversible` is **`maxCount 1` without a
`minCount`** rather than required. Most mechanics are not acts at all — the activation cycle is
not something you can "take back" — and forcing a boolean onto them would mean writing `false`
where the truthful answer is "not applicable", which is how a vocabulary starts lying. What the
shape does enforce is that a mechanic which *does* state reversibility states it once and as a
boolean, so a retrieval can branch on it without parsing prose.

`enables` is `sh:class smac:TurnMechanic` — a mechanic can only enable another mechanic, which
keeps it from drifting into a general-purpose "related to" edge. No `maxCount`: one mechanic may
enable several.

`computedBy` matches the SecretProject treatment exactly — optional, `maxCount 1`, and the only
sanctioned `smac:`→`bobbin:` crossing. It is present on the Thinker-specific mechanics
(`engine-observed`) and absent on the stock ones, which are player-interface behaviour with no
single function behind them.

## Loading and validation

These shapes load into the datalinks graph the same way `code-entities.ttl` does
— via the `quipu_shapes` MCP tool or an HTTP `POST /shapes` with
`{"action":"load","name":"smac-shapes","turtle":"<contents>"}`. Once loaded they
are enforced on **every** datalinks write: each `quipu_set` and each
`quipu_episode` that lands a rule fact is validated against the compiled shape
set before it is committed, so a fact missing its engine+tier tag — or a
`SecretProject` missing an AI integer — is refused at the boundary rather than
discovered wrong at retrieval time.

> `quipu-server` must run with `--features shacl` for these to be enforced;
> without it the shapes load but do not gate writes. See
> [../knowledge-architecture.md](../knowledge-architecture.md) §Honesty.
