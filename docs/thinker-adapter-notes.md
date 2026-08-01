# Thinker Adapter Notes

Source-grounded reference for the **Thinker adapter** (Track A / near-term). Verified against
the fork at `scbrown/thinker` (paths are `src/...`; `file:line` cited). For the overall
dual-engine design and shared contract, see [VISION.md](https://github.com/scbrown/NeuralAmplifier/blob/main/VISION.md) and [contract.md](contract.md).

> **Why this route:** Thinker patches the original, **complete** *Alpha Centauri* — production,
> full tech tree, social engineering, diplomacy, combat, secret projects, real fog-of-war —
> and its AI is already carved into clean per-decision functions. So Claude can drive a faction
> in a deep, balanced game *now*, by intercepting those functions.

## License

**MIT** (`License.md`: "Copyright (c) Thinker Mod authors"). We can adapt Thinker code with
attribution; our adapter is a fork of / addition to Thinker.

## 1. How Thinker patches the game

- Ships as **`thinker.dll`** injected into 32-bit `terranx.exe` (v2.0 GOG). `DllMain`
  → `DLL_PROCESS_ATTACH` parses `thinker.ini` then calls **`patch_setup(&conf)`**
  (`main.cpp:446-466`), which installs every hook. `ThinkerModule()` (`main.cpp:442`) is an
  export stub the patched import table points at.
- **Two hook primitives** (`patch.cpp`, declared `patch.h:15-23`), both verify the original
  bytes and abort on mismatch (binary-compat guard):
  - `write_jump(addr, func)` (`patch.cpp:218`) — overwrite a function prologue with a jump →
    **replace a whole engine function**. The ~200-entry table starts at `patch.cpp:461`.
  - `write_call(addr, func)` (`patch.cpp:250`) — rewrite one call-site → **intercept a single
    call**.
- **Loading:** the `thinker.exe` launcher does remote-thread injection into a suspended
  `terranx.exe` (`launch.cpp:46-93`: `CreateProcess(SUSPENDED)` → `WriteProcessMemory` →
  `CreateRemoteThread(LoadLibraryA)` → `ResumeThread`), or an import-table hex patch auto-loads
  it. `terranx.exe` on disk is never modified — all patches are applied in memory at attach.
- **To add/override a decision:** write a `__cdecl`/`__thiscall` replacement matching the
  original's signature, then add one `write_jump`/`write_call` line to `patch_setup`. Read
  engine globals via the `extern` pointers in `engine.h`.

## 2. The LLM injection seams

Each of these is a decision function returning a value — the natural place to substitute
Claude's choice. Map them directly to the [contract](contract.md)'s `scope`:

| Contract scope | Thinker hook | Returns | file:line | Cleanliness |
| --- | --- | --- | --- | --- |
| **turn** (whole faction) | `mod_faction_upkeep(faction_id)` | — (runs the faction's whole turn) | `game.cpp:1557` (hook `patch.cpp:511`) | ★ cleanest handoff |
| **base** (production) | `mod_base_build(base_id, has_gov)` | build item id (facility / unit / special) | `base.cpp:1145` (picker `select_build` `build.cpp:810`) | ★ clean, self-contained |
| **unit** (orders) | `mod_enemy_move(veh_id)` | order/sync code | `veh_turn.cpp:137` (hook `patch.cpp:669`) | ★ clean (dispatches by type) |
| research | `mod_tech_ai(faction_id)` | tech id | `tech.cpp:613` (hook `patch.cpp:807`) | clean |
| social engineering | `mod_social_ai(faction_id, …)` | sets SE model | `faction.cpp:1458` | clean |
| unit design | `design_units(faction_id)` | — | `plan.cpp:103` | clean per-faction |
| strategy pass | `plans_upkeep(faction_id)` | — (builds `AIPlans`) | `plan.cpp:432` | faction-level |
| diplomacy | `enemy_diplomacy(faction_id)` + many `net_*`/`mod_tech_val` | — | `game.cpp:1574`, `patch.cpp:514-545` | diffuse |

**The per-faction switch (where "LLM controls faction N" plugs in):**
`thinker_enabled(faction_id)` / `thinker_move_upkeep(faction_id)` (`faction.cpp:146`) gate
whether Thinker's AI (vs. legacy) runs, driven by `conf.factions_enabled` etc. Our adapter adds
a parallel gate: for LLM-routed factions, a hook serializes state + action space, asks the
orchestrator, and applies the result; otherwise it falls through to Thinker's native decision
(that fall-through **is** our deterministic tier).

## 3. Reading state → the world view

Engine globals (`extern` in `engine.h:474-481`): `Factions`, `MFactions`, `Bases`, `Units`,
`Vehs`, `MapTiles`; counts `*BaseCount` (`engine.h:300`), `*VehCount` (`:301`), `*CurrentTurn`
(`:315`); tile accessor `mapsq(x,y)` (`map.h:14`). Thinker's own per-faction strategy lives in
`AIPlans plans[]` (`main.h:359`).

Key structs (packed):

| Struct | World-view fields | file:line |
| -------- | ------------------- | ----------- |
| `MAP` (tile) | `climate`, `contour`, `owner`, `items`/`landmarks` bitmasks, `region`, **`visibility`** (per-faction) | `engine_types.h:6` |
| `VEH` (unit) | `x,y`, `unit_id`, `faction_id`, `state`, `order`, `morale`, `moves_spent`, `damage_taken`, `home_base_id`; helpers `triad()`, `is_colony()`, `is_former()` | `engine_veh.h:482` |
| `BASE` | `x,y`, `faction_id`, `pop_size`, `name`, `queue_items[10]`, `facilities_built[12]`, worked tiles, `nutrient/mineral/energy_intake/_surplus` | `engine_base.h:148` |
| `Faction` | `energy_credits`, `SE_Politics/Economics/Values/Future`, `tech_research_id`, `tech_achieved`, `labs_total`, `diplo_status[8]`, `base_count` | `engine_types.h:293` |

**Fog-of-war is real and per-faction** — filter the world view to what a faction can see:
`MAP::visibility` bitfield → `is_visible(faction_id)` / `is_known(faction_id)`
(`engine_types.h:47-52`); units also carry `VEH::visibility` (`engine_veh.h:19`). So the Thinker
world view is **fair from day one** (`contract` `map.fog: true`) — unlike GLSMAC today.

**Enumeration:** `for i in 0..*BaseCount: Bases[i]` (filter `faction_id`); same for `Vehs[]`;
tiles via `mapsq(x,y)` gated on `is_known(faction_id)`. Economy/tech/SE read off `Factions[fid]`.

## 4. The per-faction AI turn (the handoff pipeline)

`mod_faction_upkeep(faction_id)` (`game.cpp:1557`, replaces engine `faction_upkeep`) runs, in
order (`game.cpp:1561-1604`): `plans_upkeep` → social/repair → `mod_production_phase` (loops
bases → `mod_base_build`) → energy/diplomacy → `mod_social_ai` → `probe_upkeep` →
`move_upkeep` → tech selection. Per-unit orders fire separately via `mod_enemy_move` as the
engine iterates units. So **faction-level policy** (strategy, production, tech, SE) lives in
`mod_faction_upkeep`; **per-unit orders** live in `mod_enemy_move` — matching our two-tier +
drill-down model exactly.

## 5. Adapter design

### 5.0 Which slot Claude occupies (decide this first)

Everything below depends on one bit: `is_human(faction_id)` — `FactionStatus[0] & (1 << faction_id)`
(`faction.cpp:109-112`). It selects not just the UI but **which rules the faction plays under**.
Three viable configurations:

| | **Mode A — AI slot** | **Mode B — human slot** | **Mode B+ — human slot, managed** |
| --- | --- | --- | --- |
| `is_human` | false | true | true |
| Dialogs | none fire | all fire and block | all fire and block |
| Deterministic tier | full (Thinker AI) | **none** | bases + units (see below) |
| Plays by fair rules | **no** — inherits AI handicaps | yes | yes |
| Good for | unattended autonomous runs | copilot (S3) | **autonomous, fair** |

**Mode A's problem is not cosmetic.** Non-human factions get a systematic handicap layer, not a
single branch: unit support (`base.cpp:1645`), facility maintenance (`game.cpp:1846-1859`), tech
cost (`tech.cpp:1155`), terraform speed (`veh_action.cpp:210`), mind-control cost
(`probe.cpp:713`), combat (`veh_combat.cpp:1558-1567`), content population
(`content_pop_player` vs `content_pop_computer`, `base.cpp:4220`), starting units
(`faction.cpp:1759-1760, 2234-2235`). Two are outright rule differences: **AI factions pay no
retool penalty at all** (`base.cpp:1045`, `build.cpp:11`), and AI factions below Transcend
**do not accumulate global warming** (`base.cpp:3205`).

Project policy is to **record these, not patch them out** ([game-surface.md](game-surface.md)
§5): the active set is declared in the world view's `fairness` block. So a Mode A run is
legitimate and interpretable — it just cannot be reported as a fair win without citing the
profile.

**Mode B+ is the resolution.** Two config switches let a *human* faction borrow Thinker's AI
brain while keeping human rules:

- `conf.manage_player_bases` (`main.h:225`) — routes human bases through `mod_base_build` /
  `mod_base_hurry` instead of the engine's `base_reset` (`base.cpp:991`, `build.cpp:55`).
- `conf.manage_player_units` (`main.h:225`) — lets Thinker drive human units, but only those the
  player has set to *automated* (`veh->order_auto_type`, `move.cpp:2074-2098`).

So Mode B+ gives fair rules **and** a deterministic tier. Two gaps remain, and the LLM tier must
own them outright because no AI path executes for a human faction:

- **Energy allocation** — `mod_allocate_energy` returns early for humans (`game.cpp:1908`); the
  AI heuristic at `game.cpp:1922-2010` exists but never runs. Port it or let Claude decide.
- **Social engineering** — `mod_social_ai` hard-returns at `faction.cpp:1463`; `conf.social_ai`
  applies to AI factions only.

Also note `BASE::gov_config()` (`engine_base.h:248`) returns `~0u` for AI but only the ticked
bits for humans — so in Mode B/B+ the 21 governor permission flags become a real, recurring
decision surface with **no AI policy to copy**.

**Status: the gate is implemented** (`scbrown/thinker`, branch
`claude/neural-amplifier-smac-testing-uq1fcg`). `Config.llm_factions` is a bitmask over faction
slots (default `0` = stock Thinker, no bridge), `llm_endpoint` is the orchestrator base URL, and
`llm_enabled(faction_id)` in `faction.cpp` sits alongside `thinker_enabled` rather than
replacing it — a faction that is not LLM-routed falls through to Thinker's own choice, and that
fall-through *is* the deterministic tier. Both options are documented in `docs/thinker.ini` at
the point of use. No decision is intercepted yet; that is A1.

**Recommendation: Mode B+ for autonomous play, Mode B for copilot.** Mode A stays usable —
project policy is to **record the handicaps, not patch them out**
([game-surface.md](game-surface.md) §5), so a Mode A run is legitimate as long as its `fairness`
profile is declared and reported. What it cannot support is an *unqualified* fair-play claim;
only Mode B+ (empty profile) does that. Full
surface consequences in [game-surface.md](game-surface.md); dialog handling in
[headless-harness.md](headless-harness.md) §4.

### 5.1 Tiers and hooks

- **Deterministic tier = Thinker's native AI.** For any decision we don't route to the LLM, the
  hook returns Thinker's own choice. Free, fast, already good. (In Mode B/B+ this requires the
  `manage_player_*` switches above — otherwise there is no deterministic tier at all.)
- **LLM tier via hooks.** For an LLM-controlled faction, wrap the clean hooks:
  - `mod_base_build` → world view (this base + faction context) + `action_space` (the base's
    buildable items) → orchestrator → apply returned build id. **Best first spike** (A1): one
    `int` in, one `int` out, self-contained.
  - `mod_enemy_move` → per-unit drill-down.
  - `mod_faction_upkeep` (or `plans_upkeep`) → per-turn policy the sub-hooks then follow.
- **Action space** comes from the game's own legality (buildable list, legal moves, available
  techs), so illegal choices can't be returned.
- **Transport from inside the DLL.** The hook runs in `terranx.exe`'s 32-bit address space, so
  the HTTP call to the orchestrator uses **WinHTTP/WinINet** (or a helper thread/process). It
  **must not block the message pump** for long — Thinker already installs a low-CPU idle hook
  `ModPeekMessage` (`patch.cpp:135`); do the request on a worker thread and apply the result
  when the hook next runs, or bound the synchronous wait tightly. Keep the DLL **thin**: all
  reusable logic lives in the orchestrator (which is unit-testable); the DLL just serializes
  state and applies the choice.

## 6. Build & config

- **Toolchain:** 32-bit **MinGW** (i686), C++11 — *not* MSVC. `CMakeLists.txt:16-18` pins
  `i686-w64-mingw32-g++`. Deps: `apt install build-essential cmake g++-mingw-w64-i686-posix`
  (Technical.md:157). Build: `cmake --preset develop && cmake --build --preset develop`
  (presets in `CMakePresets.json`). Targets: `thinker.dll` + `thinker.exe` launcher
  (`CMakeLists.txt:61-81`). Runs on Windows or under **Wine**.
- **Config:** `thinker.ini` `[thinker]`, parsed by `option_handler` (`main.cpp:12`) into
  `struct Config` (`main.h:205`). **Add a toggle** (`llm_factions` bitmask, `llm_endpoint`):
  new `Config` field + one `else if (MATCH(...))` clause + read `conf.<field>` in the gate
  (`thinker_enabled`) and the hooks to route selected factions to the LLM.

## 7. Open questions / next steps

1. **A0 spike:** build `thinker.dll` under MinGW; confirm it patches `terranx.exe` under Wine
   and that `mod_base_build` fires (log base_id + chosen item).
2. **A1:** add the `llm_factions`/`llm_endpoint` config, wrap `mod_base_build` to POST the
   world view and apply Claude's build id for one faction. First end-to-end LLM decision.
3. Confirm the exact `action_space` sources: the buildable-items list for a base, legal-orders
   list for a unit, available-techs list — and whether to read them from Thinker helpers or the
   engine.
4. Decide the transport (WinHTTP worker thread vs. local helper process) and the
   non-blocking/timeout policy against the message pump.
5. Wine + virtual-display harness for unattended/CI runs (the hardest part of this route) —
   designed in **[headless-harness.md](headless-harness.md)**, which also covers the game
   fixture (`terranx.exe` v2.0 sourcing) and the menu-free startup seams (`load_daemon`,
   `cmd_parse`, `mod_auto_save`).

## Reference

- Thinker (upstream): <https://github.com/induktio/thinker> — `Technical.md`, `Details.md`.
- Contract this adapter implements: [contract.md](contract.md).
