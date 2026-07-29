# Building & Testing

How we build, test, and iterate — grounded in what the two engines actually allow (verified
against source; specifics in [glsmac-integration-notes.md](glsmac-integration-notes.md) and
[thinker-adapter-notes.md](thinker-adapter-notes.md)).

Guiding principle: **push testing down to where it's fast, free, and deterministic** — the
platform-agnostic orchestrator and the GLSMAC `--gse-tests` path — and reserve slow,
game-dependent runs for a gated integration lane.

The one lane that can't be pushed down — a real game, unattended — has its own design:
**[headless-harness.md](headless-harness.md)** covers making an owned SMAC copy available to
the harness (the *game fixture*) and driving it with no human at the keyboard.

## The pieces

| Component | Build | Test | In default CI? |
|-----------|-------|------|:--------------:|
| `orchestrator/` (Python) | `uv sync` | `pytest` + `ruff`/`mypy` on fixtures, fake Claude | ✅ |
| `adapters/glsmac/mod` (`.gls.js`) | none (interpreted) | GLSMAC `--gse-tests` — **headless, no assets** | ⚠️ once mocks extended |
| `adapters/glsmac/builtin` (C++) | CMake vs. GLSMAC | build + `--gse-tests` smoke | ❌ (needs GLSMAC) |
| `adapters/thinker` (C++ DLL) | MinGW 32-bit — **cross-compiles on Linux** | run SMAC under Wine | ✅ build only (no game needed) |
| *full game* | — | end-to-end play | ❌ (nightly / local) |

## The test pyramid

```text
   ┌──────────────────────────────────────────────┐
   │  e2e: real Claude, real game (manual)         │  few · costs tokens
   ├──────────────────────────────────────────────┤
   │  integration: a real game drives the loop     │  GLSMAC (SMAC assets) /
   │  with a fake Claude                           │  Thinker (Wine)
   ├──────────────────────────────────────────────┤
   │  contract: world-view/orders JSON both        │  no game needed
   │  adapters must satisfy                         │
   ├──────────────────────────────────────────────┤
   │  unit: orchestrator logic + gls.js mod logic  │  most · fast · free
   │  (--gse-tests) on fixtures                     │
   └──────────────────────────────────────────────┘
```

## 1. Orchestrator (Python) — the fast, free core

The first thing we build (roadmap S1), and where most tests live — **no game required**.

- **Build:** `just orchestrator build` (`uv sync`). **Test:** `just orchestrator test`
  (`pytest`). **Lint:** `ruff` + `mypy`.
- **Fake Claude.** A stub SDK client returns scripted responses (including malformed/illegal
  ones); real API calls are opt-in behind an env flag. Deterministic and free.
- **Golden fixtures.** Recorded [contract](contract.md) world views → assert the orchestrator
  returns only `action_id`s present in `action_space` (the anti-hallucination guarantee),
  handles missing sections (thin GLSMAC world views), respects `fog:false`, keeps memory
  across turns, and degrades to the safe fallback on timeout/error/budget.
- Because the orchestrator is engine-agnostic, these tests cover **both** adapters at once.

## 2. GLSMAC adapter — headless logic tests, no Xvfb

The key finding: **GLSMAC runs `.gls.js` fully headless** via `--gse-tests` /
`--gse-tests-script` — `graphics::Null`/`audio::Null`, no window, no SMAC assets
(`main.cpp:206-242`). So the mod's logic (world-view building, order application) is
**unit-testable with no display and no game boot**:

```bash
GLSMAC --gse-tests --gse-tests-script GLSMAC_data/tests/na_worldview.gls.js
```

- **Enabling fork change:** the built-in test mocks stub only a limited API. **Extending them
  to the game bindings (`um`/`bm`/`tm`/`fm` + events)** makes the mod fully testable this way —
  a high-ROI change tracked in the integration notes' fork backlog.
- **Building the `http` builtin:** `just glsmac build` applies the builtin into a `GLSMAC_DIR`
  checkout and `cmake --build`s it. Deps and the exact seam are in the integration notes.
- **Xvfb is not needed for logic tests.** It only ever mattered for a *full rendered game*, and
  we're replacing that need with a real headless backend mode (fork backlog item 3).

## 3. GLSMAC full game — the one place assets are required

A complete GLSMAC game **requires a real SMAC install** (`ResourceManager` validates and
decodes SMAC assets; no synthetic fallback — `resource/ResourceManager.cpp`, `map/Map.cpp:99`).
Until the headless backend mode + asset/render decoupling land, full-game integration needs
SMAC data and a GL display, so it stays out of default CI. Determinism via
`--quickstart --quickstart-seed A:B:C:D`.

## 4. Thinker adapter — test by running the real game

Thinker is a closed-binary patch, so there's no in-isolation unit test of game logic:

- **Build:** `just thinker build` (32-bit MinGW). This **cross-compiles on Linux with no game
  present** and is verified in CI on every push — `apt install build-essential ninja-build
  g++-mingw-w64-i686-posix`, then `cmake --preset ninja-develop && cmake --build --preset
  ninja-develop`, producing a `PE32 executable (DLL) … Intel 80386`. Two gotchas: the fork's
  `CMakePresets.json` requires **CMake ≥ 3.31** (newer than several distro and runner images),
  and the Ninja presets share a `binaryDir` with the Makefiles ones, so switching generators in
  place fails on the stale cache — delete `build/` first.
- **Test:** run SMAC + the DLL under **Wine** with a virtual display and assert the loop
  (state → `/decide` → applied choice) completes. Deterministic where SMAC allows; fake Claude
  for free/repeatable runs.
- Keep the DLL **thin** — all reusable logic belongs in the orchestrator, which *is* unit-
  testable. The DLL just serializes state to the contract and applies the returned choice.

## 5. The contract is the shared test seam

Both adapters must satisfy [contract.md](contract.md). A **contract test suite** validates
that a captured world view is well-formed and that orders reference real `action_id`s — run
against fixtures from either engine, with no game. Recording a real GLSMAC `--gse-tests` world
view produces an orchestrator fixture *and* a contract fixture from one run.

## 6. CI

Default CI (`.github/workflows/ci.yml`) runs only the fast, self-contained lanes: markdown
lint, the pre-commit gate, the orchestrator (`ruff`, `ruff format`, `mypy`, `pytest`), and the
**Thinker DLL cross-compile**, which uploads `thinker.dll` as a build artifact. None of these
need a game, an adapter checkout of SMAC, or an API key — the scripted brain is the default, so
CI never makes a paid call. Game-dependent lanes — the GLSMAC builtin build, GLSMAC full-game
integration (SMAC assets), and Thinker/Wine e2e — run locally via `just` and, later, on a
nightly runner.
GLSMAC upstream CI compiles but runs no tests; adding a Debug `--gse-tests` step (no display,
no assets) is the cleanest check to build on.

## What's testable when (maps to VISION §Roadmap)

- **S1:** full orchestrator + contract suite on fixtures — the bulk of tests, no game. **Landed:**
  contract types, `POST /decide`, action-space validation, safe degradation, the decision record
  and JSONL log, and the coverage report (`just coverage`).
- **A0–A2 (Thinker):** DLL builds; loop runs under Wine with fake Claude; a faction plays.
- **B0 (GLSMAC):** `http` builtin smoke via `--gse-tests`; mod logic tested headless once mocks
  are extended.
- **B1–B2:** integration as the action space, then fog and depth, come online.
