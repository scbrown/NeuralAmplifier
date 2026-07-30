# neural amplifier - Agent Instructions

## Project Overview

An LLM brain for *Sid Meier's Alpha Centauri*. Each turn an engine adapter hands Claude a
fog-limited **world view** plus a menu of **legal actions**; Claude reasons and returns orders;
the engine validates and executes them. One platform-agnostic brain, two engine adapters, one
JSON [contract](docs/contract.md). See [VISION.md](VISION.md) for the full design and roadmap.

**Current focus: the Thinker adapter** (Track A) — the original `terranx.exe` driven through a
Thinker fork. It is the complete, balanced game and is controllable now. GLSMAC (Track B) is the
long-term open platform but most of its game systems don't exist yet. Don't assume GLSMAC.

**Status: pre-alpha.** The repo holds design docs and scaffold; no component has landed yet.
Read the design before writing code — it is source-grounded and will save you a day.

## Before You Implement

| If you're working on… | Read first |
|---|---|
| Anything at all | [VISION.md](VISION.md), [docs/contract.md](docs/contract.md) |
| A Thinker hook, or choosing which faction slot Claude drives | [docs/thinker-adapter-notes.md](docs/thinker-adapter-notes.md) — esp. §5.0 slot modes |
| Running the game unattended, dialogs, or the SMAC game fixture | [docs/headless-harness.md](docs/headless-harness.md) |
| Deciding what an AI player must cover, or adding a decision hook | [docs/game-surface.md](docs/game-surface.md) |
| Moving a surface to the LLM tier, or wondering why a decision is poor | [docs/decision-inputs.md](docs/decision-inputs.md) — the per-surface input checklist |
| Tests, CI lanes, or fixtures | [docs/building-and-testing.md](docs/building-and-testing.md) |
| Logging, metrics, tracing, or measuring coverage | [docs/observability.md](docs/observability.md) |
| A GLSMAC mod or the GSE builtin | [docs/glsmac-integration-notes.md](docs/glsmac-integration-notes.md) |
| Knowledge, memory, or guardrails | [docs/knowledge-architecture.md](docs/knowledge-architecture.md) |

## Design Invariants

Break these and the project stops being what it claims to be. They are not style preferences.

1. **The engine is authoritative.** Claude picks from the engine-supplied `action_space` and
   never invents an action. An illegal order must be impossible, not merely unlikely.
2. **One contract.** The orchestrator speaks only [docs/contract.md](docs/contract.md) and must
   never learn which engine it is driving. Engine specifics stay in the adapter.
3. **Keep the adapter thin.** Reusable logic belongs in the orchestrator, which is unit-testable
   with no game. The DLL/mod serializes state and applies a choice — nothing more.
4. **Assign the tier deliberately.** Every decision is either deterministic tier or LLM tier.
   Accidental assignment is a bug; record it in [docs/game-surface.md](docs/game-surface.md).
5. **Emit a surface ID from every decision hook.** Coverage is measured, not assumed — see
   [docs/game-surface.md](docs/game-surface.md) §1.
6. **Declare the AI handicaps; never hide them.** Non-human factions get a systematic bonus
   layer (fairness ledger, [docs/game-surface.md](docs/game-surface.md) §5). Policy is to
   **record, not neutralise**: every active asymmetry goes in the world view's `fairness` block
   and onto the decision record. Never report a Mode A result as a fair win — cite the profile.
7. **Intercept in-game dialogs; never blanket-suppress them.** They are decision points. Only
   Thinker's *fatal error* `MessageBoxA` should be suppressed
   ([docs/headless-harness.md](docs/headless-harness.md) §4).
8. **Never commit game assets.** SMAC data is copyrighted. The repo holds a checksum manifest;
   the bytes live in `$SMAC_DIR` outside the tree.
9. **Degrade safely.** A slow, broken, or over-budget model returns the safe fallback. The game
   never stalls waiting on the brain.

## Repository Layout

A platform-agnostic brain + two engine adapters, joined by one JSON contract.

- `orchestrator/` — Python brain, the LLM decision loop (Claude Agent SDK). **MIT.**
- `adapters/thinker/` — near-term: DLL bridge to the original `terranx.exe` via a Thinker
  fork. **MIT.**
- `adapters/glsmac/` — long-term: `.gls.js` mod (**MIT**) + a GSE `http` builtin that modifies
  GLSMAC (**AGPL-3.0**).
- `docs/` — `contract.md` (the shared interface), `building-and-testing.md`,
  `headless-harness.md` (game fixture + unattended runs), `game-surface.md` (the decision
  inventory + AI coverage matrix), `observability.md` (decision records, tracing, coverage
  measurement), and the adapter notes (`thinker-adapter-notes.md`,
  `glsmac-integration-notes.md`).

### Orchestrator module map

Every module owns one invariant. The invariant is why the module exists, so check it before
changing the module — most of these look like ordinary plumbing and are not.

| Module | Owns | The invariant it protects |
|---|---|---|
| `contract.py` | The wire types (`docs/contract.md`) | A field an engine lacks is *omitted*, never faked |
| `surfaces.py` | The frozen 77-surface registry | A renamed surface invalidates every recorded run |
| `orchestrator.py` | `decide()` — the whole loop | **Exactly one decision record per decision**, on every path |
| `validate.py` | Action-space checking | Orders can only name actions the engine offered |
| `fog.py` | Diplomacy-feed gating | The brain never sees a pact between factions it hasn't met |
| `fairness.py` | The computed handicap ledger | `favours` is *derived*, never copied from a table — three entries flip side by difficulty |
| `knowledge.py` | The Quipu/Hank seam | Knowledge degrades, never stalls; a dead guard **allows** |
| `decisions.py` | The record + JSONL log | The record of truth, written before any exporter |
| `telemetry.py` | Sink fan-out + OTel | The record is assembled **once**; layers are projections of one object |
| `coverage.py` | Run health | `degrade_rate` and `fair_play` are measured, not asserted |
| `replay.py` | World-view store + diffing | A log alone can't be replayed — something must keep the bytes |
| `datalinks/` | SMAC's own rules → Quipu | Provenance on every fact, and **filtered on read** or the tag is decoration |
| `brain.py` | Claude / scripted brains | CI never makes a paid call |

The one that surprises people: `telemetry.Emitter` takes an *already-built* record and hands the
same instance to every sink. Assembling it twice is how a dashboard and a log drift apart.

## Task Tracking — Beads

Work is tracked in **[beads](https://github.com/steveyegge/beads)** (`bd`), not in markdown
TODO lists. Issues live in a local Dolt database; **`.beads/issues.jsonl` is the git-tracked
artifact** and `.beads/embeddeddolt/` is local working state (already gitignored).

```bash
bd ready                     # unblocked work, highest priority first — start here
bd show <id>                 # full detail, including why something is blocked
bd update <id> --claim       # claim before starting
bd close <id> -r "what landed"     # -r is a flag; a positional reason is rejected
bd create "Title" -p 1 -t task -d "..."
bd dep add <blocked> <blocker>
bd export -o .beads/issues.jsonl   # refresh the tracked artifact before committing
```

Conventions for this repo:

- **Re-export before you commit.** Auto-export is throttled, so `.beads/issues.jsonl` can lag
  the database. `bd export -o .beads/issues.jsonl` is the step that makes your work visible to
  everyone else — an unexported bead may as well not exist.
- **Never commit `.beads/embeddeddolt/`.** It is per-machine state and would conflict on every
  merge. `.beads/.gitignore` already handles this; don't override it.
- **Put the *why* in the description**, with a `file:line` or a doc section. A bead that only
  restates its title is not worth the round trip.
- **Model real blockers as dependencies** so `bd ready` stays trustworthy. If everything is
  ready, nothing is prioritised.
- **Read the output of `bd close`.** It refuses to close a bead that is still blocked
  (`cannot close X: blocked by open issues [Y]`) and errors on an unknown id. Both are silent
  if you redirect to `/dev/null` — which has already produced two commits here claiming
  "Closes na-…" for a bead that stayed open. Check `bd ready` before you write the commit
  message, not after.

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

Tooling you need to install first — and the two cross-compile gotchas — is in
[CONTRIBUTING.md](CONTRIBUTING.md#tooling). Core tooling (`just`, `uv`, `pre-commit`, Node,
`bd`) needs no game and no cross-compiler.

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
