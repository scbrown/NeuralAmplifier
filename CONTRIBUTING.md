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

## Tooling

**Fastest route: [`scripts/setup-environment.sh`](scripts/setup-environment.sh)** installs
everything below and prints a per-tool verification table at the end. Each lane fails
independently, so a partial run still leaves you able to work.

For a **Claude Code cloud session**, paste that file's body into the environment's *Setup
script* field (claude.ai/code → the cloud icon above the message box → add/edit environment).
The field takes a script, not a path, and it runs before the repo is available, so it cannot
call the checked-in copy — this file is the version-controlled thing you paste from. Three
constraints shape it, and all three are load-bearing:

- **It must exit 0.** A non-zero exit means the session *fails to start*, so every optional
  step is `|| true` and the script ends in an explicit `exit 0`.
- **It must finish in ~5 minutes**, or the environment cache never builds — and without the
  cache it re-runs on *every* session instead of once. Quipu alone is 4m19s measured, so the
  lanes run in parallel and the total is the slowest lane rather than the sum.
- **The cache is a filesystem snapshot**: installed files persist, running processes do not.
  It rebuilds when the script changes or after ~7 days.

Cloud sessions already ship Rust, Python (+uv, ruff, mypy, pytest), Node, ninja, git, jq, and
ripgrep, so the script only adds what is genuinely missing.

Otherwise, install by tier. **Core** is everything you need to build, lint, test, and track
work — no game, no cross-compiler, no knowledge graph. **Per-component** is only needed if you
touch that component.

### Core (always)

| Tool | Install | Used for |
| --- | --- | --- |
| [just](https://github.com/casey/just) | `npm install -g rust-just` (seconds; `cargo install just` also works but takes minutes) | The single entry point for every command |
| [uv](https://github.com/astral-sh/uv) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Python env + deps for `orchestrator/` |
| [pre-commit](https://pre-commit.com/) | `pip install pre-commit` | The quality gate (`just check`) |
| [Node](https://nodejs.org/) 22+ | your platform's installer | markdown + `.gls.js` tooling via `npx` |
| [beads](https://github.com/steveyegge/beads) (`bd`) | `npm install -g @beads/bd` — or `brew install beads` | Task tracking (see below) |
| [mdBook](https://rust-lang.github.io/mdBook/) | `cargo install mdbook` — or grab a [release binary](https://github.com/rust-lang/mdBook/releases) | Building the docs site (`just docs build`) |

> **Install `bd` from npm or Homebrew, not `go install`.** The `go install` route documented
> upstream builds with `CGO_ENABLED=0`, and embedded Dolt needs CGO — `bd init` then fails with
> *"embedded Dolt requires a CGO build"*. The npm and brew packages ship working binaries.

Then:

```bash
just setup     # install the git hooks
just check     # the full gate: whitespace, YAML/JSON, markdown, ruff
just test      # every component's tests
```

### Per-component

| If you're touching… | You also need |
| --- | --- |
| `orchestrator/` telemetry | Nothing extra — `opentelemetry-sdk` is in the dev group so the exporter is tested in the default lane. At runtime it's the `otel` extra (`uv sync --extra otel`), enabled with `NA_OTEL=1`; layer 1 (JSONL) has no dependencies. |
| `adapters/thinker/` | A 32-bit MinGW toolchain and **CMake ≥ 3.31** — see below |
| `adapters/glsmac/` | A [GLSMAC](https://github.com/afwbkbc/glsmac) checkout + its build deps (SDL2, GL/GLU/GLEW, FreeType, yaml-cpp, uuid). Point `just` at it with `GLSMAC_DIR=/path/to/glsmac`. Headless logic tests use GLSMAC's own `--gse-tests` path — no display. [Notes](docs/glsmac-integration-notes.md). |
| Running a real game | Your own copy of *Alpha Centauri* (see the game fixture below), plus **Wine + Xvfb** — see below |
| Quipu retrieval (K2) | A `quipu` build with `--features shacl,onnx` — see below |
| The datalinks graph (K1) | Nothing extra. `just ingest` parses **your** `alphax.txt` into `datalinks/` (gitignored — it is derived game data). Deterministic: no model, no tokens, no API key. |

**Thinker cross-compile (works on Linux, no game required):**

```bash
sudo apt install build-essential ninja-build g++-mingw-w64-i686-posix
cmake --preset ninja-develop && cmake --build --preset ninja-develop
# → build/develop/thinker.dll — "PE32 executable (DLL) … Intel 80386"
```

Two things that will bite you, both verified the hard way:

- **CMake ≥ 3.31 is required** by the fork's `CMakePresets.json`, which is newer than Debian/
  Ubuntu's packaged CMake and several CI images. `pip install "cmake>=3.31"` is the quickest fix.
- **The Ninja and Makefiles presets share a `binaryDir`**, so switching generators in an existing
  tree fails with *"does not match the generator used previously"*. Delete `build/` first.

CI runs this lane on every push and uploads `thinker.dll` as an artifact — so a build break is
caught without anyone needing SMAC installed.

**Wine + Xvfb (only for running a real game):**

`terranx.exe` is a 32-bit Windows GUI binary, so this needs `wine32` via i386 multiarch. Xvfb
gives it a virtual display — the game still renders, it just has nobody watching
([headless-harness.md](docs/headless-harness.md) §1).

```bash
sudo dpkg --add-architecture i386 && sudo apt update
sudo apt install libgd3:i386            # <- FIRST. See below.
sudo apt install wine wine32:i386 xvfb
WINEARCH=win32 WINEPREFIX=~/.wine32 wineboot -i   # 32-bit prefix, not the default
```

Two traps, both hit on this image:

- **Install `libgd3:i386` first.** On Ubuntu noble, `wine32:i386` pulls in
  `libgphoto2-6t64:i386`, which depends on `libgd3:i386` that apt declines to auto-install. The
  transaction then dies with `E: Unable to correct problems, you have held broken packages` and
  names no package at all. Installing `libgd3:i386` explicitly clears it.
- **The prefix must be `WINEARCH=win32`.** A default 64-bit prefix will not run a 32-bit
  `terranx.exe`, and the prefix architecture cannot be changed after creation — delete and
  recreate.

**Quipu (the knowledge layer, K2 onward):**

```bash
cargo install --locked --git https://github.com/scbrown/quipu \
    --features shacl,onnx --bin quipu --bin quipu-server
```

- **There is no prebuilt binary.** The repo's releases are release-plz *source* tags with empty
  asset lists, so this compiles from scratch — budget ~15 minutes cold on 4 cores. Rust ≥ 1.85.
- **`--features shacl,onnx` is mandatory, not a preference.** `shacl` enforces the
  anti-masquerade tier predicates at write time; without it a Thinker house-rule stores as
  canonical SMAC, silently — the exact failure
  [knowledge-architecture.md](docs/knowledge-architecture.md) exists to prevent. `onnx` supplies
  the embedding runtime; without it `quipu_context` and `quipu_hybrid_search` degrade to SPARQL
  `CONTAINS`.
- **Name both binaries explicitly.** `quipu-server` declares `required-features`, and a plain
  `cargo build` *silently skips it* — exit 0, no warning, leaving whatever binary was there
  before. Quipu ships `scripts/build-deploy-server.sh` for exactly this reason.
- ONNX Runtime is loaded dynamically, so a missing native library fails at the first embedding
  call rather than at build time.

**Not needed:** `rusty-beads`. It requires a newer Rust than the container ships, falls back to
a slow source build, and `bd` from npm does the same job.

## Task Tracking

Work lives in **beads** (`bd`), not markdown TODO lists. `.beads/issues.jsonl` is the
git-tracked artifact; `.beads/embeddeddolt/` is local state and is gitignored.

```bash
bd ready                            # unblocked work — start here
bd show <id>                        # detail, including what's blocking it
bd update <id> --claim              # claim before starting
bd close <id> "what landed"
bd export -o .beads/issues.jsonl    # refresh the tracked artifact before committing
```

Auto-export is throttled, so **re-export before you commit** or your work won't be visible to
anyone else. Full conventions are in [AGENTS.md](AGENTS.md).

## Setup

1. Install the core tooling above.
2. Run `just setup` to install git hooks.
3. For **running the actual game**: your own copy of *Alpha Centauri* — see the game fixture
   below. Not needed to build, lint, or run the test suite.

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
- Ruff lint + format (Python)

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
