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
6. For the **Thinker adapter**: the Thinker fork and its 32-bit MinGW/MSVC toolchain; runs
   under Windows or Wine. See [docs/thinker-adapter-notes.md](docs/thinker-adapter-notes.md).
7. Run `just setup` to install git hooks.

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
