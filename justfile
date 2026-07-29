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
build: (orchestrator "build") (glsmac "build") (thinker "build")

# Test every component
test: (orchestrator "test") (glsmac "test")

# Lint every component
lint: (orchestrator "lint") (glsmac "lint")

# Format every component
fmt: (orchestrator "fmt") (glsmac "fmt")

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
        lint)          uv run ruff check . && uv run ruff format --check . && uv run mypy ;;
        fmt)           uv run ruff format . ;;
        run)           uv run neural-amplifier serve ;;
        *)             echo "Unknown: {{cmd}}. Try: build install test lint fmt run" ;;
    esac

# === GLSMAC adapter (Track B · .gls.js mod + GSE http builtin) ===

# The long-term open engine adapter: just glsmac <cmd>  (needs GLSMAC_DIR for build/test)
# Commands: build test lint fmt   (build = the AGPL http builtin; test = headless --gse-tests)
glsmac cmd="test":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d adapters/glsmac/mod ]; then
        echo "glsmac adapter: not yet scaffolded (see VISION.md §Roadmap, Track B)."; exit 0
    fi
    case "{{cmd}}" in
        build)  bash adapters/glsmac/builtin/scripts/apply.sh "{{glsmac}}" \
                    && cmake --build "{{glsmac}}/build" --target glsmac ;;
        test)   bash adapters/glsmac/mod/scripts/gse-test.sh "{{glsmac}}" ;;  # headless --gse-tests
        lint)   npx prettier --check "adapters/glsmac/mod/**/*.js" ;;
        fmt)    npx prettier --write "adapters/glsmac/mod/**/*.js" ;;
        *)      echo "Unknown: {{cmd}}. Try: build test lint fmt" ;;
    esac

# === Thinker adapter (Track A · DLL bridge to terranx.exe) ===

# The near-term deep-game adapter: just thinker <cmd>  (needs the Thinker toolchain)
# Commands: build test
thinker cmd="build":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d adapters/thinker/src ]; then
        echo "thinker adapter: not yet scaffolded (see VISION.md §Roadmap, Track A)."; exit 0
    fi
    case "{{cmd}}" in
        build)  bash adapters/thinker/scripts/build.sh ;;
        test)   bash adapters/thinker/scripts/test.sh ;;   # runs SMAC under Wine
        *)      echo "Unknown: {{cmd}}. Try: build test" ;;
    esac

# === Integration ===

# Run the full observe→decide→act loop end to end for the chosen engine.
# engine = thinker | glsmac ; drives one faction with Claude via the orchestrator.
play engine="thinker" faction="GAIANS":
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Starting orchestrator + {{engine}} adapter, faction {{faction}}..."
    bash scripts/play.sh "{{engine}}" "{{faction}}"

# === Coverage ===

# Summarise a decision log: which surfaces fired, fallback rate, adherence.
# Fails if the brain was largely absent or an illegal action slipped through.
coverage log="decisions.jsonl" max_degrade_rate="0.05":
    @cd orchestrator && uv run neural-amplifier coverage "../{{log}}" \
        --max-degrade-rate {{max_degrade_rate}}

# Replay a recorded log through the current orchestrator — no game, no tokens.
# Needs a world-view store from the run (set NA_WORLD_VIEW_STORE when recording).
# exact=true additionally requires identical decisions; only valid for scripted runs.
replay log="decisions.jsonl" store="worldviews" exact="false":
    @cd orchestrator && uv run neural-amplifier replay "../{{log}}" \
        --store "../{{store}}" {{ if exact == "true" { "--exact" } else { "" } }}

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
