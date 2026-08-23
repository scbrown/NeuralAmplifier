# Alpha Centauri game-knowledge reference

Read this before diagnosing a failed order or changing an early-game plan. It separates
rules from implementation details and strategy: **canonical** means the shipped rules data,
**Thinker** means the engine fork we run, and **heuristic** means a plan to test rather than a
game law.

## Source key and confidence

| Key | Source | What it can establish |
|---|---|---|
| A | Thinker's copy of [`docs/alphax.txt`](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt) | Canonical/house-rule data rows: costs, prerequisites, yields and combat modifiers. Line references below use that revision. |
| T | Thinker source and configuration at [`5b60ed2`](https://github.com/scbrown/thinker/tree/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49) | What our engine computes. This can differ from stock SMAC. |
| W | [Alpha Centauri 2 wiki](https://alphacentauri2.info/wiki/Main_Page) | Community-maintained mechanics; useful where `alphax.txt` names a rule but does not encode the UI/algorithm. |
| C | Community strategy discussions | Practitioner heuristics. These are explicitly labelled and are not treated as engine facts. |

`alphax.txt` in Thinker identifies itself as the Thinker expansion rules file. NeuralAmplifier
therefore tags its generated graph `house-rule`, not stock canonical; see
[`datalinks/thinker/README.md`](../datalinks/thinker/README.md). Check the active game's file
and `thinker.ini` before assuming a numeric value applies to a particular run.

## Mechanics that commonly invalidate a plan

### Movement

- The internal movement scale is three road movement points per ordinary movement point
  (`move_rate_roads = 3`). A connected road-to-road or river-to-river step costs one internal
  point; an ordinary clear-tile step costs three. Rivers only receive that discount when the
  two adjacent land tiles are connected along the river. [A:30](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L30),
  [T:map.cpp:1202-1236](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/map.cpp#L1202-L1236)
- Entering rocky terrain or forest adds another full ordinary movement point. Entering land
  fungus with a conventional unit and non-positive PLANET adds two ordinary movement points,
  making the nominal cost three; native units and the Xenoempathy Dome use road cost instead.
  [T:map.cpp:1227-1255](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/map.cpp#L1227-L1255)
- Stock fungus entry may fail because of native life without spending movement. That is a
  different outcome from “the move cost exceeded the remaining allowance”; do not eliminate a
  destination merely because one attempt did not enter it. [W: Wild Natives and Fungus](https://alphacentauri2.info/wiki/Wild_Natives_and_Fungus.html)
- The rules name Centauri Psi as the technology that eases fungus movement and Centauri Empathy
  as the prerequisite for roads in fungus. Thinker's default `fast_fungus_movement=1` caps the
  conventional-unit cost at the unit's full speed, specifically to avoid stock's random waiting.
  Thus a stock guide and our current engine can legitimately disagree. [A:79-81](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L79-L81),
  [T:thinker.ini:599-607](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/thinker.ini#L599-L607)
- A conventional land unit cannot move directly from one enemy zone of control to another
  when the destination is otherwise empty. Probe units and cloaked units are exempt; pact
  partners do not impose hostile ZOC. [T:veh_action.cpp:2119-2127](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/veh_action.cpp#L2119-L2127),
  [W: Diplomacy—Pact](https://alphacentauri2.info/wiki/Diplomacy.html#Pact)

**Decision rule:** before diagnosing an unavailable move, classify it in this order:
off-map/triad, occupancy, ZOC, then movement cost, then fungus-entry randomness. Record the
active `fast_fungus_movement` value with the observation.

### Terrain economics and former time

- Every citizen consumes two nutrients. The base tile supplies 2 nutrients, 1 mineral and
  1 energy before other effects; forest supplies 1/2/1; a borehole supplies 0/6/6; a monolith
  supplies 2/2/2. [A:31](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L31),
  [A:151-160](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L151-L160)
- Early terraforming has sharply different opportunity costs: farm, solar and forest take four
  former-turns; a road takes one; mine and soil enricher take eight; condenser twelve; borehole
  twenty-four. A road is therefore valuable both as transport and as a cheap way to keep the
  former productive while connecting the next base site. [A:105-134](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L105-L134)
- A mine without a road is subject to the configured mineral-increase limit, and mining removes
  one nutrient under these rules. Do not recommend a mine from mineral yield alone; include its
  road and food balance. [A:38-39](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L38-L39)
- Thinker's production AI models one local former for bases below population 4, and up to two
  for larger bases (with an additional high-population priority). It also counts a former in an
  adjacent tile as half a local former. This is implementation evidence for a coverage target,
  not proof that every position wants the same ratio. [T:build.cpp:1143-1161](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L1143-L1161)

**Decision rule:** keep `former_count / base_count >= 1.0` through initial expansion unless the
world view shows fewer than four actionable unimproved tiles around the affected bases. Count a
shared adjacent former as 0.5 for planning, matching Thinker's locality model. This threshold is
a heuristic grounded in Thinker and in community practice, not a canonical rule.
[C: “How many Formers per Base?”](https://www.reddit.com/r/alphacentauri/comments/1dhs62v/)

### Expansion and population

- A colony module has chassis cost 8 and a former module cost 6; the global mineral multiplier
  is 10, yielding the familiar 80-mineral colony pod and 60-mineral former before faction and
  difficulty modifiers. [A:35-36](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L35-L36),
  [A:425-426](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L425-L426)
- Thinker will consider pods only where the base has population above 1 or positive nutrient
  surplus, keeps fewer than two pods homed at one base, and scores expansion especially strongly
  below 16 faction bases. [T:build.cpp:901](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L901),
  [T:build.cpp:1199-1204](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L1199-L1204)
- The shipped Thinker configuration uses minimum base spacing 3 and permits at most three nearby
  bases at that minimum distance. AI `expansion_limit=50` is a ceiling, not a target; autoscaling
  raises it to match a human with more bases. [T:thinker.ini:532-546](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/thinker.ini#L532-L546),
  [T:faction.cpp:208-231](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/faction.cpp#L208-L231)
- Community consensus favors tight, repeated early expansion because each new base immediately
  works its base tile plus a citizen tile; common sequences put a garrison and former before or
  around a size-2 colony pod. Exact order depends on native threat, faction support and available
  nutrient tiles. [C: expansion discussion](https://www.reddit.com/r/alphacentauri/comments/fm0lyw/),
  [C: early build-order discussion](https://apolyton.net/forum/other-games/alpha-centauri/157340-early-terraforming-build-order-questions)

There is currently **no cited, controlled Talent run establishing a universal turn when an AI
“typically” reaches five or ten bases**. Map size, pod scattering, faction, start, and Thinker's
autoscale all change the answer. Treat any uncited timestamp as a hypothesis. For M2, use the
following provisional evaluation gates and replace them with medians after at least five seeded
Talent baselines:

| Metric-vocabulary gate | Provisional plan target | Interpretation |
|---|---:|---|
| `base_count` | >= 5 by turn 25 | Detect a stalled opening, not establish a game law. |
| `base_count` | >= 10 by turn 50 | Detect prolonged under-expansion; condition on legal sites. |
| `former_count / base_count` | >= 1.0 | Maintain improvement coverage during the pod wave. |
| active `colony_pod_count` | 1–2 faction-wide until 10 bases | Preserve cadence without parking excessive population/minerals in transit. |

These targets synthesize Thinker's strong sub-16-base expansion weighting and community advice;
they are deliberately marked **heuristic**. [T:build.cpp:1199-1204](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L1199-L1204),
[C: early-expansion discussion](https://www.reddit.com/r/alphacentauri/comments/1v5bypx/early_build_order/)

### Combat and reserves

- Bases have an intrinsic 25% defense bonus. Mobile units receive 25% in open ground; infantry
  attacking a base receives 25%; a friendly sensor adds 25%; noncombat defenders suffer 50%.
  Psi uses configured offense:defense ratios rather than conventional weapon-versus-armor
  strength: 3:2 on land, 1:1 at sea and in air. [A:47-71](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L47-L71)
- Thinker's production logic immediately returns a land defender when a base has minerals, is
  allowed to build combat units, and has no effective nearby defender. This supports a minimum
  floor of one credible defender per exposed base, while interior bases can share a mobile
  reserve. [T:build.cpp:1110-1123](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L1110-L1123)
- Thinker's hurry logic refuses a purchase that would breach a reserve computed from turn,
  faction base count, contact, threat, project status and HQ. A single fixed credit number is
  therefore less faithful than a reserve floor expressed against current scale and threat.
  [T:build.cpp:114-126](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L114-L126)

**Decision rules:** keep `defender_count >= 1` at each frontier base and at any base within a
known one-turn enemy reach. Keep `energy_reserves >= max(20, 2 * base_count)` while peaceful and
raise the floor to `max(40, 4 * base_count)` after hostile contact. Those numeric floors are
conservative plan defaults, not engine constants; log overrides with the immediate threat or
irreversible purchase they fund.

### Diplomacy and Planetary Council

- Treaty and pact relationships create commerce; a pact produces twice the treaty commerce,
  shares map and infiltrator information, permits movement and stacking in partner territory,
  and removes partner ZOC restrictions. Either party can dissolve it, so dependent moves must
  not assume it persists beyond the current observed state. [W: Diplomacy](https://alphacentauri2.info/wiki/Diplomacy.html)
- Once one faction knows every surviving commlink it may convene the Council, and the first
  session gives every faction all commlinks. The rules file sets a 20-turn minimum between
  councils; the community reference records the governor's shorter call privilege and veto,
  plus one extra commerce point per base/partner and infiltration of every faction.
  [A:75](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L75),
  [W: Planetary Council](https://alphacentauri2.info/wiki/Diplomacy.html#Planetary_Council)
- Votes can be negotiated with energy or technology. The Global Trade Pact doubles commerce;
  the diplomatic victory vote requires 75% and Mind/Machine Interface. [W: Diplomacy](https://alphacentauri2.info/wiki/Diplomacy.html),
  [W: Supreme Leader](https://alphacentauri2.info/wiki/Supreme_Leader.html)

**Decision rule:** value a treaty as income and a pact as income + information + mobility. Before
trading away a technology, compare it with the value of the vote/commlink gained and whether it
unlocks a Council proposal. Do not spend credits on a vote until the proposal and likely voting
bloc are recorded.

## Competent early-game plan template

Apply this as a directive skeleton, then condition it on the observed map:

1. Establish a legal first base promptly. Prioritize Centauri Ecology if formers are unavailable;
   the former unlock is the prerequisite for the development loop. [A:426](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/docs/alphax.txt#L426)
2. Maintain `defender_count >= 1` for exposed bases and `former_count / base_count >= 1.0`.
   Improve the food tile needed to regrow from each pod, then road toward the next legal site.
3. At population 2 with positive nutrient surplus, put one productive base on a colony pod.
   Keep `active_colony_pod_count <= 2` until settlement transit is reliable; do not queue pods
   that would destroy a size-1 base. Thinker's own eligibility predicate encodes the population/
   surplus guard. [T:build.cpp:901](https://github.com/scbrown/thinker/blob/5b60ed2b32a2a9fd6a499849c06d8dc8aa480a49/src/build.cpp#L901)
4. Prefer sites connected by roads/rivers and capable of working a >=2-nutrient tile. Avoid
   sending the only pod through fungus unless its failed-entry and full-allowance outcomes are
   acceptable.
5. Evaluate `base_count >= 5` at turn 25 and `>= 10` at turn 50 as provisional regression gates.
   If missed, name the binding constraint—no legal site, nutrient recovery, former coverage,
   threat, or transit—rather than blindly increasing pod priority.
6. Preserve the peaceful/hostile `energy_reserves` floors above. Hurry only when the purchase
   does not cross the applicable floor or when an explicit threat override is logged.
7. On contact, pursue treaty commerce unless border pressure makes the withdrawal obligation
   unacceptable. Track commlinks and Council eligibility as strategic resources, not flavor.

## Experiment contract

For every strategy run record: map seed and size, faction, difficulty, `thinker.ini` hash,
`base_count` by turn, colony pods active, former count, defenders by base, energy reserves,
contacts, and legal-site count. A five-run median and range may replace the provisional 5/10-base
targets; a single successful run may not. When an order fails, consult the relevant mechanic
above before changing the policy—the engine result is evidence about one branch, not permission
to rediscover the entire ruleset by elimination.
