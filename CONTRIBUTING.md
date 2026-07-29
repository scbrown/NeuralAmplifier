# Contributing to neural amplifier

## Using Just

This project uses [just](https://github.com/casey/just) as a command runner. **Always prefer
`just` commands over raw tool commands** — they're configured with sensible defaults.

```bash
just --list          # Show available commands
just setup           # Install pre-commit hooks
just check           # Run all quality checks
just build           # Build every component
just test            # Test every component
just lint            # Lint every component
just fmt             # Format every component
```

Component-scoped recipes take a subcommand:

```bash
just orchestrator test       # Python brain (Claude Agent SDK)
just glsmac test             # GLSMAC adapter — headless via --gse-tests
just thinker build           # Thinker adapter (needs the Thinker toolchain)
just play thinker GAIANS     # Full loop for an engine (thinker | glsmac)
```

## Setup

1. Install [just](https://github.com/casey/just)
2. Install [pre-commit](https://pre-commit.com/)
3. Install [uv](https://github.com/astral-sh/uv) (for the Python orchestrator)
4. Have Node available (for markdown/`.gls.js` tooling via `npx`)
5. For the **GLSMAC adapter**: a local [GLSMAC](https://github.com/afwbkbc/glsmac) checkout and
   its build deps (SDL2, GL/GLU/GLEW, FreeType, yaml-cpp, uuid). Point `just` at it with
   `GLSMAC_DIR=/path/to/glsmac`. Headless logic tests use GLSMAC's own `--gse-tests` path — no
   display needed. See [docs/glsmac-integration-notes.md](docs/glsmac-integration-notes.md).
6. For the **Thinker adapter** (the current focus): the Thinker fork and a 32-bit MinGW
   toolchain — `apt install build-essential cmake g++-mingw-w64-i686-posix`. Cross-compiles on
   Linux; the game itself runs under Windows or Wine. See
   [docs/thinker-adapter-notes.md](docs/thinker-adapter-notes.md).
7. For **running the actual game**: your own copy of *Alpha Centauri* — see the game fixture
   below. Not needed to build, lint, or run the test suite.
8. Run `just setup` to install git hooks.

## The Game Fixture (bring your own SMAC)

Running a real game needs **Alien Crossfire v2.0 `terranx.exe`** — Thinker verifies the binary
and refuses anything else. The check is one command:

```bash
sha1sum terranx.exe   # want: 4b19c1fe3266b5ebc4305cd182ed6e864e3a1c4a
```

Extract your install once to a directory outside the repo and point the harness at it with
`SMAC_DIR=/path/to/smac`. **Game data is copyrighted and must never be committed** — the repo
holds only a checksum manifest. Steam's Planetary Pack is the expected source; a physical-media
ISO works too but ships v1.0 and needs the official v2.0 patch. Full detail, including why the
ISO path is worth keeping, is in [docs/headless-harness.md](docs/headless-harness.md) §2.

Everything in the default CI lane — orchestrator tests, linting, and the `thinker.dll`
cross-compile — runs with **no game present**. Only integration runs need the fixture.

## Implementing the Design

The design is written down and source-grounded; please read before building.

- **[AGENTS.md](AGENTS.md)** has a "before you implement" doc map and the nine design
  invariants. Start there — the invariants are load-bearing, not style preferences.
- **Work contract-first.** New capability usually means extending
  [docs/contract.md](docs/contract.md) first, then the adapter, then the orchestrator. The
  contract is versioned so engines can differ; a field an engine lacks is *omitted*, not faked.
- **Push tests down.** Most logic belongs in the orchestrator, where it tests against fixtures
  with no game and no tokens. Reserve real-game runs for harvesting fixtures and catching
  integration drift — see [docs/building-and-testing.md](docs/building-and-testing.md).
- **Adding a decision hook?** Give it a stable surface ID and add the row to
  [docs/game-surface.md](docs/game-surface.md), including its tier and whether an engine AI path
  exists. That table is how we know what an AI player can and cannot do.
- **Touching a Cargo/CMake feature or a CI lane?** Wire it into CI in the same change — a
  feature that ships dark isn't shipped.
- **Record what you couldn't verify.** These docs cite `file:line` and are explicit about
  inference vs. fact. Keep that habit; a confident wrong citation costs more than a gap.

## Pre-Commit Hooks

This project uses [pre-commit](https://pre-commit.com/) to enforce quality standards. Hooks
run automatically on `git commit` and include:

- Trailing whitespace removal
- End-of-file newline
- YAML/JSON validation
- Merge conflict detection
- Markdown linting
- Ruff lint + format (Python; active once `orchestrator/` lands)

To run all hooks manually:

```bash
just check
```

## Quality Gates

All checks must pass before pushing:

```bash
just check           # Pre-commit hooks
just test            # Component tests
just lint            # Component linters
```

CI runs the same gate on every push and pull request via GitHub Actions.

## License Boundary

Original work in this repo — `orchestrator/`, `adapters/thinker/`, `adapters/glsmac/mod/`,
`docs/` — is **MIT**. The GSE `http` builtin under `adapters/glsmac/builtin/` modifies GLSMAC
and is **AGPL-3.0**; keep that surface minimal and plan to contribute it upstream. Don't copy
GLSMAC source into the MIT-licensed parts of the tree.

## How We Build & Test

See [docs/building-and-testing.md](docs/building-and-testing.md) for the per-component build
and test strategy (the GLSMAC adapter's logic is tested headless via `--gse-tests` — no Xvfb),
the [contract](docs/contract.md) as the shared test seam, and what is testable at each phase of
the roadmap.
