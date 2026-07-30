# neural amplifier
# Run `just --list` to see available recipes

# Quiet by default to save context; use verbose=true for full output
verbose := "false"

# Path to a local GLSMAC checkout (needed to build/run the engine builtin + mod)
glsmac := env_var_or_default("GLSMAC_DIR", "../glsmac")

# Path to your own extracted SMAC install (the game fixture — never committed)
smac := env_var_or_default("SMAC_DIR", "../smac")

# Path to a local Thinker checkout (source of the committed house-rule graph)
thinker := env_var_or_default("THINKER_DIR", "../thinker")

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

# Fails if the brain was largely absent or an illegal action slipped through.
# Summarise a decision log: surfaces fired, fallback rate, adherence
coverage log="decisions.jsonl" max_degrade_rate="0.05":
    @cd orchestrator && uv run neural-amplifier coverage "../{{log}}" \
        --max-degrade-rate {{max_degrade_rate}}

# Needs a world-view store from the run (set NA_WORLD_VIEW_STORE when recording).
# exact=true additionally requires identical decisions; scripted runs only.
# Replay a recorded log through the current orchestrator — no game, no tokens
replay log="decisions.jsonl" store="worldviews" exact="false":
    @cd orchestrator && uv run neural-amplifier replay "../{{log}}" \
        --store "../{{store}}" {{ if exact == "true" { "--exact" } else { "" } }}

# === Track A: play the real game ===

# One-time host setup: i686 cross-compiler, Wine, Xvfb, CMake >= 3.31.
setup-host:
    bash scripts/setup-host.sh

# Build our Thinker fork, install it over a real SMAC install, and launch.
# cmd = launch | headless | build | restore   (restore puts stock Thinker back)
# Needs THINKER_DIR; finds the game automatically or set SMAC_PLAY_DIR.
thinker-play cmd="launch":
    bash scripts/play-thinker.sh {{cmd}}

# See and drive the running game's window: just game-screen shot|click|key|info
# Captures the game WINDOW, not the root — under XWayland the root is solid black.
# Coordinates are window-relative. e.g. just game-screen "click 2370 1185"
game-screen args="shot":
    bash scripts/game-screen.sh {{args}}

# === Game fixture ===

# The repo holds paths and checksums, never the bytes (docs/headless-harness.md §2.3).
# Needs SMAC_DIR. `scan` refuses a tree with a mod overlay on it — see §2.4.
# Check or regenerate the SMAC fixture manifest: just game verify|scan
game cmd="verify" manifest="fixtures/smac/steam-2204130.manifest":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{cmd}}" in
        verify) python3 scripts/game_fixture.py verify "{{smac}}" --manifest "{{manifest}}" ;;
        strict) python3 scripts/game_fixture.py verify "{{smac}}" --manifest "{{manifest}}" --strict ;;
        scan)   python3 scripts/game_fixture.py scan "{{smac}}" --out "{{manifest}}" \
                    --provenance "${SMAC_PROVENANCE:-unspecified}" ;;
        *)      echo "Unknown: {{cmd}}. Try: verify strict scan" ;;
    esac

# === Datalinks (K1) ===

# Deterministic — no model, no tokens. Needs SMAC_DIR. Output is gitignored,
# being derived from copyrighted game data.
# Parse your SMAC install's alphax.txt into the canonical smac: graph
ingest out="datalinks/smac.ttl" brief="datalinks/briefing.txt":
    @mkdir -p "$(dirname "{{out}}")"
    @cd orchestrator && uv run neural-amplifier ingest "{{smac}}/alphax.txt" \
        --out "../{{out}}" --briefing "../{{brief}}"

# Tagged house-rule, NOT canonical — Thinker ships its own tech tree and the
# ingester refuses to label it otherwise. Needs THINKER_DIR.
# Regenerate the committed Thinker-sourced datalinks graph
ingest-thinker:
    @mkdir -p datalinks/thinker
    @cd orchestrator && uv run neural-amplifier ingest "{{thinker}}/docs/alphax.txt" \
        --engine thinker --tier house-rule \
        --out ../datalinks/thinker/alphax.ttl \
        --briefing ../datalinks/thinker/briefing.txt

# === Quipu (knowledge graph) ===

# Needs `quipu` built with --features shacl,onnx (scripts/setup-environment.sh).
# Loads the committed house-rule graph; add your own canonical one separately.
# Load the datalinks graph into a local Quipu store
quipu-load db=".quipu/na.db" ttl="datalinks/thinker/alphax.ttl":
    @mkdir -p "$(dirname "{{db}}")"
    quipu knot "{{ttl}}" --db "{{db}}"
    @quipu stats --db "{{db}}"

# Handy sanity query: everything a technology unlocks, with its tier.
# Query the local Quipu store: just quipu-ask '<sparql>'
quipu-ask sparql db=".quipu/na.db":
    @quipu read '{{sparql}}' --db "{{db}}"

# Point the orchestrator at it with NA_QUIPU_URL=http://127.0.0.1:3030.
# Serve the local Quipu store over REST for grounded retrieval
quipu-serve db=".quipu/na.db" bind="127.0.0.1:3030":
    quipu-server --db "{{db}}" --bind "{{bind}}"

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
