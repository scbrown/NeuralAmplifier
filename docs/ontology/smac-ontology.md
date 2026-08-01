# SMAC Ontology — the static datalinks plane

> **Status: design / pre-alpha.** No ingester or `.ttl` exists yet. This is the
> class/predicate reference for the **datalinks plane** described in
> [../knowledge-architecture.md](../knowledge-architecture.md) — the static,
> canonical SMAC rules parsed from `alphax.txt`. The companion SHACL guardrail
> reference is [smac-shapes.ttl.md](smac-shapes.ttl.md).

Everything here is grounded against the real
[`thinker/docs/alphax.txt`](https://github.com/scbrown/thinker) data file; the
example values below are quoted from actual rows so the class→section mapping is
verifiable, not invented.

## Namespace decision

- **`smac:`** = `<http://neuralamplifier.local/ontology/smac/>` — SMAC *rules*
  (technologies, facilities, units, social models, terrain).
- **`bobbin:`** = `<http://aegis.gastown.local/ontology/>` — *code structure*
  (modules, symbols, call graph), Hank's identity space.
- **`aegis:`** = governance atoms (`Policy`/`Selector`/`Predicate`/`Verdict`),
  reused unchanged.

Rules and code are **different domains**, so they get different namespaces. A
merged namespace would let a query for "the Fusion Power tech" collide with "the
`fusion()` scoring function" and would make the trust tags (below) ambiguous
about *what kind of thing* they annotate. Keeping `smac:` distinct from
`bobbin:` is the same discipline Hank enforces on its own base IRI: the
namespace is data identity, not cosmetics — repointing it silently mints
parallel IRIs that never join.

The two planes meet at **exactly one** predicate:

```turtle
smac:computedBy  rdfs:range  bobbin:CodeSymbol .
```

`smac:computedBy` is the **Hank grounding bridge**. A rule fact points at a
Hank-promoted `bobbin:CodeSymbol` to say "here is the engine function that
*actually* computes this," turning "how does Thinker really score this facility"
into a single graph hop. This is the only sanctioned crossing between the two
namespaces; see the "engine-observed via Hank" note at the end.

## Core classes

Each class names the `alphax.txt` section it is parsed from, with real example
values quoted from that section.

### `smac:Technology` — `#TECHNOLOGY`

The atom of the tech tree. Key predicates: `abbrev` (string),
`aiWeightGrowth` / `aiWeightTech` / `aiWeightWealth` / `aiWeightPower` (the four
AI-interest integers), `requiresTech` (**0..2** prerequisites — this edge set
*is* the tech dependency graph), `flags`.

- `Centauri Ecology` — `abbrev "Ecology"`; weights Growth 0, Tech 1, Wealth 2,
  Power 3; `requiresTech` none (a root tech).
- `Fusion Power` — `abbrev "Fusion"`; weights Growth 3, Tech 4, Wealth 3,
  Power 1; `requiresTech` **two** — `Pre-Sentient Algorithms` (`Algor`) and
  `Superconductor` (`Super`).

The two prerequisite columns give the 0..2 `requiresTech` fan-in: `None,None` →
0 edges, one named tech → 1, two → 2.

### `smac:Chassis` / `Reactor` / `Weapon` / `Armor` / `Ability` — `#CHASSIS`…`#ABILITIES`

Unit *components*. Each carries component stats, a `cost`, and a `requiresTech`.

- **Chassis** (`#CHASSIS`): `Infantry` (speed 1, land triad, cost 1,
  `requiresTech` none); `Foil` (speed 4, sea triad, `requiresTech "DocFlex"`);
  `Needlejet` (speed 8, air triad, `requiresTech "DocAir"`).
- **Reactor** (`#REACTORS`): `Fission Plant` (power 1, no preq);
  `Fusion Reactor` (power 2, `requiresTech "Fusion"`); `Quantum Chamber`
  (power 3, `requiresTech "Quantum"`).
- **Weapon** (`#WEAPONS`): `Laser` (attack 2, `requiresTech "Physic"`);
  `Fusion Laser` (attack 10, `requiresTech "SupLube"`).
- **Armor** (`#DEFENSES`): `Synthmetal Armor` (defense 2,
  `requiresTech "Indust"`); `Silksteel Armor` (defense 4,
  `requiresTech "Alloys"`).
- **Ability** (`#ABILITIES`): `Super Former` (cost 1,
  `requiresTech "EcoEng2"`); `Deep Radar` (cost 0, `requiresTech "MilAlg"`).

### `smac:UnitProto` — `#UNITS`

A predefined or designed unit template. Predicates:
`hasChassis` / `hasReactor` / `hasWeapon` / `hasArmor` / `hasAbility` (object
edges into the component classes above) and `isPredefined` (boolean — true for
the 26 stock rows in `#UNITS`).

- `Colony Pod` — `hasChassis Infantry`, `hasWeapon "Colony Pod"` (module),
  `hasArmor Scout`, `isPredefined true`, `requiresTech` none.
- `Formers` — `hasChassis Infantry`, `hasWeapon "Formers"`, `hasArmor Scout`,
  `isPredefined true`, `requiresTech "Ecology"`.
- `Probe Team` — `hasChassis Speeder`, `hasWeapon "Probe Team"`,
  `isPredefined true`, `requiresTech "PlaNets"`.

### `smac:Facility` — `#FACILITIES`

A base facility. Predicates: `cost`, `maintenance`, `requiresTech`,
`obsoletedBy`, `effectText`, and the optional `computedBy` bridge.

- `Recycling Tanks` — `cost 4`, `maintenance 0`, `requiresTech "Biogen"`,
  `effectText "Bonus Resources"`.
- `Network Node` — `cost 8`, `maintenance 1`, `requiresTech "InfNet"`,
  `effectText "Labs Bonus"`.

`cost` / `maintenance` / `requiresTech` / `effectText` map to raw columns.
`obsoletedBy` is a **modeled** predicate sourced from encyclopedia prose (not a
raw column), and `computedBy` is the Hank bridge — both carry their own
provenance tags like any other fact.

### `smac:SecretProject` ⊂ `smac:Facility` — `#FACILITIES` (SP rows)

A one-of-a-kind wonder. Inherits every `Facility` predicate and adds the **five
AI-priority integers** from the trailing SP columns:
`aiPriorityFight`, `aiPriorityPower`, `aiPriorityTech`, `aiPriorityWealth`,
`aiPriorityGrowth`.

- `The Human Genome Project` — `cost 20`, `requiresTech "Biogen"`,
  `effectText "+1 Talent Each Base"`; priorities Fight −1, Power 0, Tech 1,
  Wealth 1, Growth 2.
- `The Neural Amplifier` — `cost 30`, `requiresTech "Neural"`,
  `effectText "Psi Defense +50%"`; priorities Fight 0, Power 2, Tech 0,
  Wealth 0, Growth 1.

All five integers are mandatory (see `SecretProjectShape` in the shapes doc): a
half-parsed SP that dropped a column is refused rather than stored with a hole.

### `smac:SocialCategory` / `SocialChoice` / `SocialEffect` / `SocialLadderRung` — `#SOCIO`, `#SOCECONOMY`…`#SOCRESEARCH`

The social-engineering model. Predicates: `inCategory`, `requiresTech`,
`affectsModel`, `delta`, `level`.

- **`SocialCategory`** (`#SOCIO` header): `Politics`, `Economics`, `Values`,
  `Future Society`.
- **`SocialChoice`**: `Free Market` (`inCategory` Economics,
  `requiresTech "IndEcon"`); `Democratic` (`inCategory` Politics,
  `requiresTech "EthCalc"`).
- **`SocialEffect`** (a choice's per-model deltas): `Free Market` →
  `affectsModel ECONOMY` `delta +2` (`++ECONOMY`), `affectsModel PLANET`
  `delta −3` (`---PLANET`), `affectsModel POLICE` `delta −5` (`-----POLICE`).
- **`SocialLadderRung`** (`#SOCECONOMY`…): one rung per `level` with its
  `effectText`. ECONOMY `level 5` → `"+1 energy/sq; +4 energy/base; +3
  commerce!!!!"`; EFFIC `level −4` → `"ECONOMIC PARALYSIS"`.

### `smac:ResourceYield` / `TerraformAction` — `#RESOURCEINFO`, `#TERRAIN` — **parsed**

Both ship: 9 yield rows and 20 terraform orders, every node carrying `sourcedFrom`.

- **`ResourceYield`** (`#RESOURCEINFO`): `yieldNutrients` / `yieldMinerals` /
  `yieldEnergy`. `Borehole Square` → 0/6/6; `Forest Square` → 1/2/1; `Ocean
  Square` → 1/0/0.

  A `*` column is **omitted rather than zeroed**. The file's own comment says those
  values are "ignored entirely" — the engine derives them from the tile's temperature,
  rainfall and rockiness — so `Improved Land` carries `yieldNutrients 1` and no mineral
  or energy predicate at all. Emitting `0` would assert that improving land produces no
  minerals, which is confidently wrong where an absent predicate is merely a gap.

- **`TerraformAction`** (`#TERRAIN`): `terraformVerb`, `baseRate`, `requiresTech`,
  and either `seaVariant` + `seaRequiresTech` or `landOnly`. `Thermal Borehole`
  (`EcoEng`, rate 24); `Mag Tube` (`Magnets`, rate 3); `Soil Enricher` (`EcoEng2`,
  rate 8, `landOnly`).

  Two rows are named **Fungus** — one removes it, one plants it — so the node id is
  built from the verb as well: `terra:remove-fungus`, `terra:plant-fungus`. Keyed on the
  name alone they collide and one silently wins.

  `landOnly` is stated rather than inferred from a missing `seaVariant`, because the
  file writes "has no sea form" as the literal string `Disable` in the sea prerequisite
  column — the same token that means "switched off" in the land column. Forest is
  land-only; Monolith is genuinely disabled. One predicate each, so the two can never be
  read as the same thing.

### `smac:Faction` — `#FACTIONS` / `#NEWFACTIONS`

Predicates: `agenda`, `socialPreference`, `socialAversion`, `startingBonus`.
The **roster** is the `alphax.txt` section — `GAIANS`, `HIVE`, `UNIV`,
`MORGAN`, `SPARTANS`, `BELIEVE`, `PEACE` (`#FACTIONS`) plus `CYBORG`,
`PIRATES`, `DRONE`, `ANGELS`, `FUNGBOY`, `CARETAKE`, `USURPER`
(`#NEWFACTIONS`). The per-faction detail (`agenda`, `socialPreference`,
`socialAversion`, `startingBonus`) is parsed from each faction's own `.txt`
file, so those predicates carry `sourcedFrom` pointing at the faction file, not
the `alphax.txt` roster node — e.g. `GAIANS` → `socialPreference` Green,
`socialAversion` Free Market.

## The tech graph as SPARQL

Because `smac:requiresTech` is a real edge on every class, the whole dependency
tree is queryable with property paths. "What must I research to unlock Fusion
Power?" is the transitive closure `smac:requiresTech+`:

```sparql
PREFIX smac: <http://neuralamplifier.local/ontology/smac/>

SELECT ?prereq WHERE {
  smac:FusionPower smac:requiresTech+ ?prereq .
  # engine + tier filter always applied — see provenance below
  smac:FusionPower smac:appliesToEngine ?e .
  FILTER(?e IN ("smac", "thinker"))
}
```

- **"cheapest path to X"** ranks the `requiresTech+` frontier by accumulated
  research cost — a weighted path query over the same edges.
- **"if I skip Ecology, what breaks?"** is the counterfactual
  `quipu_impact remove=true` on the `Centauri Ecology` node: Quipu returns the
  blast radius — `Formers`, `The Weather Paradigm`, every terraform action
  gated on `EcoEng`/`EcoEng2`, and so on down the closure.

## The anti-masquerade provenance model

Every rule fact **must** carry three provenance predicates. SHACL
(`RuleProvenanceShape`, see the shapes doc) refuses any fact that lacks them —
this is the gate that stops a house-rule or a GLSMAC deviation from posing as
canonical SMAC.

- **`smac:appliesToEngine`** ∈ `{ smac, thinker, glsmac }` — which engine this
  rule is true for.
- **`smac:ruleTier`** ∈ `{ canonical, house-rule, engine-observed, aspirational }`:
  - `canonical` — stock `alphax.txt` / encyclopedia.
  - `house-rule` — a Thinker override of stock behavior.
  - `engine-observed` — Hank-promoted from engine C++ (carries `computedBy`).
  - `aspirational` — a GLSMAC system that does not exist yet, marked loudly.
- **`smac:sourcedFrom`** (IRI) — points at a datalinks section node (e.g. the
  `#FACILITIES` node) **or** a Hank-promoted `bobbin:CodeSymbol`.

```turtle
smac:TheNeuralAmplifier
    a smac:SecretProject ;
    smac:cost 30 ;
    smac:requiresTech smac:Neural ;
    smac:appliesToEngine "smac" ;
    smac:ruleTier        "canonical" ;
    smac:sourcedFrom     <…/alphax#FACILITIES> .
```

How the gate holds:

- **Retrieval always filters** `appliesToEngine ∈ {smac, <current engine>}`. In
  a Thinker game a `glsmac`-only fact is simply never selected, so it cannot
  surface as canonical. A GLSMAC deviation is scoped to `glsmac` and stays
  invisible to Thinker retrieval.
- **Overrides are explicit and bitemporal.** A Thinker house-rule that changes a
  stock value is written with `quipu_set` + an explicit `smac:supersedes` edge
  to the canonical fact. Time-travel still shows the canonical value; the diff
  is auditable. The house-rule never *overwrites* canonical — it shadows it, and
  only for `thinker`.
- **Per-engine `group_id`s** (`datalinks:smac`, `datalinks:thinker`,
  `datalinks:glsmac`) allow clean bulk re-sync of one engine's rules without
  touching another's. (`group_id` organizes; it is **not** a security boundary —
  see the tenancy doc.)

The posture mirrors the sibling shape files: **permissive on domain shape,
strict on the provenance/tier predicates** — because the tier tag is the
reader's only signal of how much to trust the fact.

## Engine-observed via Hank

When a rule's *real* behavior lives in engine C++ rather than `alphax.txt`, Hank
analyzes the scoring function (`hank_analyze`/`hank_symbols`), promotes the
symbol to Quipu (`hank_promote` → a `bobbin:CodeSymbol`), and the rule fact
points at it with `smac:computedBy`, tagged `ruleTier "engine-observed"` and
`sourcedFrom` = that symbol. This is the single sanctioned `smac:`→`bobbin:`
crossing. See [../hank-integration.md](../hank-integration.md) for the
tool→source→Quipu path and its honest blockers (HTTP-only promotion, unkeyed
signing at Hank Phase 4).
