# neural amplifier
# Run `just --list` to see available recipes

# Quiet by default to save context; use verbose=true for full output
verbose := "false"

# Path to a local GLSMAC checkout (needed to build/run the engine builtin + mod)
glsmac := env_var_or_default("GLSMAC_DIR", "../glsmac")

# Default recipe - show available commands
default:
    @just --list

# === Setup ===

# Install pre-commit hooks and verify tooling
setup:
    pre-commit install
    @echo "Setup complete."

# === Quality (all components) ===

# Full quality gate (pre-push): runs every pre-commit hook across the repo
check:
    pre-commit run --all-files

# Build every component
build: (orchestrator "build") (mod "build") (engine "build")

# Test every component
test: (orchestrator "test") (mod "test")

# Lint every component
lint: (orchestrator "lint") (mod "lint")

# Format every component
fmt: (orchestrator "fmt") (mod "fmt")

# === Orchestrator (Python · Claude Agent SDK) ===

# The LLM brain service: just orchestrator <cmd>
# Commands: build install test lint fmt run
orchestrator cmd="test":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f orchestrator/pyproject.toml ]; then
        echo "orchestrator: not yet scaffolded (see VISION.md §Roadmap, Phase 1)."; exit 0
    fi
    cd orchestrator
    case "{{cmd}}" in
        build|install) uv sync ;;
        test)          uv run pytest ;;
        lint)          uv run ruff check . && uv run mypy . ;;
        fmt)           uv run ruff format . ;;
        run)           uv run neural-amplifier ;;
        *)             echo "Unknown: {{cmd}}. Try: build install test lint fmt run" ;;
    esac

# === Mod (.gls.js GLSMAC agent mod) ===

# The thin in-game client: just mod <cmd>
# Commands: build test lint fmt
mod cmd="test":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d mod/src ]; then
        echo "mod: not yet scaffolded (see VISION.md §Roadmap, Phase 0)."; exit 0
    fi
    case "{{cmd}}" in
        build)  echo "mod is interpreted .gls.js — nothing to compile." ;;
        test)   bash mod/scripts/test.sh ;;
        lint)   npx prettier --check "mod/**/*.js" ;;
        fmt)    npx prettier --write "mod/**/*.js" ;;
        *)      echo "Unknown: {{cmd}}. Try: build test lint fmt" ;;
    esac

# === Engine (C++ · GSE HTTP builtin, AGPL-3.0 boundary) ===

# The one engine addition: just engine <cmd>  (needs GLSMAC_DIR / --glsmac)
# Commands: build test apply
engine cmd="build":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d engine/src ]; then
        echo "engine: not yet scaffolded (see VISION.md §Roadmap, Phase 0)."; exit 0
    fi
    if [ ! -d "{{glsmac}}" ]; then
        echo "engine: GLSMAC checkout not found at '{{glsmac}}'. Set GLSMAC_DIR."; exit 1
    fi
    case "{{cmd}}" in
        apply)  bash engine/scripts/apply.sh "{{glsmac}}" ;;   # graft builtin into GLSMAC tree
        build)  cmake --build "{{glsmac}}/build" --target glsmac ;;
        test)   bash engine/scripts/smoke.sh "{{glsmac}}" ;;
        *)      echo "Unknown: {{cmd}}. Try: apply build test" ;;
    esac

# === Integration ===

# Run the full observe→decide→act loop against GLSMAC under a virtual display.
# Boots a quickstart game, loads the mod, and drives one faction with Claude.
play faction="GAIANS":
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Starting orchestrator + GLSMAC (headless via Xvfb), faction {{faction}}..."
    bash scripts/play.sh "{{glsmac}}" "{{faction}}"

# === Documentation ===

# Documentation: just docs <cmd>
# Commands: lint fix fmt check
docs cmd="check":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{cmd}}" in
        lint)  npx markdownlint-cli2 "**/*.md" ;;
        fix)   npx markdownlint-cli2 --fix "**/*.md" ;;
        fmt)   npx prettier --write "**/*.md" --prose-wrap preserve ;;
        check) npx markdownlint-cli2 "**/*.md" ;;
        *)     echo "Unknown: {{cmd}}. Try: lint fix fmt check" ;;
    esac
