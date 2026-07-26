# Building & Testing

Neural Amplifier is three small components in three toolchains, joined by one integration
loop. This document is the practical answer to *"how do we build and test this?"* — including
the awkward parts (GLSMAC has no headless mode, its scripting layer is sandboxed, and the
brain is an external, non-deterministic, paid API).

The guiding principle: **push as much testing as possible down to where it's fast, free, and
deterministic** — the Python orchestrator — and reserve the slow, game-dependent tests for a
gated integration lane.

## The pieces

| Component | Language / build | Tested with | Runs in default CI? |
|-----------|------------------|-------------|:-------------------:|
| `orchestrator/` | Python · `uv` | `pytest`, `ruff`, `mypy` | ✅ (once scaffolded) |
| `mod/` | `.gls.js` (interpreted) | GLSMAC script runner + payload harness | ⚠️ partial |
| `engine/` | C++ · CMake (against GLSMAC) | build + smoke test | ❌ (needs GLSMAC) |
| *integration* | all of the above | headless (Xvfb) end-to-end | ❌ (nightly / local) |

## The test pyramid

```text
        ┌────────────────────────────────────────┐
        │  e2e: real Claude, real game (manual)   │  few · costs tokens
        ├────────────────────────────────────────┤
        │  integration: headless game + fake      │  some · Xvfb + GLSMAC
        │  Claude, deterministic seed             │
        ├────────────────────────────────────────┤
        │  contract: world-view schema + mod↔     │  more · no game needed
        │  orchestrator payloads                  │
        ├────────────────────────────────────────┤
        │  unit: orchestrator logic on fixtures   │  most · fast · free
        └────────────────────────────────────────┘
```

## 1. Orchestrator (Python) — the fast, free feedback loop

This is where most testing lives, and the **first thing we can build and test end-to-end
without a game at all** (roadmap Phase 1).

- **Build:** `just orchestrator build` → `uv sync`.
- **Test:** `just orchestrator test` → `uv run pytest`.
- **Lint/format:** `just orchestrator lint` (`ruff` + `mypy`), `just orchestrator fmt`.

What we test here, with no GLSMAC in sight:

- **Move selection on golden fixtures.** Feed recorded world-view JSON (`tests/fixtures/*.json`)
  through the orchestrator and assert it returns a **legal** move drawn from the fixture's
  `action_space`. Assert it *never* emits an action outside that set — the anti-hallucination
  guarantee, tested directly.
- **Fake Claude.** A stub SDK client that returns scripted responses (including malformed and
  illegal ones) so tests are deterministic and cost nothing. Real API calls are opt-in behind
  an env flag.
- **Schema validation.** The world-view and the moves round-trip through the versioned
  schema; reject unknown `schema_version`, missing fields, and out-of-range coordinates.
- **Memory continuity.** Notes written on turn N are present on turn N+1.
- **Degradation.** On timeout / API error / budget exceeded, the orchestrator returns the
  safe fallback (`end_turn`) rather than raising.

## 2. Mod (`.gls.js`) — the thin in-game client

There's nothing to compile — `.gls.js` is interpreted by GLSMAC's GSE engine. Testing it
means checking two contracts:

- **It snapshots the right world view.** Load the mod in a controlled game and assert the JSON
  it produces matches a golden fixture for a known seed.
- **It applies orders correctly.** Given a canned orders payload, assert it calls the expected
  bindings (`game.um` move, `game.fm`, production) and that the engine accepts them.

How, in practice:

- Point the GSE HTTP builtin at a **local stub server** (a few lines of Python) that records
  the outgoing world view and replays a fixed orders response. This exercises the whole mod
  path without Claude, and the recorded world view becomes an orchestrator fixture — the two
  components share one contract.
- Lint/format with `prettier` (`just mod lint` / `just mod fmt`).

## 3. Engine (C++ GSE HTTP builtin) — the one native addition

- **Build:** `just engine apply` grafts the builtin into a GLSMAC checkout, then
  `just engine build` runs `cmake --build`. Requires `GLSMAC_DIR` and GLSMAC's deps (SDL2,
  GL/GLU/GLEW, FreeType, yaml-cpp, uuid).
- **Test:** `just engine test` runs a smoke script that boots GLSMAC with a one-line
  `--mainscript` which calls the builtin against a local echo server and asserts the response
  comes back through `Async`. If the builtin is registered and round-trips, it passes.
- Keep the patch **minimal and isolated** so it's cheap to rebase onto upstream and clean to
  contribute back. All behavior lives in the mod, not here.

## 4. Integration — the full observe→decide→act loop

`just play GAIANS` boots the whole thing; the automated version runs it under a virtual
display and asserts a full turn completes.

Handling GLSMAC's constraints:

- **No headless mode.** Wrap the game in **Xvfb** (`xvfb-run`), with `--nosound` and
  `--windowed`. The backend/frontend split means the render loop runs but we ignore it.
- **Determinism.** Use `--quickstart --quickstart-seed <N> --quickstart-mapsize WxH` so the
  same seed yields the same board every run.
- **Free & deterministic by default.** Integration tests use the **fake Claude** stub, so they
  assert the *plumbing* (mod → builtin → orchestrator → mod → engine, orders accepted, turn
  advances) without paying for or depending on the model.
- **Real Claude e2e** is a separate, manual/nightly lane behind an API key — it checks that
  the model plays *legally and sensibly*, and is where we watch play quality.

> **Asset note:** GLSMAC loads assets from an existing SMAC install and requires one to run a
> real game. Integration/e2e therefore need a provisioned GLSMAC + SMAC data + display, which
> is why they stay out of the default CI lane and run locally or on a dedicated nightly runner.

## CI

Default CI (GitHub Actions, `.github/workflows/ci.yml`) runs only what's fast and
self-contained:

- **Markdown lint** and the **pre-commit gate** (whitespace, YAML/JSON, merge conflicts,
  markdownlint, Ruff).
- **Orchestrator** lint + `pytest`, auto-skipped until `orchestrator/pyproject.toml` exists.

Game-dependent lanes (engine build, headless integration, real-Claude e2e) are intentionally
**not** in default CI — they need a GLSMAC build, a display, SMAC assets, and (for e2e) API
credentials. They run locally via `just` and, later, on a nightly workflow.

## What's testable when (maps to VISION §Roadmap)

- **Phase 0 (Spike):** engine smoke test (builtin round-trips); mod loads and fires on `turn`.
- **Phase 1 (MVP loop):** full orchestrator unit + contract suite on fixtures — the bulk of
  our tests, no game required.
- **Phase 2 (Autonomous):** headless integration with fake Claude; a whole game completes
  under Xvfb on a fixed seed.
- **Phase 3 (Copilot):** tests for the suggest/approve path.
- **Phase 4 (Depth & upstream):** multi-faction integration; engine builtin proposed upstream
  with its own tests.
