# Strategy Knowledge — the curated doctrine plane

> **Status: design / architecture (pre-alpha).** No ingester, `.ttl`, or retrieval
> code exists yet. This document specifies a **third** knowledge plane — curated
> expert *doctrine* — alongside the static datalinks plane
> ([ontology/smac-ontology.md](ontology/smac-ontology.md)) and the learned-memory
> plane ([learned-memory.md](learned-memory.md)), under the umbrella in
> [knowledge-architecture.md](knowledge-architecture.md).

## Why doctrine is its own plane

[knowledge-architecture.md](knowledge-architecture.md) defines two planes: the
**datalinks** plane (canonical `smac:` rules parsed from `alphax.txt`) and the
**memory** plane (`mem:` facts learned from *our* games). Neither captures a third
thing the agent needs to play well: **expert knowledge about how to use the rules**.

- The datalinks plane knows a Particle Impactor is attack 4 and needs Nonlinear
  Mathematics. It does **not** know that "Impact Rovers by the early 30s win the
  momentum game on a shared continent" — that is a human judgement, not a row.
- The memory plane learns tactics from play, but a fresh `player_identity` starts
  empty. Without a seed it must rediscover a rover rush from scratch, game after game.

Doctrine fills the gap: **community-sourced expert heuristics** (Velociryx's guide,
GameFAQs FAQs, alphacentauri2 / Fandom wikis) about *which* legal moves are good and
*when*. It is neither ground-truth rules nor our own experience — it is a curated
prior, so it gets its own `strat:` namespace, its own tier, and its own provenance.

### How the three compose

- **Rules ground it.** A `strat:UnitTemplate` references real `smac:Chassis` /
  `smac:Weapon` / `smac:Ability` nodes; a `strat:` fact that names a component the
  current engine does not have is invalid. Doctrine never invents mechanics.
- **Doctrine seeds it.** On turn 1 of game 1, with no learned memory, doctrine is the
  agent's opening playbook.
- **Learned memory refines and overrides it.** Per
  [knowledge-architecture.md](knowledge-architecture.md), a high-confidence
  `mem:Tactic` outranks a doctrine heuristic as it accrues `mem:Outcome` evidence.

**Precedence** (extends the chain in
[knowledge-architecture.md](knowledge-architecture.md) §"Retrieval + guardrail flow"):

```text
engine legality (action_space)     ← hard gate, engine authoritative
  > Hank deny-policies             ← guardrail
  > canonical datalinks (smac:)    ← facts
  > engine-observed (Hank-promoted)
  > house-rule
  > strat: doctrine                ← curated advice (this plane)
  > mem: learned tactic (low conf)
```

Doctrine is **advisory**, below every factual/legality tier — it can only steer among
*already-legal, already-grounded* options. And it is **provisional**: once a
`mem:Tactic` crosses a confidence threshold it is promoted above the doctrine it
contradicts (recorded with `strat:overriddenBy → mem:Tactic`), so play that has been
*proven* in our games wins over a generic guide. Doctrine seeds; memory earns its way up.

## Namespace

- **`strat:`** = `<http://neuralamplifier.local/ontology/strat/>` — curated doctrine.

`strat:` is kept distinct from `smac:` (rules) and `mem:` (our experience) for the same
reason those two are split: the namespace **is** the trust signal. A reader must be
able to tell a wiki heuristic from an `alphax.txt` row at a glance. `strat:` facts
*reference* `smac:` component/facility IRIs but never redefine them.

## The `strat:` model

Five classes, each pointing into the existing `smac:` ontology.

### `strat:UnitTemplate` — a recommended prototype

A doctrine-recommended design, expressed as edges into the `smac:` component classes
(`Chassis` / `Reactor` / `Weapon` / `Armor` / `Ability`) plus:

- `strat:role` ∈ `{garrison, rush, recon, former, probe, interceptor, artillery, psi,
  transport, dropTroop}` — what job the unit does.
- `strat:whenToBuild` — a trigger blank node: `strat:requiresTech` (the gating
  `smac:Technology`), `strat:threatLevel` ∈ `{none, low, high}`, `strat:phase` ∈
  `{expansion, infrastructure, specialization, endgame}`.
- `strat:upgradesTo` — the next template in the same role as tech advances.
- `strat:rationale` — one-line human justification (carries the citation feel).

```turtle
@prefix strat: <http://neuralamplifier.local/ontology/strat/> .
@prefix smac:  <http://neuralamplifier.local/ontology/smac/> .

# 1. Early Synthmetal garrison — the standard border defender once armor tech lands.
strat:SynthmetalGarrison
    a strat:UnitTemplate ;
    strat:role        "garrison" ;
    smac:hasChassis   smac:Infantry ;
    smac:hasReactor   smac:FissionPlant ;
    smac:hasArmor     smac:SynthmetalArmor ;      # attack left at 1 → cheap
    smac:hasAbility   smac:Trance ;               # +50% psi, ~free on a no-weapon unit
    strat:whenToBuild [ strat:requiresTech smac:Indust ;
                        strat:threatLevel "low" ; strat:phase "expansion" ] ;
    strat:upgradesTo  strat:PlasmaTranceGarrison ;
    strat:rationale   "Synthmetal + Trance defender deters rovers and roving worms cheaply." ;
    strat:source      "alphacentauri2.info/wiki/Unit ; Fandom Clean Reactor / Trance" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .

# 2. Impact Rover — the classic momentum rush unit (Vel: enough by the early-mid 30s).
strat:ImpactRoverRush
    a strat:UnitTemplate ;
    strat:role        "rush" ;
    smac:hasChassis   smac:Speeder ;              # speed 2, exploits open terrain
    smac:hasReactor   smac:FissionPlant ;
    smac:hasWeapon    smac:Impact ;               # Particle Impactor, attack 4
    strat:whenToBuild [ strat:requiresTech smac:NonLin ;
                        strat:threatLevel "high" ; strat:phase "expansion" ] ;
    strat:rationale   "Raw speed + attack 4 rushes a neighbour sharing your continent." ;
    strat:source      "Vel's SMAX Guide v4.0 (momentum) ; GameFAQs jchamberlin FAQ" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .

# 3. Fast Super-Former — terraforming throughput, kept alive all game.
strat:FastSuperFormer
    a strat:UnitTemplate ;
    strat:role        "former" ;
    smac:hasChassis   smac:Speeder ;              # or Gravship late for flying formers
    smac:hasReactor   smac:FusionReactor ;
    smac:hasWeapon    smac:Formers ;              # terraform "equipment"
    smac:hasAbility   smac:SuperFormer ;          # 2x terraform rate
    smac:hasAbility   smac:CleanReactor ;         # no support cost for a lifetime unit
    strat:whenToBuild [ strat:requiresTech smac:EcoEng2 ;
                        strat:threatLevel "none" ; strat:phase "infrastructure" ] ;
    strat:rationale   "Formers last the whole game; Super doubles output, Clean removes upkeep." ;
    strat:source      "Fandom Clean Reactor (SMAC) ; alphacentauri2 abilities" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .

# 4. Trance Probe defender — probe utility + psi-hardened, guards against enemy probes.
strat:TranceProbeDefender
    a strat:UnitTemplate ;
    strat:role        "probe" ;
    smac:hasChassis   smac:Infantry ;
    smac:hasReactor   smac:FissionPlant ;
    smac:hasWeapon    smac:ProbeTeam ;
    smac:hasAbility   smac:Trance ;               # cheap on a probe; blocks worm/probe threat
    strat:whenToBuild [ strat:requiresTech smac:PlaNets ;
                        strat:threatLevel "low" ; strat:phase "infrastructure" ] ;
    strat:rationale   "Cheap in-base probe deters enemy probe teams and screens mind worms." ;
    strat:source      "CivFanatics probe-defense thread ; alphacentauri2 Unit" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .
```

**Design intuition doctrine encodes (from the cost formula).** SMAC unit cost is
roughly `Weapon × (Armor + Speed) × 10 / 2^(Reactor+1)`, with HP = `Reactor × 10`.
Two consequences doctrine leans on: (1) a unit strong in **all three** of
weapon/armor/speed is far pricier than one strong in **two** — so templates specialise
(a garrison drops its weapon to 1; a rush unit drops armor). (2) A **better reactor
divides the whole cost** while adding a small fixed floor, so state-of-the-art
weapons/armor become *relatively cheaper* once Fusion/Quantum arrives — doctrine says
"redesign your heavy units the turn a new reactor lands," which is why templates carry
`strat:upgradesTo`.

**Worthwhile abilities (doctrine consensus).** `Trance` (+50% psi defense) and
`Clean Reactor` (no support) are the two most cost-effective — Trance is near-free on a
weaponless garrison (cost scales with attack/defense ratio), Clean pays back over the
lifetime of formers and garrisons. Situational picks: `AAA Tracking` (vs air/'copters),
`Comm Jammer` (vs fast movers), `Air Superiority` (interceptors), `Deep Radar`
(sentries), `Empath Song` + `Trance` (psi offense/defense), `Drop Pods` (mobility
strike), `Nerve Gas` (brutal but reputation cost), `Cloaking`/`Fungicide`
(niche). **When to design new vs use predefined:** use the stock `smac:UnitProto` rows
(Scout Patrol, Colony Pod, Formers) until a component or ability materially changes the
unit's job — then prototype. Doctrine tags that trigger via `strat:whenToBuild`.

### `strat:BuildOrder` — an ordered production sequence for a base archetype

- `strat:forRole` ∈ `{HQ, core, border, coastal}` — the base's job.
- `strat:phase` — expansion / infrastructure / specialization.
- `strat:step` — an ordered blank node: `strat:order` (int), `strat:produce`
  (→ `smac:Facility` **or** `smac:UnitProto` **or** a `strat:UnitTemplate`),
  `strat:condition` (optional guard string).

```turtle
# Border base, expansion phase — safety and growth before luxury.
strat:BorderBaseExpansion
    a strat:BuildOrder ;
    strat:forRole "border" ; strat:phase "expansion" ;
    strat:step [ strat:order 1 ; strat:produce strat:SynthmetalGarrison ;
                 strat:condition "no in-base defender" ] ;
    strat:step [ strat:order 2 ; strat:produce smac:RecyclingTanks ] ;
    strat:step [ strat:order 3 ; strat:produce smac:Formers ;
                 strat:condition "faction has < 1 former per 2 bases" ] ;
    strat:step [ strat:order 4 ; strat:produce smac:ColonyPod ;
                 strat:condition "open expansion sites remain" ] ;
    strat:step [ strat:order 5 ; strat:produce smac:PerimeterDefense ;
                 strat:condition "enemy within 8 tiles" ] ;
    strat:source "StrategyWiki Base management ; land-of-kain guide (former→escort→pod)" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .
```

```json
{
  "strat:BuildOrder": "HQCoreInfrastructure",
  "strat:forRole": "HQ",
  "strat:phase": "infrastructure",
  "steps": [
    { "order": 1, "produce": "smac:RecyclingTanks", "why": "fast +1/+1/+1, immediate payback" },
    { "order": 2, "produce": "smac:ChildrensCreche", "why": "gates growth; before Rec Commons on slow bases" },
    { "order": 3, "produce": "smac:RecreationCommons", "condition": "drones >= talents" },
    { "order": 4, "produce": "smac:NetworkNode", "why": "labs bonus; enables Virtual World" },
    { "order": 5, "produce": "smac:EnergyBank", "condition": "energy-focused economy" }
  ],
  "strat:source": "StrategyWiki Facilities ; CivFanatics build-order-by-faction",
  "smac:appliesToEngine": "smac", "smac:ruleTier": "doctrine"
}
```

### `strat:FacilityPriority` — facility weighting by phase / economy state

A ranking rule, not a fixed order: `strat:facility → smac:Facility`, `strat:weight`
(0–1), `strat:whenPhase`, `strat:whenEconomy` (e.g. `growth-limited`, `drone-limited`,
`energy-limited`). Retrieval scores the base's producible facilities by matching weight.

```turtle
strat:CrecheWhenGrowthLimited
    a strat:FacilityPriority ;
    strat:facility smac:ChildrensCreche ; strat:weight 0.9 ;
    strat:whenEconomy "growth-limited" ; strat:whenPhase "infrastructure" ;
    strat:rationale "Creche removes the −growth from police/SE and enables pop booms." ;
    strat:source "StrategyWiki Base management ; Lilura1 Drones" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .

strat:RecCommonsWhenDroneLimited
    a strat:FacilityPriority ;
    strat:facility smac:RecreationCommons ; strat:weight 0.85 ;
    strat:whenEconomy "drone-limited" ;
    strat:rationale "Two extra content citizens; cheaper than running Psych high." ;
    strat:source "StrategyWiki Drones ; Fandom Drone (SMAC)" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .
```

**Drone management doctrine** (what gates growth). Drones exceeding Talents → riots →
production stops. Levers in cost order: (1) `smac:RecreationCommons` /
later psych facilities; (2) **police** — military units in-base under a high
`POLICE` SE rating; (3) `smac:ChildrensCreche` to offset SE growth/efficiency
penalties; (4) worker→Doctor conversion or raising the Psych energy slider (1 Talent
per 2 Psych). Doctrine's rule of thumb: build the facility before you burn energy on
Psych, and never let a base sit in riot.

### `strat:ProjectPriority` — secret-project weighting

`strat:project → smac:SecretProject`, `strat:tier` ∈ `{must-grab, strong, situational,
skip}`, `strat:whenDoctrine` (which opening wants it), `strat:rationale`.

```turtle
strat:WeatherParadigmMustGrab
    a strat:ProjectPriority ;
    strat:project smac:TheWeatherParadigm ; strat:tier "must-grab" ;
    strat:rationale "+50% terraform and free advanced formers actions; grab it or take it by force." ;
    strat:source "GameFAQs Alpha2099 Secret Projects FAQ ; StrategyWiki" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .
```

- **must-grab:** `smac:TheWeatherParadigm` (terraform engine), `smac:TheHumanGenomeProject`
  (+1 Talent/base — cheap drone relief), `smac:TheVirtualWorld` (Network Nodes double as
  Hologram Theaters → drone control + labs), `smac:TheCloningVats` (endless growth, guilt-free
  police/thought control).
- **strong / situational:** `smac:TheHunterSeekerAlgorithm` (probe immunity — vitally strong
  vs the University/probe factions), `smac:TheCommandNexus` (free Command Centers → +morale,
  momentum openings), `smac:TheMerchantExchange` (+1 energy at one base — minor, skip unless
  free). Priority shifts by `strat:whenDoctrine`.

### `strat:Doctrine` — the higher-level opening

Ties a strategy together: `strat:usesBuildOrder`, `strat:usesTemplate`,
`strat:techBeeline` (an ordered list of `smac:Technology`), `strat:prefersSE`
(→ `smac:SocialChoice`), `strat:projectFocus` (→ `strat:ProjectPriority`).

```turtle
strat:MomentumRush
    a strat:Doctrine ;
    strat:summary "Expand fast, spam small production bases, field Impact Rovers, war early." ;
    strat:usesBuildOrder strat:BorderBaseExpansion ;
    strat:usesTemplate   strat:ImpactRoverRush , strat:SynthmetalGarrison ;
    strat:techBeeline ( smac:Ecology smac:IndAuto smac:DocFlex smac:NonLin smac:EcoEng ) ;
    strat:prefersSE   smac:Police ;              # a growth/order tradeoff for war footing
    strat:projectFocus strat:WeatherParadigmMustGrab ;
    strat:source "Vel's SMAX Guide v4.0 (Momentum) ; Wikibooks Understanding the game" ;
    smac:appliesToEngine "smac" ; smac:ruleTier "doctrine" .
```

- **Builder / tall** — few well-developed bases, terraform + infrastructure + tech,
  avoid war; build order front-loads Recycling Tanks → Creche → Network Node → economy
  facilities; SE toward Planned/Green/Knowledge.
- **Momentum / rush** — the template above; techs Ecology → Ind. Automation → Doctrine:
  Flexibility → Nonlinear Math → Ecological Engineering; wins on a shared continent.
- **ICS (Infinite City Sprawl)** — many minimal bases packed tight, each: garrison →
  Recycling Tanks → next Colony Pod; leans on Creche + police for drones, trades quality
  for board control. Doctrine flags ICS as strong under original balance and a prime
  candidate for **engine reconciliation** (below), since Thinker tunes AI expansion.

## Provenance & the honest caveat

Every `strat:` fact carries the same three-predicate discipline the datalinks plane
enforces, plus a citation:

- `strat:source` — the guide/wiki/FAQ the heuristic came from (see Sources).
- `smac:appliesToEngine` ∈ `{smac, thinker, glsmac}` — reused from
  [ontology/smac-ontology.md](ontology/smac-ontology.md); retrieval filters on it.
- `smac:ruleTier "doctrine"` — a **new** tier value, distinct from the datalinks set
  (`canonical`, `house-rule`, `engine-observed`, `aspirational`) and from `mem:`
  learned facts. `doctrine` = *curated/expert*, advisory, human-sourced.

**Honest caveat — balance version.** Community strategy overwhelmingly reflects
**original SMAC/SMACX balance**. It must be *reconciled* with engine deviations:

- **Thinker** retunes AI behaviour, worm rates, and some costs (`house-rule` facts can
  `smac:supersedes` a `canonical` value the doctrine assumes) — a doctrine heuristic may
  be mis-calibrated and should defer to an `engine-observed` fact where one exists.
- **GLSMAC** lacks production, SE, and diplomacy — most `strat:` facts are simply
  **inapplicable** there and must not surface (they are not `appliesToEngine "glsmac"`).
- **SMAC vs SMACX.** SMACX adds chassis/abilities/factions and rebalances; a template
  citing a SMACX-only component is tagged accordingly. Where a source is ambiguous, the
  `strat:source` string names the guide so a human can audit.
- **Learned memory overrides.** Per the precedence chain, a proven `mem:Tactic` outranks
  doctrine; the flip is recorded via `strat:overriddenBy`. Doctrine is a *prior*, not law.
- **Figures & IRIs are provisional.** The cost-formula constants and some ability/facility
  numbers here were gathered from search-result snippets (full-page fetches were blocked at
  research time) and are doctrine-level *approximations*; the `smac:` node IRIs
  (e.g. `smac:NonLin`, `smac:TheWeatherParadigm`) follow the ontology's abbrev convention but
  are illustrative until pinned to the actual `alphax.txt` ingest. Validate both against the
  datalinks plane before they drive play — the `strat:source` string names the guide for audit.

## Retrieval hooks — the two decision points

Doctrine surfaces at the same `/decide` flow as the other planes
([knowledge-architecture.md](knowledge-architecture.md) §"Retrieval + guardrail flow"),
via `quipu_context` (situation → ranked facts) and batched `quipu_query`.

### (1) Unit production / the unit designer

Fetch `strat:UnitTemplate`s whose `strat:whenToBuild` matches the base's producible
tech and the current threat level, then propose a prototype (or a stock `smac:UnitProto`
if no template fires).

```json
{
  "tool": "quipu_query",
  "input": {
    "query": "SELECT ?tmpl ?role ?chassis ?weapon ?armor WHERE { ?tmpl a strat:UnitTemplate ; strat:role ?role ; strat:whenToBuild ?w . ?w strat:requiresTech ?t ; strat:threatLevel ?lvl . VALUES ?t { smac:Indust smac:NonLin } VALUES ?lvl { \"low\" \"high\" } OPTIONAL { ?tmpl smac:hasChassis ?chassis } OPTIONAL { ?tmpl smac:hasWeapon ?weapon } OPTIONAL { ?tmpl smac:hasArmor ?armor } ?tmpl smac:appliesToEngine ?e . FILTER(?e IN (\"smac\",\"thinker\")) }"
  }
}
```

> The query above uses `VALUES` and `FILTER IN` as shorthand; Quipu implements neither, so the
> real query is a `||` disjunction (see [quipu-integration.md](quipu-integration.md)).

The tech/threat filter is scoped to *this turn's* researched techs + threat, mirroring the
action-space-bounded fetch discipline. A firing template becomes a design suggestion the
LLM can accept, tweak, or reject.

### (2) Base production

Fetch the `strat:BuildOrder` + `strat:FacilityPriority` for the base's `strat:forRole`
and current `strat:phase`, then rank the base's legal build options.

```json
{
  "tool": "quipu_context",
  "input": {
    "situation": "border base, expansion phase, drone-limited, enemy Hive within 8 tiles",
    "filters": { "class": ["strat:BuildOrder","strat:FacilityPriority","strat:ProjectPriority"],
                 "appliesToEngine": ["smac","thinker"], "ruleTier": ["doctrine"] }
  }
}
```

### Hank policy-guard enforces doctrine invariants

The gameplay policy-guardrail harness (role (c) in
[knowledge-architecture.md](knowledge-architecture.md)) can promote select doctrine into
**enforced invariants**. A `strat:` heuristic marked `strat:enforceable true` projects to
an `aegis:Policy` (`effect warn`, `tier "game-state"`) evaluated by `hank_guard` over the
proposed orders — e.g. *"a `border` base must not end the turn without an in-base unit
matching a `garrison` `strat:UnitTemplate`."* The guard only **warns/advises** on legal
orders; it never overrides `action_space` legality (the engine stays authoritative), and
a doctrine warning yields to a higher-confidence learned tactic exactly as the precedence
chain dictates.
