# neural amplifier - Agent Instructions

## Project Overview

An LLM brain for [GLSMAC](https://github.com/afwbkbc/glsmac) (open-source Alpha Centauri).
Each turn, a thin `.gls.js` mod snapshots the board and sends it — via a small GSE HTTP
builtin — to an external Python orchestrator that calls Claude and returns validated moves.
See [VISION.md](VISION.md) for the full design and roadmap.

## Repository Layout

A platform-agnostic brain + two engine adapters, joined by one JSON contract.

- `orchestrator/` — Python brain, the LLM decision loop (Claude Agent SDK). **MIT.**
- `adapters/thinker/` — near-term: DLL bridge to the original `terranx.exe` via a Thinker
  fork. **MIT.**
- `adapters/glsmac/` — long-term: `.gls.js` mod (**MIT**) + a GSE `http` builtin that modifies
  GLSMAC (**AGPL-3.0**).
- `docs/` — `contract.md` (the shared interface), `building-and-testing.md`,
  `headless-harness.md` (game fixture + unattended runs), `game-surface.md` (the decision
  inventory + AI coverage matrix), and the adapter notes (`thinker-adapter-notes.md`,
  `glsmac-integration-notes.md`).

## Conventions

- **Always use `just` instead of raw commands.** The justfile is the single entry point and
  is configured with quiet output by default to save context — you only see errors and
  warnings.
- **Prefer subcommands over separate recipes.** Group related operations under one recipe
  with a subcommand argument (e.g. `just orchestrator test`, `just docs lint`) rather than
  creating separate top-level recipes (`just orchestrator-test`, `just docs-lint`).
- **Mind the license boundary.** Original work under `orchestrator/`, `adapters/thinker/`,
  `adapters/glsmac/mod/`, and `docs/` is MIT. The GSE builtin under `adapters/glsmac/builtin/`
  modifies GLSMAC and is AGPL-3.0 — keep that surface small and contribute it upstream.
- **One contract.** The orchestrator speaks only `docs/contract.md`; engine specifics stay in
  the adapter. Don't leak Thinker/GLSMAC details into the orchestrator.

## Build Commands

```bash
just --list          # Show available commands
just setup           # Install pre-commit hooks
just check           # Run all quality checks
just build           # Build every component
just test            # Test every component
```

For verbose output when debugging:

```bash
just check verbose=true
```

Component-scoped work uses the `<component> <cmd>` form:

```bash
just orchestrator test    # build install test lint fmt run
just glsmac test          # build test lint fmt  (test = headless --gse-tests)
just thinker build        # build test           (needs the Thinker toolchain)
just play thinker GAIANS  # full observe→decide→act loop for an engine
```

## Quality Requirements

### Before Every Push

You MUST run and pass the full quality gate before pushing:

```bash
just check
```

This runs all pre-commit hooks: trailing-whitespace and EOF checks, YAML/JSON validation,
merge-conflict detection, markdown linting, and Ruff (once Python code exists).
**Do NOT push if any check fails.** Fix the issues and re-run.

### Test Requirements

- All existing tests must pass before pushing (`just test`).
- New functionality must include corresponding tests.
- Tests are part of the pre-push gate.

### Documentation Requirements

- User-facing changes MUST include documentation updates.
- Update `README.md` if the change affects quick-start or usage.
- Update `docs/building-and-testing.md` if the build or test flow changes.

## Landing the Plane (Session Completion)

**When ending a work session**, complete ALL steps below. Work is NOT complete until
`git push` succeeds.

1. **Run quality gates** — `just check` must pass.
2. **Run tests** — `just test` must pass for affected components.
3. **Commit and push**:

   ```bash
   git add <files>
   git commit -m "<type>: <description>"
   git push
   ```

4. **Verify** — All changes committed AND pushed.

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds.
- NEVER stop before pushing — that leaves work stranded locally.
- NEVER say "ready to push when you are" — YOU must push.
- If push fails, resolve and retry until it succeeds.
