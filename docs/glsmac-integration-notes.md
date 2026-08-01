# GLSMAC Integration Notes

Source-grounded reference for the **GLSMAC adapter** (Track B / long-term). For the overall
dual-engine design and the shared contract, see [VISION.md](../VISION.md) and
[contract.md](contract.md); for the near-term engine, [thinker-adapter-notes.md](thinker-adapter-notes.md).

The turnkey, source-grounded reference for building Neural Amplifier against GLSMAC.
Everything here was verified by reading the fork at `scbrown/glsmac` (paths are
`src/...` in that tree); `file:line` citations point at the evidence. Treat this as the
"what's actually true today" companion to the aspirational [VISION.md](../VISION.md).

> **Headline:** GLSMAC is early. The scripting engine, event system, and a handful of
> managers are real, but most of the *game* — production, research, diplomacy, combat
> entry-points, unit orders, fog-of-war — **does not exist yet**. Neural Amplifier
> therefore **co-develops with GLSMAC**: much of the near-term work is contributing game
> systems and a scriptable action layer to the fork, under AGPL-3.0.

## 1. The scripting model

- Managers are exposed on the `game` object both as `game.get_um()`… and as direct
  properties `game.um / bm / tm / rm / fm` (`game/backend/Game.cpp:755-779, 822-862`;
  getters `Game.cpp:1298-1311`). `State` only wraps `fm` (`State.cpp:191-199`); everything
  else is on `Game`.
- **Mutation is gated (this shapes the whole design).** Every state-changing call routes
  through `Game::CheckRW` (`Game.cpp:1567-1570`) and throws *"Game state is read-only. Try
  using events?"* unless a read-write window is open. RW is only granted inside
  `WithRW(...)` (`Game.cpp:1339-1343`), which wraps **event handler apply/rollback**
  (`Game.cpp:357,387`) and **turn processing** (`Game.cpp:1503,1528`). Reads are ungated.
- **So the AI's action space = registered GSE events, not direct calls.** A script defines
  actions with `game.register_event(name, {validate, resolve?, apply, rollback})`
  (`Game.cpp:688-732`) and fires them with `game.event(name, args)` (`Game.cpp:735-752`).
  This is a *gift*: `validate` is exactly the "is this move legal?" gate we want, and
  `apply/rollback` gives undo for free. Design the action space as a library of these events.

### Event handlers (the loop hooks)

Fired in `Game::AdvanceTurn` (`Game.cpp:1216-1252`), master-only:

| Event | Target | Payload | Notes |
| ------- | -------- | --------- | ------- |
| `turn` | `game` | `{ year: Int }` | Turn-poor — no turn id, no player, no lists. Call back into `um/bm/tm` yourself. `Game.cpp:1245-1250` |
| `unit_turn` | `um` | `{ unit }` | Per-unit; engine resets `moved_this_turn` after (`Game.cpp:1219-1228`) |
| `base_turn` | `bm` | `{ base }` | Per-base (`Game.cpp:1233-1240`) |

Also relevant: `um.on('unit_spawn'/'unit_despawn'/'unit_turn')`, `bm.on('base_spawn'/
'get_base_intake'/'get_base_workable_tiles')`, and global `start`, `configure`,
`create_world`, `error`, `message`.

## 2. Readable state (the world view we can build today)

| Source | Fields exposed to script | Cite |
| -------- | -------------------------- | ------ |
| Game/turn | `get_turn()`, `get_year()`, `is_turn_complete(slot)`, `get_player[s]()`, `is_master/slave/started`, `random`, `get_map()` | `Game.cpp:537-781` |
| Player | `id`, `type` (always `"human"`), `name`, `is_ready/master`, `get_faction()` | `Player.cpp:107-195` |
| Faction (`fm`) | `id`, `name`, `text_color`, `is_naval`, `is_progenitor`; `fm.list/add/remove` | `Faction.cpp:71-94`, `FactionManager.cpp:72+` |
| Unit (`um`) | `id`, `def`, `owner` (slot idx), `tile{x,y}`, `movement` (pts), `morale`, `health`, `moved_this_turn`, `is_land/water/air/immovable`; `um.has_unit/get_unit` | `Unit.cpp:125-176`, `UnitManager.cpp:443-451` |
| Base (`bm`) | `id`, `name`, `get_owner/tile/pops/size`, `get_workable/worked/unworked_tiles`, `is_tile_worked`, `get_intake`, `get_consumption`; `bm.get_bases()` | `Base.cpp:148-297`, `BaseManager.cpp:408` |
| Tile (`tm`) | `x`, `y`, `is_water/land`, `moisture`, `rockiness`, `elevation`, 8 neighbor links, `features{}` (river, monolith, xenofungus, jungle, uranium, geothermal, unity_pod, volcano, sunny_mesa, garland_crater, fossil_field_ridge, unity_energy/chopper/radar, dunes), `bonuses{nutrient,energy,minerals}`, `get_units()`, `get_base()`, `get_resources([player])` | `Tile.cpp:163-269`, `tile/Types.h:106-131` |

**Not readable today:** tile `owner`/territory, improvements/terraforming state (flags exist
at `tile/Types.h:140-143` but are never surfaced), base production/queue, and — importantly —
**there is no fog-of-war**. A repo-wide search for `visib|fog|explored|reveal|discover` in the
backend returns nothing. Scripts see **full ground truth for all players**. Fine for a first
bot; *not fair*, and "fair fog" is fork work (§5).

## 3. Mutating actions (what the action space can contain today)

All require an RW window (inside an event apply/rollback or a turn handler).

| Action | Status | Evidence |
| -------- | :------: | ---------- |
| Spawn / despawn unit | ✅ | `um.spawn_unit(...)` `UnitManager.cpp:465-518` |
| Move unit | ⚠️ basic | `unit.move_to_tile(tile, cb)` `Unit.cpp:144-166` → `UnitManager.cpp:643-692`. **No pathfinding, no adjacency/terrain check, no movement-point cost.** Teleports to any tile. |
| Set unit movement/morale/health | ✅ | `Unit.cpp:167-176` |
| Attack unit | ⚠️ no entry | pipeline exists (`UnitManager.cpp:694-747`) but **no script `attack()` method** |
| Found / despawn base | ✅ | `bm.spawn_base(owner,tile,{name}[,cb])` `BaseManager.cpp:356-388` |
| Base pops / worked tiles | ✅ | `Base.cpp:155-213` |
| Complete / uncomplete / advance turn | ✅ | `Game.cpp:604,629,654` (note `GlobalFinalizeTurn` is a TODO stub `Game.cpp:1272`; `Turn::FinalizeAndChecksum` stub `Turn.cpp:24`) |
| Register / fire custom event | ✅ | `Game.cpp:688-752` |
| **Set production / build queue** | ❌ absent | no production concept in `base/` (`Base.h:61-81`) |
| **Research / tech** | ❌ absent | no tech subsystem anywhere in backend |
| **Social engineering** | ❌ absent | — |
| **Diplomacy** | ❌ absent | — |
| **Terraform / improvements** | ❌ absent | flags defined, no mutator/getter |

**Also:** AI/computer slots are **not scriptable** — `Slot::Wrap` asserts `SS_PLAYER`
(`Slot.cpp:175-178`), so a bot controlling a non-human slot can't be wrapped/queried yet.

## 4. The HTTP builtin (how the mod reaches the orchestrator)

GSE ships no network IO; we add one builtin. The seam is precise and small:

- **Register** by deriving from `gse::Bindings` and implementing `AddToContext`, which calls
  `ctx->CreateBuiltin(name, value, ep)` (`gse/context/Context.cpp:123-125`; template
  `gse/builtins/Console.cpp:30`). Builtins are `#name` globals; to get `#http.request(...)`,
  register an Object named `http` whose `request` prop is a Native callable. Wire into
  `gse/builtins/Builtins.{h,cpp}` + `builtins/CMakeLists.txt`.
- **Non-blocking async is feasible** via `gc::Space::Accumulate` (`gc/Space.cpp:84-122`):
  do the HTTP call on a worker `std::thread`, then `Accumulate(...)` the callback — it's
  deferred to the main thread and drained every frame by `GSE::Iterate` (`gse/GSE.cpp:50-56`;
  loop at `GLSMAC.cpp:152`). **Do not** reuse the `Async` timer builtin (main-thread only,
  not worker-safe: `gse/Async.cpp:36-64`).
- **No JSON in the engine** (grep clean) → return the raw response body as a `String` and
  **parse it in `.gls.js`**, keeping C++ minimal.
- **No HTTP client / no libcurl** vendored → add **libcurl** as a new dependency (cleanest,
  gives TLS). `src/network/` is raw TCP only (`network/Network.h:15-140`).
- **Skeleton:** `src/gse/Http.{h,cpp}` (a `gc::Object` module owned by `GSE`, mirroring
  `gse::Async`, holding in-flight callbacks so they stay GC-reachable — copy the
  `GetReachableObjects` pattern at `gse/Async.cpp:132-148`) + `src/gse/builtins/Http.{h,cpp}`
  (the `Bindings`). Script API:

  ```js
  #http.request({ url: '...', method: 'POST', headers: {}, body: '...' }, (res) => {
      // res.status : Int, res.body : String (parse in-script)
  });
  ```

- **Gotchas:** keep the callback GC-reachable while in flight; build all `Value*` and run the
  callback **only** on the main thread (inside the `Accumulate` closure); deliver network
  errors as a response field, not a cross-thread C++ exception; join worker threads on
  shutdown (mirror `Async::ProcessAndExit`, `gse/Async.cpp:111-130`).

## 5. Headless & the render/asset coupling

- GLSMAC already ships **`graphics::Null` / `audio::Null` / `input::Null`** subsystems, used
  by its GSE test mode (`main.cpp:206-242`, `DF_GSE_ONLY` branch) — no OpenGL, no SDL window.
  This is the foundation for a real headless "dedicated" backend mode: route a game run
  through the Null subsystems instead of only the test task. Far less work than decoupling
  from scratch. (The normal path always builds a real GL window: `main.cpp:275-276`,
  `graphics/opengl/OpenGL.cpp:49-98`.)
- **But a full game currently needs a real SMAC install.** `ResourceManager::Init` probes for
  SMAC marker files and resolves the whole PCX/TTF/WAV table (`resource/ResourceManager.cpp:
  14-224, 244-348`); with no install it throws and quickstart never runs (`GLSMAC.cpp:401-419`).
  Map generation then actually decodes `texture.pcx` / `ter1.pcx` (`map/Map.cpp:99-100`).
  Stubbing filenames passes detection but crashes at PCX decode — there is **no synthetic
  asset path**.
- **Fork work for asset-free headless play:** decouple texture/asset loading (a rendering
  concern) from backend map generation, so a Null-graphics run can generate and play a map
  without SMAC PCX assets. The Null subsystem infra makes this tractable; it's the enabling
  change for CI-able full-game integration.

## 6. Building & testing (the fast loop)

- **Build (Debug — needed for the GSE test path + map/seed dumps):**
  `cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && make -C build -j` → `build/bin/GLSMAC`
  (`CMakeLists.txt:8,56-93`). Deps: `cmake build-essential libfreetype-dev libsdl2-dev
  libsdl2-image-dev libglu-dev libglew-dev libossp-uuid-dev libyaml-cpp-dev` (`README.md:143`).
  `Release` adds `-march=native`; use `Portable64` for a portable artifact.
- **Test the decision layer fully headless, no assets (highest-value loop):**
  `./GLSMAC --gse-tests --gse-tests-script GLSMAC_data/tests/foo.gls.js` runs any `.gls.js`
  through the real GSE engine with Null subsystems and mocks (`gse/tests/Scripts.cpp:23-98`,
  `task/gsetests/GSETests.cpp`). Existing corpus in `GLSMAC_data/tests/`.
  - **Fork win:** the test **mocks only stub a limited API** (`gse/tests/mocks/`). *Extending
    the mocks to cover the game bindings (`um/fm/tm/bm` + events)* turns "test the AI headless"
    from partial to complete — a high-ROI change to make in the fork.
- **Inject a real mod:** a dir `GLSMAC_data/mods/<name>/main.gls.js`, activated with
  `--mods <name>`; or override entry with `--mainscript` / world-gen with `--worldscript`
  (`config/Config.cpp:226-294`, `GLSMAC.cpp:20-52`).
- **Determinism:** `--quickstart --quickstart-seed A:B:C:D` (`Game.cpp:143-145`); Debug builds
  dump `~/.local/share/glsmac/debug/lastmap.{seed,gsm}` to replay maps (`Map.cpp:674-681`).
- **CI today** (`.github/workflows/buildall.yml`) compiles + static-analyzes but **runs no
  tests**. Adding a Debug `--gse-tests` step is the cleanest CI-friendly check — no display,
  no assets.

## 7. Prior art for the deterministic AI tier

GLSMAC has **no computer-opponent AI** (roadmapped upstream for ~v0.7), so the deterministic
tier is greenfield. Don't invent from scratch — adapt proven SMAC AI:

- **[Thinker](https://github.com/induktio/thinker)** — the modern gold-standard SMAC AI
  overhaul. **C++, MIT-licensed**, so its code is license-compatible to *adapt* into AGPL
  GLSMAC **with attribution** (preserve its MIT notice for adapted portions). Strong reference
  for production/movement AI, former/terraforming automation, base management, and expansion.
  *Caveat:* it patches the original `terranx.exe` and operates on SMAC's in-memory structures
  — a different data model than GLSMAC's — so we adapt **algorithms and heuristics**, not
  verbatim code, and several systems it tunes (production, tech, SE) must first exist in the
  fork.
- **[Freeciv](https://github.com/freeciv/freeciv)** AI (GPL) — a legally-inspectable,
  well-structured open 4X AI: autoworkers/autosettlers, city governors, threat evaluation,
  tech planning. Use as *design inspiration* (different data model; mind GPL if copying).
- **SMACX AI Growth mod** (Yitzi) — rules/data-level (`alphax.txt`) AI tuning; a source of
  good heuristic *parameter values* rather than code.
- **PRACX / Scient's Unofficial Patch** — patch-level AI/bugfix context.

Recommended posture: study Thinker's structure, **reimplement the heuristics against GLSMAC's
model** as the deterministic tier (former automation, pathfinding, base governor, production
defaults, threat/retreat), and let the LLM overlay strategy and drill down to specific events.

## 8. Fork work backlog (prioritized)

What we'll build in `scbrown/glsmac`, roughly in dependency order:

1. **GSE `http` builtin** (§4) — unblocks the whole external loop. Small, upstreamable.
2. **Extend GSE test mocks** to the game bindings (§6) — unblocks headless AI unit-testing.
3. **Headless "dedicated" game mode** via Null subsystems + asset/render decoupling (§5) —
   kills Xvfb, enables CI integration.
4. **Scriptable non-human slots** (`Slot::Wrap`, §3) — so a bot can control a faction.
5. **Real unit orders**: movement-point cost, adjacency/terrain legality, multi-turn goto,
   and a script `attack()` entry-point (§3). Deterministic tier depends on this.
6. **Base production / build queue** — the economic core the AI plans around.
7. **Fog-of-war / per-faction visibility** (§5) — for a *fair* world view.
8. **Tech / social engineering / diplomacy** — larger systems, later; align with upstream's
   own roadmap and contribute back.
