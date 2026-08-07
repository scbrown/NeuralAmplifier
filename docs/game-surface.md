# The Game Surface

An inventory of every decision *Alpha Centauri* asks a faction to make, so we can answer one
question precisely: **which parts of the game can our AI actually play?**

Without this, "Claude plays a faction" is unfalsifiable. With it, coverage is a number.

Grounded in the Thinker fork at [`scbrown/thinker`](https://github.com/scbrown/thinker); all
`file:line` citations are `src/...` in that tree unless noted. Companions:
[thinker-adapter-notes.md](thinker-adapter-notes.md) (hooks and slot modes),
[headless-harness.md](headless-harness.md) (dialogs and unattended runs),
[contract.md](contract.md) (the `scope` / `action_space` vocabulary).

---

## 1. Three kinds of coverage

These get conflated, and only the third is measurable:

1. **Surface coverage** — does the adapter handle this decision *at all*, or does it fall
   through to the deterministic tier? Design state; the tables below.
2. **Tier coverage** — deterministic or LLM-routed? Per VISION §4 both are legitimate; what
   matters is that the assignment is *deliberate* rather than accidental.
3. **Exercise coverage** — did a real run actually *hit* this surface? A surface can be fully
   implemented and never fire in any test.

### Surface instrumentation

Give every surface a stable ID (`base.production`, `unit.design`, `faction.tech`,
`diplo.treaty_break`, …) and have each adapter hook emit its ID into the decision log. A
headless run then yields a **coverage report** for free: which surfaces fired, how often, and
which never fired. That turns vague questions into harness assertions:

- "Did this scenario exercise unit design?" → assert `unit.design` count > 0
- "Are we regressing?" → diff surface sets between runs
- "Is a surface dead?" → implemented but never fires; the scenario is wrong or the hook is misplaced

It also makes canned-save design deliberate: a coastal start for naval and sea colonies, a
contact-heavy save for diplomacy, a late-game save for secret projects and transcendence. Each
save targets **named** surfaces instead of being whatever state was lying around.

---

## 2. The turn spine

Decision *ordering* is fixed by `mod_faction_upkeep` (`game.cpp:1557`, installed at
`patch.cpp:511`). Any per-turn LLM policy call has to fit this sequence:

```text
mod_faction_upkeep(faction_id)              game.cpp:1557    [per faction, per turn]
 ├─ plans_upkeep                            :1562   AI strategic plan state (plan.cpp:432)
 ├─ social_upkeep                           :1566   commit pending SE
 ├─ mod_repair_phase                        :1568
 ├─ mod_production_phase                    :1570   → per base: mod_base_upkeep (base.cpp:4045)
 ├─ mod_allocate_energy                     :1572   econ/labs/psych sliders
 ├─ enemy_diplomacy                         :1574   ← AI↔AI diplomacy (opaque engine fn)
 ├─ enemy_strategy                          :1576
 ├─ mod_social_ai                           :1584   SE model choice
 ├─ probe_upkeep / move_upkeep              :1585-6
 ├─ corner-market check                     :1589   AI-only (!is_human)
 ├─ mod_tech_selection                      :1622   research target (when tech_research_id < 0)
 └─ call_council                            :1636   AI-only (!is_human)
```

Per-unit orders fire separately as the engine iterates units (`mod_enemy_turn`
`veh_turn.cpp:4` → `mod_enemy_move` `veh_turn.cpp:137`). So **faction policy** lives in
`mod_faction_upkeep` and **unit drill-down** in `mod_enemy_move` — matching the contract's
`turn` / `unit` / `base` scopes.

---

## 2.5 Instrumentation status — measured 2026-07-29, amended 2026-08-03

**4 of 77 surfaces the brain can actually decide**, plus **1 observed only**. The four apply:
the choice executes, validated against the engine's own availability tests first, so an illegal
order is rejected rather than applied. A surface is not covered until its decision can be applied
— `just surfaces` reports it from the frozen registry rather than from this paragraph.

`econ.energy_sliders` is the observed-only one (na-yd4): the adapter records what
`mod_allocate_energy` chose and every split that was legal, and nothing applies a brain's answer
yet. It is deliberately in `OBSERVED` and not `APPLIED`, because the applied count is what says
how much of the game the brain drives and moving it for an observation would overstate the one
number kept honest.

**The surface count is no longer the whole story.** Since na-8ja an agent can also command any
unit or base *directly* — `move` / `skip` / `build` on the command channel, outside the engine's
ask-and-answer cycle entirely. Those verbs cut across this registry rather than adding to it, and
they reach territory the 25 unit-scope surfaces below were deferred over. See
[turn-scoped-play.md](turn-scoped-play.md); this table counts only what the engine *asks* about.

The registry is frozen at 77 (`orchestrator/surfaces.py`), partitioned by contract scope:
`base` 25, `unit` 32, `turn` 20.

Three further surfaces — `base.governor_config`, `base.abandon` and `base.hq_escape` — are
**instrumented but not brain-decidable**, and are deliberately not in the four. Each has a
deterministic tier in the fork and a probe, and no decide or apply path at all — which is the
intended first step for the 21 `NO_AI_PATH` surfaces (na-2mn): give them a native answer
*before* a model, so there is something to fall back to and something to measure against.
Counting them in the four would report as brain-covered surfaces the brain has never been
asked about.

`base.hq_escape` is worth reading as the honest case: its tier returns the **same answer as
stock**. Affordability is settled upstream, the destination is the engine's, and the HQ's
value scales with the empire, so a decline threshold would have been a fabricated number
wearing the costume of reasoning. A native answer that is already right is still worth naming
and recording — that is what the LLM tier falls back to and gets scored against — but it is
not worth changing.

| Surface | Scope | Seam | Action space | Probe |
| --- | --- | --- | --- | --- |
| `base.production` | base | `mod_base_build` | engine-authoritative, costed in minerals, unit roles + facility effect text (no `Action.effects` — see contract.md) | `observe <base_id>` |
| `faction.tech` | turn | `mod_tech_selection` | `tech_avail`, with the AI's own valuation weights | `observe-tech <faction_id>` |
| `faction.se` | turn | `mod_social_ai` | legal (field, model) pairs with effect deltas | `observe-se <faction_id>` |
| `base.hurry` | base | `mod_base_hurry` (wrapped) | hurry / don't, with credit cost and turns saved; unaffordable option omitted | `observe-hurry <base_id>` |
| `base.governor_config` | base | `governor_priorities` | n/a — deterministic tier only so far; records the resolved weights and their source | `observe-gov <base_id>` |
| `base.abandon` | base | `mod_base_production` (size-1 base, pod ready) | keep / spend the base, with the growth numbers the answer turns on | `observe-abandon <base_id>` |
| `base.hq_escape` | base | `mod_capture_base` | relocate / don't, with the 1000-credit cost, the reserve, and the engine's chosen destination | `observe-hq-escape <base_id>` |

Each of the four has an apply command, and each validates with the engine's own test rather
than with a reconstruction of it:

| Surface | Apply | Legality test | Costs |
| --- | --- | --- | --- |
| `base.production` | `apply <base_id> unit:<n>\|facility:<n>` | `mod_veh_avail` + `can_build_unit` / `mod_facility_avail` + `can_build` | — |
| `faction.tech` | `apply-tech <faction_id> tech:<n>` | `tech_avail` | — |
| `faction.se` | `apply-se <faction_id> se:<field>:<model>\|se:none` | `society_avail` | upheaval, debited via `social_upheaval` |
| `base.hurry` | `apply-hurry <base_id> hurry:now\|hurry:none` | `can_hurry_item` + affordable `hurry_cost` | energy credits, debited via `hurry_item` |

**These are probe verbs, and a probe verb is not an in-game decision hook.** They are driven by
the `na-command` file the window proc polls, so an agent outside the process can observe, decide
and apply without an orchestrator. Applying *during play* is a separate thing, and needs a
`na_decide_*` entry point the engine's own call site actually routes through. All four surfaces
now have both; they were not always the same list, and reading this table as the coverage number
is what made it read 4 when it was 1:

| Surface | Decide entry point | Engine call site |
| --- | --- | --- |
| `base.production` | `na_decide_base_production` | `base.cpp` — return replaces the engine's choice |
| `faction.tech` | `na_decide_faction_tech` | `tech.cpp` — return replaces the engine's choice |
| `faction.se` | `na_decide_faction_se` | `faction.cpp` — in/out params replace the tier's candidate |
| `base.hurry` | `na_decide_base_hurry` | `build.cpp` — replaces the call, and stands down to it |

The bottom two needed the hook *moved* rather than a return assigned, because each spends on
its way out. `faction.se` decides ahead of the apply block: by the end of it the upheaval is
debited and pending is set, and undoing a paid-for change is how a faction gets free social
engineering. `base.hurry` goes further — `mod_base_hurry` decides and spends in one pass, so
there is no point after it at which a different answer can still be given. The decide path runs
first and calls the deterministic tier itself when it stands down.

Two of them spend something, and both debit through the engine's own routine — `hurry_item`
does the credit debit and the mineral credit together, and reimplementing either half is how a
faction gets free production. `base.hurry` is the one that can lose something irreversibly, so
it refuses an unaffordable order with the numbers rather than partially applying it.

`faction.se` takes legality from `society_avail`, which is the engine's test and **not** the
checks the observation's action space makes for itself. Those two are supposed to agree; this is
the one that binds.

**Which surfaces the brain may decide is configuration**, not code: `na.toml` carries a
toggle per surface, and one switched off is recorded at `deterministic` tier — explicitly not
degraded, because the brain was never asked. That is how a surface gets rolled out one step at a
time: instrument it, watch it observe, then let it decide.

All three `NO_AI_PATH` surfaces are at the first of those steps with an extra one in front of it. A
`NO_AI_PATH` surface has a rollout one longer than the others — **native answer, instrument,
observe, then decide** — because the usual first step assumes a deterministic tier already
exists to observe, and on these 21 it does not. Each therefore has a `thinker.ini` option
(`na_governor_policy`, `na_abandon_policy`, `na_hq_escape_policy`; all default off) and no `na.toml` toggle,
there being no brain answer yet to switch on.

It is also the reason that list is worth working through rather than routing straight to the
model: it looked like a permissions checkbox and turned out to be the sole input to every build
decision a player-owned base makes. Had it gone to the brain first there would have been no
baseline to A/B against, because there was no decision to compare with — which is the argument
na-2mn makes for all 21.

**Every new surface ships a side-effect-free probe.** In-game input cannot be driven at all
([headless-harness.md](headless-harness.md) §3.0.2), so a surface that fires every five to ten
turns is otherwise unverifiable without playing until it happens. A probe calls the serialiser
and never the decision function.

### Grounding utilisation scales with how narrow the action space is

Measured with the same model (Haiku) on two surfaces, same graph, same retrieval path:

| Surface | Options | Facts offered | Cited | Utilisation |
| --- | --- | --- | --- | --- |
| `base.production` | 8 | 7 | 1 | **0.14** |
| `base.hurry` | 2 | 1 | 1 | **1.00** |

`base.production` was re-measured later over twenty decisions, once citations were asked to
include facts that helped *rule an option out*: 1.45 of 8 cited, utilisation **0.18**. Higher,
and the conclusion is unchanged — see the `na-373` eval (`just eval score na-373`), which also
records why narrowing the offered set turned out not to be the fix.

The wide surface paid for six facts nobody read. That is not a model failure — retrieval fetched
one fact per offered action, and a decision only turns on a few of them. It is a retrieval-tuning
signal, and it did not exist before citations were instrumented.

Worth recording separately: on `base.hurry` the brain **disagreed with the deterministic tier**,
which declined to hurry, and justified it numerically — "100 of 139 credits to save 15 turns,
preserving 39". That is the value proposition working: not a better rule lookup, a judgement about
whether the reserve is better spent than held. Whether it is *right* is a separate question that
needs outcome measurement over a game, not a single decision.

### What the 72 remaining actually divide into

The gap is not uniform, and the interesting split is not by scope:

Run `just surfaces` for these numbers rather than trusting this paragraph — they come from the
frozen registry (`surfaces.coverage()`), and the prose version of this list was wrong for a
while in a way worth recording. It counted "21 with no AI path" and "32 unit-scope" as separate
piles; seven surfaces are **both**, so the remainder looked like 20 when it is 27. A third more
work was available than the document admitted. The buckets below partition, and a test now
enforces that they do.

- **21 the native AI never decides at all** (`NO_AI_PATH`) — `base.abandon`, `council.vote`,
  `base.retool`, `diplo.base_swap` and the rest. No deterministic tier to fall back to, which
  cuts both ways: an LLM adds capability the engine genuinely lacks, and there is no native
  answer to degrade to when the brain is unavailable. Anything moved here needs its fallback
  designed, not inherited — that is step 0 of the plan, and the one that is expensive to skip.
- **25 `unit`-scope with a native path**, most of which should stay deterministic on volume
  grounds — see the `unit.move` entry in [decision-inputs.md](decision-inputs.md) §5 for why, and
  why a revisit should decide *operations* rather than tile moves.
- **26 base and faction decisions with an existing native path** (was 27; `econ.energy_sliders`
  is now observed), so they can be instrumented incrementally with a safe fallback already in
  place. This is the bucket to work through.

  Two cautions learned instrumenting the first one, both on `na-yd4`. **"Ready" means "has a
  native path and is not unit-scope"**, which reads as "cheap" and is not the same claim:
  `base.specialists` is base-scope and therefore counted ready, while `best_specialist()` fires
  per specialist beyond the 16th, per base, inside a block that runs several times per base-turn.
  And **the seam is most of the per-surface work** — `econ.energy_sliders` needed
  `mod_allocate_energy` read end to end to learn that `SE_alloc_labs` is narrowed four times and
  only the last value is real. A grep sweep across ten surfaces produced nothing usable, because
  "where does the engine decide X" is not a textual question. These 26 are not a batch job.

One known limitation, recorded because it is invisible otherwise: **`faction.se` never fires for
a human-slot faction.** `mod_social_ai` returns immediately for humans, so in the recommended
Mode B+ configuration (human slot, `manage_player_bases`) it covers AI factions only. Routing a
human slot's SE decision needs a different hook, because that choice is made through the UI.

## 3. Coverage matrix

Legend — **AI**: ✅ engine/Thinker path exists · ⚠️ exists but asymmetric · ❌ human-dialog only.
**Tier**: D = deterministic tier suffices · L = LLM should own · D+L = deterministic default,
LLM drills down.

### 3.1 Base & economy — scope `base`

| Surface | AI | Path | Tier |
| --- | :--: | --- | :--: |
| `base.production` | ✅ | `mod_base_build` base.cpp:1145 → `select_build` build.cpp:810 | D+L |
| `base.queue` | ✅ | `base_queue` base.cpp:1276 | D |
| `base.hurry` | ✅ | `mod_base_hurry` build.cpp:40 → `hurry_item` :214 | D+L |
| `base.workers` | ✅ | `mod_base_yield` base.cpp:1834; `base_radius` :1733 | D |
| `base.specialists` | ✅ | `pick_specialist` base.cpp:4577, `best_specialist` :4600 | D |
| `base.psych` | ✅ | `mod_base_psych` base.cpp:2400 | D |
| `base.facility` | ✅ | `select_build` build.cpp:810; `can_build` base.cpp:4701 | D+L |
| `base.project` | ✅ | `find_project` build.cpp:349; `facility_score` plan.cpp:8 | **L** |
| `base.satellite` | ✅ | `find_satellite` build.cpp:284 | D |
| `base.staple` | ⚠️ | `consider_staple` build.cpp:232 — **skipped for player bases** (base.cpp:1152) | L |
| `base.drone_riot` | ✅ | `mod_base_drones` base.cpp:2882 → `mod_drone_riot` :2808 | D |
| `base.growth` | ✅ | `mod_base_growth` base.cpp:2669 (deterministic, no chooser) | D |
| `base.defend_goal` | ✅ | `move_upkeep` move.cpp:1078-1090 | D |
| `base.support` | ✅ | `mod_base_check_support` base.cpp:1530 | D |
| `base.capture` | ✅ | `mod_capture_base` base.cpp:399 | D+L |
| `base.hq_relocate` | ✅ | `find_relocate_base` base.cpp:45 (`conf.auto_relocate_hq`) | D |
| `base.name` | ✅ | `mod_name_base` game.cpp:2075 | D |
| `base.abandon` | ❌ | AI returns early (the `ABANDONBASE` popup is human-only); **deterministic tier added in the fork** (`na_abandon_policy`, base.cpp) | D+L |
| `base.governor_config` | ❌ | `gov_config()` returns `~0u` for AI (engine_base.h:249) — no AI policy exists; **deterministic tier added in the fork** (`na_governor_policy`, plan.cpp) | D+L |
| `base.hq_escape` | ❌ | `X_pop("ESCAPE")` — single-player human only; everyone else gets a literal `true`. **Deterministic tier added in the fork** (`na_hq_escape_policy`, base.cpp); answer unchanged on purpose | D+L |
| `base.disband` | ❌ | `mod_base_kill` base.cpp:224 — no deliberate AI caller | L |
| `base.retool` | ❌ | penalty applies to humans only (base.cpp:1045, build.cpp:11) | L |
| `econ.energy_sliders` | ⚠️ | `mod_allocate_energy` game.cpp:1902 — **returns early for humans** :1908 | **L** |
| `econ.commerce` | ✅ | passive; base.cpp:2142-2184 | — |
| `econ.corner_market` | ⚠️ | game.cpp:1589 — **AI-only** (`!is_human`) | L |

### 3.2 Units, military, terraforming — scope `unit`

| Surface | AI | Path | Tier |
| --- | :--: | --- | :--: |
| `unit.turn_order` | ✅ | `mod_enemy_turn` veh_turn.cpp:4 (10-pass priority loop :6-67) | D |
| `unit.dispatch` | ✅ | `mod_enemy_move` veh_turn.cpp:137; table :164-190 | D+L |
| `unit.move` | ✅ | `action_go_to` veh_action.cpp:449; `TileSearch` path.cpp:5-232 | D |
| `unit.attack` | ✅ | `combat_move` move.cpp:2931; `battle_priority` :211, `best_odds` :3096 | D+L |
| `unit.design` | ⚠️ | `design_units` plan.cpp:103 — **hard-returns for humans** plan.cpp:104 | **L** |
| `unit.upgrade` | ✅ | `mod_upgrade_prototype` veh.cpp:2266; driver plan.cpp:131-193 | D |
| `unit.retire` | ✅ | `retire_proto` plan.cpp:220 (≥60 designs) | D |
| `former.item` | ✅ | `former_move` move.cpp:2047 → `select_item` :1803 | D |
| `former.terraform` | ✅ | `action_terraform` veh_action.cpp:154 | D |
| `colony.found` | ✅ | `colony_move` move.cpp:1405; `base_tile_score` :1340 | **L** |
| `colony.sea` | ✅ | triad branch move.cpp:1422-1424 | L |
| `probe.action` | ⚠️ | `probe` probe.cpp:327; AI block :954-1055 — **sabotage targets hardcoded** :1066 | **L** |
| `transport.move` | ✅ | `trans_move` move.cpp:2448; `invasion_plan` :646 | D+L |
| `air.ops` | ✅ | in `combat_move` move.cpp:2988-3002 | D |
| `air.fuel` | ✅ | veh_action.cpp:3101-3160 (AI always returns home :3132) | D |
| `unit.airdrop` | ✅ | `airdrop_move` move.cpp:2864 | D |
| `unit.artillery` | ✅ | first turn pass veh_turn.cpp:13-17; fire move.cpp:3395-3420 | D |
| `unit.retreat` | ✅ | `escape_move` path.cpp:552 (no human equivalent) | D |
| `crawler.convoy` | ✅ | `crawler_move` move.cpp:1223 → `want_convoy` :1167 | D |
| `native.move` | ✅ | `mod_alien_move` veh_turn.cpp:251; `mod_alien_fauna` :568 | D |
| `unit.monolith` | ✅ | AI auto in `veh_skip` veh.cpp:2668-2672 | D |
| `unit.pod` | ✅ | `mod_goody_box` veh.cpp:1145 | D |
| `unit.artifact` | ✅ | `artifact_move` move.cpp:2204 | D+L |
| `unit.psi_gate` | ✅ | move.cpp:3431-3461, `teleport_score` :131 | D |
| `unit.planet_buster` | ✅ | `nuclear_move` move.cpp:2701 | **L** |
| `unit.odp_attack` | ❌ | `action_sat_attack` basewin.cpp:43 — **no AI caller** | L |
| `unit.tectonic/fungal` | ❌ | `action_tectonic` veh_action.cpp:1452, `action_fungal` :1537 — **no caller at all** | L |
| `unit.patrol` | ❌ | `action_patrol` veh_action.cpp:1300 — human-issued order; `valid_patrol` is only its legality test, not an AI chooser | — |
| `unit.disband` | ❌ | `action_destruct` veh_action.cpp:1174 — no AI caller | L |
| `unit.gift` | ❌ | `action_give` veh_action.cpp:1649 — engine diplomacy only | L |
| `unit.obliterate` | ❌ | `action_oblit` veh_action.cpp:1210 — no Thinker caller | **L** |

### 3.3 Faction level — scope `turn`

| Surface | AI | Path | Tier |
| --- | :--: | --- | :--: |
| `faction.tech` | ✅ | `mod_tech_ai` tech.cpp:613; scoring `mod_tech_val` :366 | **L** |
| `faction.tech_steal` | ✅ | `steal_tech` faction.cpp:744 | L |
| `faction.se` | ⚠️ | `mod_social_ai` faction.cpp:1458 — **hard-returns for humans** :1463 | **L** |
| `faction.agenda` | ✅ | `mod_setup_player` faction.cpp:1798 | — |
| `diplo.declare_war` | ✅ | `mod_wants_to_attack` faction.cpp:1731 → `evaluate_attack` :1548 | **L** |
| `diplo.treaty_break` | ✅ | `break_treaty` faction.cpp:534 (popups :546-558 return-value gated) | L |
| `diplo.atrocity` | ✅ | `atrocity` faction.cpp:398, `major_atrocity` :487 | L |
| `diplo.ai_to_ai` | ⚠️ | `enemy_diplomacy` engine.cpp:808 — **opaque engine fn, never overridden** | observe only |
| `diplo.tech_trade` | ❌ | `mod_buy_tech` gui_dialog.cpp:481 — asserts `!is_human(faction2)`; **human-initiated only** | **L** |
| `diplo.energy_loan` | ❌ | `mod_energy_trade` gui_dialog.cpp:352-397 — same assert :77 | L |
| `diplo.base_swap` | ❌ | `mod_base_swap` gui_dialog.cpp:207 — same assert | L |
| `diplo.treaty_offer` | ❌ | `propose_pact`/`propose_treaty` engine.cpp:748-749 — raw ptrs, no override | **L** |
| `diplo.surrender` | ❌ | no AI function; Thinker only throttles the dialog base.cpp:950-970 | L |
| `diplo.tribute` | ❌ | `demand_withdrawal` engine.cpp:755 etc. — no Thinker logic | L |
| `diplo.map_trade` | ❌ | `trade_maps` engine.cpp:747 — pure engine | L |
| `council.call` | ⚠️ | `call_council` game.cpp:1636 — **AI-only**; human gets only "COUNCILOPEN" :1633 | L |
| `council.vote` | ❌ | `council_get_vote` engine.cpp:683 — **zero call sites anywhere in Thinker** | **L** |
| `council.buy_vote` | ❌ | `buy_council_vote` engine.cpp:743 — decision opaque | L |
| `victory.diplomatic` | ✅ | `aah_ooga` faction.cpp:888; `at_climax` :944 (false for humans) | L |
| `victory.conquest` | ✅ | `end_of_game` game.cpp:1222 | L |

### 3.4 Interrupts & events

Random events, warming, volcanoes, pods and native life execute **identically for AI and human**
— only the `POP2`/`popp` call is gated on `is_player`/`is_visible` (`game.cpp:255` picks a base
across all factions). Two exceptions are genuine mechanical asymmetries, not presentation:
global warming accumulation (`base.cpp:3205`) and colony-pod base disbanding (`base.cpp:3325`).

Full popup catalogue and the blocking-dialog inventory live in
[headless-harness.md](headless-harness.md) §4.

---

## 4. The gap list

**Surfaces with no AI path.** These are invisible to a faction on an AI slot and have no engine
heuristic to copy — so the LLM tier must own them outright, or they are fork work. Ordered by
how much they cost us:

1. **`unit.design` — the Unit Workshop.** `design_units` hard-returns for humans
   (`plan.cpp:104`). In Mode B/B+ *nothing* designs our units. Biggest single gap, and one of
   the two decisions VISION explicitly wants the knowledge layer to sharpen.
2. **`council.vote`.** `council_get_vote` (`engine.cpp:683`) has **zero call sites** in the
   entire fork — verified by grep. The whole Planetary Council voting decision is opaque.
3. **`base.governor_config`.** 21 permission bits per base, `~0u` for AI (`engine_base.h:248`).
   No AI policy exists because the AI never needs one.
4. **The AI-negotiation tree is one-directional.** `mod_threaten`, `mod_base_swap`,
   `mod_energy_trade`, `mod_buy_tech` all assert `!is_human(faction2)` (`gui_dialog.cpp:77`) —
   they only run with a *human* as the proposer. There is **no AI-initiated** buy-tech, loan, or
   base purchase. Claude proposing a deal is net-new.
5. **`diplo.treaty_offer` / `surrender` / `tribute` / `map_trade`.** All raw engine pointers with
   no Thinker override and no `is_human` branch anywhere.
6. **`econ.energy_sliders` and `faction.se` for a human faction.** Both early-return
   (`game.cpp:1908`, `faction.cpp:1463`). The AI heuristics exist and are portable — the cheapest
   fixes on this list.
7. **`unit.tectonic` / `unit.fungal`.** No caller anywhere; AI factions never fire these.
8. **`unit.odp_attack`, `unit.obliterate`, `unit.gift`, `unit.disband`, `base.abandon`,
   `base.hq_escape`, `base.retool`.** Human-dialog-only decisions with real stakes.
9. **`probe.action` sub-menus.** The human gets ~15 dialogs; the AI collapses to one branch chain
   with a **hardcoded** sabotage target list (`probe.cpp:1066`).

### 4.1 `unit.patrol` is not a missing decision tier

`unit.patrol` remains in the frozen registry for log compatibility, but it is not work for the
deterministic-tier expansion. `action_patrol` is an imperative order issued with an already-chosen
unit and waypoint. Its only caller of `valid_patrol` asks whether that supplied waypoint is legal;
neither function chooses whether to patrol or where to go. Hank's call graph confirms the complete
local chain is `action_patrol -> valid_patrol`, with no Thinker policy caller.

Adding a default-off `na_patrol_policy` at either function would therefore put a policy gate around
a human command or its validator, not give an AI faction a native answer. The actual deterministic
unit policy already lives in `mod_enemy_move` and uses `NODE_PATROL` / `NODE_COMBAT_PATROL` goals;
an LLM-level patrol operation belongs above that movement policy (or on the direct command channel),
not at this UI action. Treat this entry as a retained registry marker, not as one of the surfaces
that needs a fallback manufactured under na-2mn.

---

## 5. Rule asymmetries (the fairness ledger)

Not UI differences — **different rules**. This is why a Mode A result is not a fair result
(see [thinker-adapter-notes.md](thinker-adapter-notes.md) §5.0).

> **Decided: we record, we do not neutralise.** Every active asymmetry is declared in the world
> view's `fairness` block ([contract.md](contract.md)) and carried on the decision record
> ([observability.md](observability.md) §2), rather than patched out of the binary.

Three reasons this is the right call, and one cost to be honest about:

- **Patching is disproportionately risky here.** Thinker's hook primitives verify original bytes
  and abort on mismatch (`patch.cpp:218,250`). Neutralising fourteen branches means fourteen more
  binary-compat surfaces that break on any upstream Thinker change — for no gameplay capability.
- **Disclosure is what the claim actually needs.** The problem was never that handicaps exist;
  it was that they were *invisible*, so a result couldn't be interpreted. Recorded, it can.
- **It turns a bug into an experimental variable.** With the profile on every record, Mode A and
  Mode B+ runs become directly comparable, and "does Claude still win with the handicaps off?"
  becomes a query rather than a rebuild.
- **The cost:** recording does **not** make a Mode A run fair — it makes it *interpretable*. A
  win under an active profile is "won as Gaians on Transcend with six declared advantages," and
  must be reported that way. For an unqualified fair-play claim, use Mode B+, where the profile
  is empty because human rules apply.

Because the profile is in the world view, **Claude can see its own handicaps** and reason about
them. That is deliberate: it is more honest than hiding them, and a model that knows tech is
cheap for it should say so out loud.

### 5.1 Two categories, and only one is a user choice

Roughly half the ledger **is** the difficulty setting — that is what difficulty *means* in SMAC,
and a player who selects Transcend is deliberately asking for a stronger opponent. When Claude is
the computer opponent (VISION's autonomous mode), inheriting those is correct product behaviour,
not cheating.

The rest are **structural**: `is_human` branches nobody selected, so they can't be justified by
the difficulty argument. The `fairness` block carries `selected_by` per entry so the two never
get conflated in a result. A few structural entries are still difficulty-*gated* — global warming
in particular — but the gate is not what a player is choosing when they pick a difficulty, so
they stay on this side of the line.

**Difficulty-selected** — scale with `*DiffLevel`; a user choice:

| Asymmetry | Where | In force when | Favours |
| --- | --- | --- | --- |
| Unit support bonus | base.cpp:1645 (`unit_support_bonus[*DiffLevel]`) | configured non-zero — **never, by default** | AI |
| Facility maintenance discount | game.cpp:1846-1859 (`*DiffLevel >= DIFF_THINKER`) | Thinker+ | AI |
| Tech cost factor | tech.cpp:1155 (`tech_cost_factor[*DiffLevel]`) | any level except Librarian | **human** below Librarian, AI above |
| Terraform speed | veh_action.cpp:210 (difficulty > 3) | Thinker+ | AI |
| Mind-control cost | probe.cpp:713 (`tgt->diff_level > 3`) | Thinker+ | AI |
| Combat modifiers | veh_combat.cpp:1557-1565 | below Talent | **human** |

**Structural** — flat `is_human` or separate config; present at every difficulty:

| Asymmetry | Where | In force when | Favours |
| --- | --- | --- | --- |
| **Retool penalty — AI pays none, ever** | base.cpp:1045, build.cpp:11 | `retool_penalty_prod_change != 0` | AI |
| **Global warming — AI exempt below difficulty 4** | base.cpp:3205 | below Thinker | AI |
| Mineral carry-over cap | base.cpp:3382, 3655 | always | AI |
| Eco-damage rollback | base.cpp:3118-3124 | always | AI |
| Former automation restrictions | move.cpp:1533-1988 | always | AI |
| Content population | base.cpp:4220 (`content_pop_player` vs `_computer`) | any level except Librarian | **human** below Librarian, AI above |
| Starting units | faction.cpp:1759-1760, 2234-2235 | always | config |
| Colony-pod base disbanding | base.cpp:3325 (AI returns early) | Talent+ | AI |
| Project race-blocking | base.cpp:3639-3652 | always | **human** |

Three of these favour the *human* — the ledger is not uniformly tilted, which is another reason
to record rather than hand-wave.

> **This table is an index, not the source of truth.** Building
> [`fairness.py`](https://github.com/scbrown/NeuralAmplifier/blob/main/orchestrator/src/neural_amplifier/fairness.py) against the fork showed a
> static `favours` column cannot be right: `tech_cost_factor` is `{124,116,108,100,84,76}`
> (`main.h:327`), so the AI pays *more* below Thinker and the entry flips sides at Librarian
> where the factor is exactly 100. `content_pop` flips at the same level for the same reason
> (`{6,5,4,3,2,1}` vs a flat `{3,3,3,3,3,3}`). The combat modifiers were listed as an AI
> advantage but scale a low-difficulty *human's* offense up and an AI attacker's down. And
> `unit_support_bonus` ships all-zero, so declaring it would claim a handicap nobody has.
>
> The orchestrator therefore **derives** the block from `(slot, difficulty, config)` rather than
> reading a table, `handicap_drift()` flags an adapter whose stamp disagrees, and each entry
> carries the `file:line` that justifies it. Correct this table from the module, not the reverse.

---

## 6. Config knobs that move ownership

Which side owns a decision is partly configurable (`struct Config`, `main.h:205+`):

| Option | Line | Effect |
| --- | --- | --- |
| `manage_player_bases` | main.h:225 | Thinker drives **human** bases (base.cpp:991, build.cpp:55) |
| `manage_player_units` | main.h:225 | Thinker drives **human** units set to automated (move.cpp:2074) |
| `factions_enabled` | thinker.ini:90 | Which factions get Thinker's AI at all |
| `base_hurry` | main.h:233 | 0 = no auto-hurry, 1 = units/facilities, 2 = also projects |
| `social_ai` / `social_ai_bias` | main.h:230-231 | SE model choice (AI factions only) |
| `design_units` | main.h:62 | Enables `design_units` (AI factions only) |
| `skip_gov_facility` | main.h:378 | Per-facility blacklist, **player governor only** |
| `warn_on_former_replace` | main.h:224 | `0` auto-answers the "MIMIMI" dialog — the only true auto-answer stub |
| `foreign_treaty_popup` | main.h:228 | Surfaces AI↔AI treaty changes (headless-harness.md §4.2) |

`manage_player_bases` / `manage_player_units` are the two that matter most — they are what make
**Mode B+** (fair rules *plus* a deterministic tier) possible.

---

## 7. Open questions

1. **Surface ID scheme.** The IDs above are provisional. They should be frozen before the first
   hook emits one, since the coverage report keys on them — see
   [observability.md](observability.md) §2 and §9.1.
2. **Where the coverage report is asserted.** A per-run JSON artifact is easy; deciding *which*
   surfaces a given canned save must exercise is the real design work.
3. ~~**Neutralising the fairness ledger.**~~ **Resolved — record, don't neutralise** (§5). What
   remains open is mechanical: the engine exposes `*DiffLevel` and the `conf.*` values, but the
   adapter has to map those to a concrete active-handicap list per faction per game. Some entries
   (retool penalty, global warming) are boolean on `is_human`; others scale with difficulty.
4. **`enemy_diplomacy` is a black box.** AI↔AI pact and vendetta decisions are unobservable from
   source. We can see outcomes (`mod_NetMsg_pop`) but not reasoning. Accept, or reimplement?
5. **Council coverage requires fork work.** With `council_get_vote` uncalled anywhere, giving
   Claude a vote means writing that path, not intercepting it.
6. **Completeness.** This map is derived from the Thinker fork. Decisions that live purely in
   `terranx.exe` with no Thinker reference (much of the diplomacy dialog tree) are listed as
   gaps, but the list of *those* is necessarily less certain than the rest.
