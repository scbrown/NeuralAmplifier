# Thinker Adapter Notes

Source-grounded reference for the **Thinker adapter** (Track A / near-term). Verified against
the fork at `scbrown/thinker` (paths are `src/...`; `file:line` cited). For the overall
dual-engine design and shared contract, see [VISION.md](https://github.com/scbrown/NeuralAmplifier/blob/main/VISION.md) and [contract.md](contract.md).

**Citations re-verified against upstream Thinker v5.5** (fork branch
`claude/thinker-upstream-sync-us8srt`). v5.5 extracted the whole turn loop out of `game.cpp`
into a new **`gameturn.cpp`** and dropped the `mod_` prefix from those functions
(`mod_faction_upkeep` → `faction_upkeep`, and likewise for `turn_upkeep`, `repair_phase`,
`production_phase`, `allocate_energy`). Every `file:line` below was re-resolved against the
merged tree rather than renamed mechanically.

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
  (`main.cpp:656`), which installs every hook. `ThinkerModule()` (`main.cpp:622`) is an
  export stub the patched import table points at.
- **Two hook primitives** (`patch.cpp`, declared `patch.h:15-23`), both verify the original
  bytes and abort on mismatch (binary-compat guard):
  - `write_jump(addr, func)` (`patch.cpp:192`) — overwrite a function prologue with a jump →
    **replace a whole engine function**. The ~200-entry table starts at `patch.cpp:463`.
  - `write_call(addr, func)` (`patch.cpp:224`) — rewrite one call-site → **intercept a single
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
| **turn** (whole faction) | `faction_upkeep(faction_id)` | — (runs the faction's whole turn) | `gameturn.cpp:1568` (hook `patch.cpp:516`) | ★ cleanest handoff |
| **base** (production) | `mod_base_build(base_id, has_gov)` | build item id (facility / unit / special) | `base.cpp:1249` (picker `select_build` `build.cpp:1385`) | ★ clean, self-contained |
| **unit** (orders) | `mod_enemy_move(veh_id)` | order/sync code | `veh_turn.cpp:137` (hook `patch.cpp:702`) | ★ clean (dispatches by type) |
| research | `mod_tech_ai(faction_id)` | tech id | `tech.cpp:629` (hook `patch.cpp:840`) | clean |
| social engineering | `mod_social_ai(faction_id, …)` | sets SE model | `faction.cpp:1500` | clean |
| unit design | `design_units(faction_id)` | — | `plan.cpp:174` | clean per-faction |
| strategy pass | `plans_upkeep(faction_id)` | — (builds `AIPlans`) | `plan.cpp:503` | faction-level |
| diplomacy | `enemy_diplomacy(faction_id)` + many `net_*`/`mod_tech_val` | — | `gameturn.cpp:1585`, `patch.cpp:514-545` | diffuse |

> **The turn row changed shape in v5.5, not just address.** `faction_upkeep` is now installed
> with **`write_jump(0x527290)`** (`patch.cpp:516`) — the whole engine function is replaced —
> where it used to be a `write_call` intercepting one site. That is why the adapter's
> turn-level seams must live inside `gameturn.cpp`'s body and nowhere else: there is no
> surviving call site left to wrap.

**The per-faction switch (where "LLM controls faction N" plugs in):**
`thinker_enabled(faction_id)` (`faction.cpp:152`) / `thinker_move_upkeep(faction_id)`
(`faction.cpp:176`) gate
whether Thinker's AI (vs. legacy) runs, driven by `conf.factions_enabled` etc. Our adapter adds
a parallel gate: for LLM-routed factions, a hook serializes state + action space, asks the
orchestrator, and applies the result; otherwise it falls through to Thinker's native decision
(that fall-through **is** our deterministic tier).

## 3. Reading state → the world view

Engine globals (`extern` in `engine.h:489-495`): `Factions`, `MFactions`, `Bases`, `Units`,
`Vehs`, `MapTiles`; counts `*BaseCount` (`engine.h:297`), `*VehCount` (`:298`), `*CurrentTurn`
(`:43`); tile accessor `mapsq(x,y)` (`map.h:14`). Thinker's own per-faction strategy lives in
`AIPlans plans[]` (`main.h:581`).

Key structs (packed):

| Struct | World-view fields | file:line |
| -------- | ------------------- | ----------- |
| `MAP` (tile) | `climate`, `contour`, `owner`, `items`/`landmarks` bitmasks, `region`, **`visibility`** (per-faction) | `engine_types.h:6` |
| `VEH` (unit) | `x,y`, `unit_id`, `faction_id`, `state`, `order`, `morale`, `moves_spent`, `damage_taken`, `home_base_id`; helpers `triad()`, `is_colony()`, `is_former()` | `engine_veh.h:482` |
| `BASE` | `x,y`, `faction_id`, `pop_size`, `name`, `queue_items[10]`, `facilities_built[12]`, worked tiles, `nutrient/mineral/energy_intake/_surplus` | `engine_base.h:148` |
| `Faction` | `energy_credits`, `SE_Politics/Economics/Values/Future`, `tech_research_id`, `tech_achieved`, `labs_total`, `diplo_status[8]`, `base_count` | `engine_types.h:293` |

**Fog-of-war is real and per-faction** — filter the world view to what a faction can see:
`MAP::visibility` bitfield → `is_visible(faction_id)` / `is_known(faction_id)`
(`engine_types.h:47-52`); units also carry `VEH::visibility` (`engine_veh.h:500`). So the Thinker
world view is **fair from day one** (`contract` `map.fog: true`) — unlike GLSMAC today.

**Enumeration:** `for i in 0..*BaseCount: Bases[i]` (filter `faction_id`); same for `Vehs[]`;
tiles via `mapsq(x,y)` gated on `is_known(faction_id)`. Economy/tech/SE read off `Factions[fid]`.

## 4. The per-faction AI turn (the handoff pipeline)

`faction_upkeep(faction_id)` (`gameturn.cpp:1568-1686`, replacing the engine's own at
`0x527290`) runs, in order: `init_save_game` → `plans_upkeep` → `reset_netmsg_status` →
`social_upkeep` → `repair_phase` → `production_phase` (loops bases → `mod_base_build`) → then,
**only when `full_game_turn()`**, `allocate_energy` → `enemy_diplomacy` → `enemy_strategy` →
`mod_social_ai` → `probe_upkeep` → `move_upkeep` → corner-market check; and after that block,
unconditionally, hurry-cost settlement → elimination check → tech selection → council →
autosave.

Two things in that sequence are worth not glossing. `init_save_game` is new in v5.5 and runs
*first*. And the `full_game_turn()` gate means the strategy/energy/diplomacy half of the turn
does **not** run every call — a decision hook placed inside it inherits that gate, which is
correct for policy but wrong if you assumed one invocation per faction-turn.

Per-unit orders fire separately via `mod_enemy_move` as the engine iterates units. So
**faction-level policy** (strategy, production, tech, SE) lives in `faction_upkeep`; **per-unit
orders** live in `mod_enemy_move` — matching our two-tier + drill-down model exactly.

## 5. Adapter design

### 5.0 Which slot Claude occupies (decide this first)

Everything below depends on one bit: `is_human(faction_id)` — `FactionStatus[0] & (1 << faction_id)`
(`faction.cpp:110`). It selects not just the UI but **which rules the faction plays under**.
Three viable configurations:

| | **Mode A — AI slot** | **Mode B — human slot** | **Mode B+ — human slot, managed** |
| --- | --- | --- | --- |
| `is_human` | false | true | true |
| Dialogs | none fire | all fire and block | all fire and block |
| Deterministic tier | full (Thinker AI) | **none** | bases + units (see below) |
| Plays by fair rules | **no** — inherits AI handicaps | yes | yes |
| Good for | unattended autonomous runs | copilot (S3) | **autonomous, fair** |

**Mode A's problem is not cosmetic.** Non-human factions get a systematic handicap layer, not a
single branch: unit support (`base.cpp:1763`), facility maintenance (`base.cpp:3093-3095`), tech
cost (`tech.cpp:1175`), terraform speed (`veh_action.cpp:210`), mind-control cost
(`probe.cpp:713`), combat (`veh_combat.cpp:1558-1567`), content population
(`content_pop_player` vs `content_pop_computer`, `base.cpp:4411-4424`), starting units
(`faction.cpp:1826-1827, 2276-2277`). Two are outright rule differences: **AI factions pay no
retool penalty at all** (`base.cpp:1149`, `build.cpp:12`), and AI factions below Transcend
**do not accumulate global warming** (`base.cpp:3336` — the `*ClimateLevel` increment is gated
on `is_human(faction_id) || *DiffLevel >= 4`).

Project policy is to **record these, not patch them out** ([game-surface.md](game-surface.md)
§5): the active set is declared in the world view's `fairness` block. So a Mode A run is
legitimate and interpretable — it just cannot be reported as a fair win without citing the
profile.

**Mode B+ is the resolution.** Two config switches let a *human* faction borrow Thinker's AI
brain while keeping human rules:

- `conf.manage_player_bases` (`main.h:170`) — routes human bases through `mod_base_build` /
  `mod_base_hurry` instead of the engine's `base_reset` (`base.cpp:1081`, `build.cpp:69`).
- `conf.manage_player_units` (`main.h:171`) — lets Thinker drive human units, but only those the
  player has set to *automated* (`veh->order_auto_type`, `move.cpp:2090-2094`).

So Mode B+ gives fair rules **and** a deterministic tier. Two gaps remain, and the LLM tier must
own them outright because no AI path executes for a human faction:

- **Energy allocation** — `allocate_energy` returns early for humans (`gameturn.cpp:1971`, inside
  the `is_human(faction_id)` block opened at `:1959`); the AI heuristic at
  `gameturn.cpp:2042-2110` exists but never runs. Port it or let Claude decide. Re-verified
  against v5.5: the early return survived the rewrite.
- **Social engineering** — `mod_social_ai` hard-returns for humans at `faction.cpp:1505-1507`
  (`is_human(faction_id) || !is_alive(faction_id)`); `conf.social_ai` applies to AI factions only.

Also note `BASE::gov_config()` (`engine_base.h:248`) returns `~0u` for AI but only the ticked
bits for humans — so in Mode B/B+ the 21 governor permission flags become a real, recurring
decision surface with **no AI policy to copy**.

**Status: the gate is implemented** and has since landed on `scbrown/thinker`'s `master` (the
old `claude/neural-amplifier-smac-testing-uq1fcg` branch no longer exists on the remote; current
upstream-sync work is on `claude/thinker-upstream-sync-us8srt`). `Config.llm_factions` is a
bitmask over faction slots (default `0` = stock Thinker, no bridge), `llm_endpoint` is the
orchestrator base URL, and `llm_enabled(faction_id)` (`faction.cpp:171`) sits alongside
`thinker_enabled` (`faction.cpp:152`) rather than
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
  - `faction_upkeep` (or `plans_upkeep`) → per-turn policy the sub-hooks then follow. Since v5.5
    this one is a whole-function replacement in `gameturn.cpp`, so the seam is a line inside its
    body rather than a wrapper around a call site.
- **Action space** comes from the game's own legality (buildable list, legal moves, available
  techs), so illegal choices can't be returned.
- **Transport from inside the DLL.** The hook runs in `terranx.exe`'s 32-bit address space, so
  the HTTP call to the orchestrator uses **WinHTTP/WinINet** (or a helper thread/process). It
  **must not block the message pump** for long — Thinker already installs a low-CPU idle hook
  `ModPeekMessage` (`patch.cpp:110`); do the request on a worker thread and apply the result
  when the hook next runs, or bound the synchronous wait tightly. Keep the DLL **thin**: all
  reusable logic lives in the orchestrator (which is unit-testable); the DLL just serializes
  state and applies the choice.

### 5.2 Where a seam can live (and why the patch table almost never takes it)

The adapter has 68 call sites and 17 of its own helper definitions sitting inside 13
Thinker-owned files — the fork's whole merge-fragility surface, since `neural.cpp` and
`na_http.cpp` cannot conflict with anything. They are enumerated in the fork's
`tests/na_seams.tsv` and enforced by `tests/check_seams.py`.

`na_base_hurry_observed` is the one seam that costs a single line in `patch.cpp` rather than an
edit inside a function body, and it survived v5.5 needing no work at all. That makes it look
like the pattern to generalise. **It is not**, and the reason is worth recording so nobody
re-derives it:

> It works because `mod_base_hurry()` takes **no arguments** and reads everything it needs from
> globals (`*CurrentBaseID`), *and* because there was an existing patched call site (`0x4F7A38`)
> to re-point. Both halves are required.

Checked against every other candidate; none has both halves:

| Seam host | Why the table cannot take it |
| --- | --- |
| `mod_base_build` | **Not in the patch table at all** — reached only from `mod_base_reset` (`base.cpp:1094`). No entry to re-point. |
| `mod_tech_selection` | Seam must precede the `tech_research_id` assignment; a wrapper runs after it, and the world view would report the question as already settled. |
| `mod_social_ai` | Needs the locals `sf`, `sm2`, `soc`. |
| `move_upkeep` | Needs loop locals (`value`, cohort size) that only exist mid-iteration. |
| `mod_capture_base` | Needs `best_base_id` / `old_name`, deep inside a conditional branch. |
| `mod_base_production` | Needs `item_id`, in a nested branch. |
| `mod_base_yield` | Placement after `base_update` is load-bearing and more code follows. |
| `mod_base_swap`, `mod_energy_trade`, `mod_buy_tech` | Need mid-body locals (`cost_ask`, loan terms, tech price). |
| `mod_base_reset` | Convertible in principle, but it has **20** table entries — converting would change 20 lines to save one seam. |
| `faction_upkeep` | Mid-body, and now a whole-function `write_jump` (§2). |

So the durable answer for the other 67 seams is not relocation but **verification**: keep each
one a single line where possible, and let `check_seams.py` assert it is still present *and still
in the right place*. Placement is what carries the argument — sampling the corner-market reserve
after the deduction records what survived the decision rather than what it was made against.

> **Watch item:** re-pointing one table entry only intercepts *that* call site. `mod_base_upkeep`
> calls `mod_base_hurry()` directly at `base.cpp:4263`, bypassing `na_base_hurry_observed`, and
> it sits on the production path. Tracked as **na-4zs**; needs a real run to settle.

## 6. Build & config

- **Toolchain:** 32-bit **MinGW** (i686), C++11 — *not* MSVC. `CMakeLists.txt:16-18` pins
  `i686-w64-mingw32-g++`. Deps: `apt install build-essential cmake g++-mingw-w64-i686-posix`
  (Technical.md:160). Build: `cmake --preset develop && cmake --build --preset develop`
  (presets in `CMakePresets.json`). Targets: `thinker.dll` + `thinker.exe` launcher
  (`CMakeLists.txt:61-81`). Runs on Windows or under **Wine**. Note `CMakeLists.txt` globs
  `src/*.cpp` at **configure** time, so a merge that adds a source file (v5.5 added
  `gameturn.cpp`) needs `cmake --preset …` re-run before it will link.
- **Config:** `thinker.ini` `[thinker]`, parsed by `option_handler` (`main.cpp:21`) into
  `struct Config` (`main.h:150`). v5.5 rewrote `config.cpp` but left `option_handler` as the
  `thinker.ini` parser, so all 23 `llm_*`/`na_*` options still route through it. Three settings
  drive the bridge:

  | Setting | Default | What it does |
  | --- | --- | --- |
  | `llm_factions` | `0` | Bitmask of faction ids routed to the orchestrator. `0` is stock Thinker — no bridge in the loop at all. `llm_factions=2` routes faction 1. |
  | `llm_endpoint` | `http://127.0.0.1:8000` | Orchestrator base URL. `http` only; an `https` value is **refused**, not downgraded. |
  | `llm_timeout_ms` | `2500` | Ceiling on one decision's whole exchange. Past it the engine's own answer applies. Also **sent** to the orchestrator as `decision_deadline_ms` — see below. |

  `llm_timeout_ms` is not only a local timeout: every world view the decide paths post carries
  it as `decision_deadline_ms`, so the orchestrator can abandon the decision *before* the engine
  does. Without that the orchestrator had no way to learn when the game stopped listening, and
  an attached agent's late answer was recorded as an applied `tier=llm` decision the game never
  used — 66 adapter rows in one measured run, zero of them `tier=llm` (na-t3h;
  [observability.md §5.4](observability.md), [contract.md](contract.md)). Emitted by
  `na_write_decision_deadline` at the four decide sites only, and **omitted** rather than sent
  as `0` when `llm_timeout_ms <= 0`, since that configuration means wait-indefinitely and a
  literal `0` invites the opposite reading.

  It is not configurable, but the companion field is worth knowing about here: `na_run_id`
  composes a per-process id from `GetCurrentProcessId`, `GetTickCount` and `time(NULL)` at first
  use, and `na_write_head` stamps it on **every** world view as `run_id`. Unlike the deadline it
  rides on observation records too — it names the process rather than asserting a wait, and
  `na-observations.jsonl` is one file appended across every run of the game with nothing else in
  it to mark where one run ended. The orchestrator uses it to retire the decisions of a game that
  has been killed, which a deadline cannot do because a dead process never reaches one (na-bzd;
  [contract.md](contract.md)).

## 6.1 The transport

`src/na_http.cpp` — raw Winsock, ~300 lines, no engine headers.

**Synchronous on the engine thread, with one deadline.** Not a worker thread: the engine is
not thread-safe (`na-abc` measured a worker deadlocking on Thinker's global `FileLock` the
moment a modal dialog opened), and `mod_base_build`'s signature — one int in, one int out —
has nowhere to park a decision and resume it later. A turn-based game can afford a bounded
pause; it cannot afford an unbounded one. `llm_timeout_ms` bounds **connect + send + read
together**, not each stage, so the number in the config file means what a player reading it
would think it means.

**HTTP/1.0 on purpose.** uvicorn may answer an HTTP/1.1 request with chunked
transfer-encoding, and de-chunking is a parser the DLL has no business carrying. An HTTP/1.0
request may not be answered with chunked encoding, so the reply is always a plain body
followed by a close — which turns "read the response" into "read until EOF".

**One field is read out of the reply** (`choices[].action_id`), by string scan rather than a
JSON parser. Anything the scan misreads produces an id that fails the legality check and
falls back, so the failure mode of the shortcut is the safe path.

Testable with **no game and no API key**: `na_http.cpp` links no engine headers, so
`just thinker wire` builds it into a standalone exe, runs it under Wine against a stub server
*and* a real `neural-amplifier serve`, and checks all of the above including that the client
gives up on its own deadline rather than the server's.

## 7. Open questions / next steps

1. ~~**A0 spike:** confirm `thinker.dll` patches `terranx.exe` under Wine and `mod_base_build`
   fires.~~ Done — `na-4pu`.
2. ~~**A1:** wrap `mod_base_build` to POST the world view and apply Claude's build id.~~ Built
   and unit-tested; **not yet run against a real game** — that needs `$SMAC_DIR` and is the
   next thing to do (`na-61c`).
3. ~~Decide the transport and the non-blocking/timeout policy against the message pump.~~
   Decided and implemented — §6.1.
4. Confirm the remaining `action_space` sources: legal-orders list for a unit, and whether to
   read them from Thinker helpers or the engine.
5. Wine + virtual-display harness for unattended/CI runs (the hardest part of this route) —
   designed in **[headless-harness.md](headless-harness.md)**, which also covers the game
   fixture (`terranx.exe` v2.0 sourcing) and the menu-free startup seams (`load_daemon`,
   `cmd_parse`, `mod_auto_save`). This is what turns "A1 is built" into "A1 is proven"
   (`na-ie9`).

## 8. One decision per base-turn

`mod_base_build` fires **more than once per base per turn** — `mod_base_reset` is hooked at
**twenty** engine call sites (`patch.cpp:883-902`: four `BaseWin::*`, `bases_reset`, seven
`base_production`, `drone_riot`, `base_drones`, two `battle_fight_2`, `capture_base`, two
`enemy_strategy`, `time_warp`) and each one applies its own answer, so the last caller wins.
Measured in real play: 21 of 24 base-turns fired twice, and 11 of those pairs *disagreed*.

(This document previously said "eleven … `patch.cpp:859-869`". That was already stale before the
v5.5 sync — the count has been twenty across the merge-base, our `master`, and v5.5 alike — so
the correction is to the citation, not to the measurement above it, which stands.)

Left alone that would mean several paid model calls to settle one build, with the answer that
happened to be last silently winning. So the adapter numbers the calls (`call_seq`, indexed by
base and invalidated on turn change), asks the orchestrator only on `call_seq == 1`, and
replays that answer for the rest of the turn — re-checking legality each time, because a base
can lose the ability to build something mid-turn when a rival completes the secret project it
picked.

That is also what makes *exactly one decision record per decision* true rather than
aspirational, without having to argue which of eleven engine paths is philosophically
authoritative.

## Reference

- Thinker (upstream): <https://github.com/induktio/thinker> — `Technical.md`, `Details.md`.
- Contract this adapter implements: [contract.md](contract.md).
