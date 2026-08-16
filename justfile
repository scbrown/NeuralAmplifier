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

# Pinned, and it must match the rev in .pre-commit-config.yaml. `just docs` and the
# pre-commit hook lint the same files with the same config, so a version skew between
# them means one gate passes and the other fails on a tree nobody changed — which is
# exactly what an unpinned `npx` did here: it floated to 0.23.2, picked up the new
# MD060, and left `just docs check` red against a green `just check` (na-hn6).
markdownlint := "markdownlint-cli2@0.23.2"

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
# Commands: build test wire
#   wire = the adapter's HTTP client, run under Wine against a real orchestrator.
#          Needs NO game, so it is the one adapter lane that can run in CI.
thinker cmd="build":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -d "{{thinker}}/src" ]; then
        echo "thinker: no checkout at {{thinker}} (set THINKER_DIR)."; exit 0
    fi
    case "{{cmd}}" in
        build)  (cd "{{thinker}}" && cmake --preset release >/dev/null \
                    && cmake --build build/release -j"$(nproc)") ;;
        test)   bash adapters/thinker/scripts/test.sh ;;   # runs SMAC under Wine
        wire)   NA_DIR="$(pwd)" bash "{{thinker}}/tests/run-na-tests.sh" --with-orchestrator ;;
        *)      echo "Unknown: {{cmd}}. Try: build test wire" ;;
    esac

# Deliberately starts NO tmux, pane or agent — the harness attaches itself, which is what
# keeps it swappable (docs/agent-play.md; the play-alpha-centauri skill is the other half).
# Serve decisions for an attached agent (Claude Code or any MCP client)
play cmd="serve" port="8000":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{cmd}}" in
      serve)
        NA_BRAIN=agent NA_DECISION_LOG=decisions.jsonl \
            uv run --directory orchestrator neural-amplifier serve --port {{port}} ;;
      # Everything that is silently wrong rather than loudly broken: the wrong brain (healthy
      # in every way except that you will never see a decision), an llm_timeout_ms no agent
      # can meet, and whether grounding and the guard are actually attached.
      check)
        uv run --directory orchestrator python ../scripts/play-preflight.py \
            --url "http://127.0.0.1:{{port}}" ;;
      *)
        echo "just play [serve|check] [port]" >&2; exit 2 ;;
    esac

# === Integration ===

# === Evals (behavioural, model in the loop) ===

# A behavioural question about the brain, not a unit test: what a decision does over many
# runs, which `just test` cannot assert. `score` reads a committed run and needs no model,
# no game and no sibling checkout; `prompts` regenerates the inputs and needs the rulebook.
# Behavioural evals: just eval list | prompts <id> | score <id> | check [<id>]
eval cmd="list" id="":
    #!/usr/bin/env bash
    set -euo pipefail
    # Resolved from the repo root, not from orchestrator/, so an absolute THINKER_DIR works.
    # The recipe used to prefix "../" unconditionally and turned /home/user/thinker into
    # ../home/user/thinker.
    links="$(cd "$(dirname "{{thinker}}/docs/alphax.txt")" 2>/dev/null && pwd)/alphax.txt" || links="{{thinker}}/docs/alphax.txt"
    uv run --directory orchestrator python ../evals/run.py {{cmd}} {{id}} --links "$links"

# === Coverage ===

# Answers "how much of the game can we see" from the frozen registry rather than from a
# document — the doc version had already drifted (docs/game-surface.md §2.5).
# Surface instrumentation: how much of the game surface emits a decision record
surfaces:
    @cd orchestrator && uv run neural-amplifier surfaces

# Refresh the tracked beads JSONL from the store, refusing to write a regression.
#
# Use this instead of `bd export`. bd's auto-export on write is GATED — a second write
# shortly after the first silently does not export (na-2a9) — and `bd export` with no -o
# writes to stdout and touches no file at all. Both leave the tracked tracker stale with
# no signal, which has cost two hand repairs on this repo.
beads-export:
    @scripts/beads-export.py

# Is the tracked JSONL current with the store? Exits 1 if not. Runs in pre-commit.
beads-check:
    @scripts/beads-export.py --check

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
#
# `stage` is the recurrence fix for na-8ie: it copies the PRISTINE tree to a play directory and
# overlays the mod THERE, so $SMAC_DIR stays vanilla. The original contamination — 17 tracked
# files overwritten, alphax.txt among them — happened because Thinker's install instructions and
# the fixture's requirements pointed at the same directory. Repairing that needs Steam; not
# doing it again needs this.
#
# It refuses to stage FROM a contaminated tree, refuses a target that contains or is contained
# by the source, and verifies alphax.txt is byte-identical afterwards — "I only read from it" is
# exactly what the original install also believed.
#
# Check, regenerate, or stage the SMAC fixture: just game verify|scan|stage
game cmd="verify" manifest="fixtures/smac/steam-2204130.manifest":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{cmd}}" in
        verify) python3 scripts/game_fixture.py verify "{{smac}}" --manifest "{{manifest}}" ;;
        strict) python3 scripts/game_fixture.py verify "{{smac}}" --manifest "{{manifest}}" --strict ;;
        scan)   python3 scripts/game_fixture.py scan "{{smac}}" --out "{{manifest}}" \
                    --provenance "${SMAC_PROVENANCE:-unspecified}" ;;
        stage)  python3 scripts/game_fixture.py stage "{{smac}}" \
                    "${SMAC_PLAY_DIR:-../smac-play}" \
                    --mod "${THINKER_BUILD:-{{thinker}}/build/develop}" \
                    --manifest "{{manifest}}" --force ;;
        *)      echo "Unknown: {{cmd}}. Try: verify strict scan stage" ;;
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

# Engine-mechanics grounding (Hank role a): promote the fork's code structure, then link the
# surfaces to the functions that decide them. Needs THINKER_DIR and a yupana built with
# `langs-extra` — without it the C++ is unparsed and the export is empty while exiting 0.
# Structure the engine's code, so "how does it actually score this" is a graph hop
code-graph db=".quipu/code.db":
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$(dirname "{{db}}")"
    yupana export "{{thinker}}/src" --repo thinker --format turtle > /tmp/na-thinker-code.ttl
    # Refuse an empty export rather than knotting nothing and reporting success — that is the
    # langs-extra failure, and it is silent by construction on yupana's side.
    test -s /tmp/na-thinker-code.ttl || { echo "empty export — is yupana built with langs-extra?" >&2; exit 1; }
    quipu knot /tmp/na-thinker-code.ttl --db "{{db}}"
    quipu knot datalinks/computed-by.ttl --db "{{db}}"
    # One line: `just` does not continue lines inside a shebang recipe, and a wrapped SPARQL
    # string reads to it as the start of a new recipe.
    quipu read 'PREFIX smac: <http://neuralamplifier.local/ontology/smac/> PREFIX bobbin: <http://aegis.gastown.local/ontology/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?name ?fn ?file WHERE { ?s rdfs:label ?name ; smac:computedBy ?sym . ?sym bobbin:name ?fn ; bobbin:definedIn ?mod . ?mod bobbin:filePath ?file . }' --db "{{db}}"

# Print yupana's signing identity and the exact registration to knot into Quipu.
#
# THE LAST STEP IS DELIBERATELY YOURS, NOT THIS RECIPE'S. Promotion signing is complete on
# yupana's side — key generation, signing, verdict spooling — but a signature only means
# something once its public key is registered in Quipu as an `aegis:VerifierRegistration`, and
# that registration IS the root of trust. A tool that minted a key and registered it for you
# would produce something that looks cryptographically trusted and is not: it would be asserting
# its own trustworthiness, which is the one claim a signature cannot make about itself.
#
# So this prints. You read it, and you knot it if you agree.
signing-identity key=".quipu/yupana-signing.pk8":
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p "$(dirname "{{key}}")"
    # Creates the key 0600 on first run; prints the existing one after that.
    pub=$(yupana verifier --key-path "{{key}}" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$pub" ]; then
        echo "yupana verifier produced no key — is yupana built with --features quipu?" >&2
        exit 1
    fi
    echo "yupana signing identity"
    echo "  key file   {{key}}   (private, 0600, never commit)"
    echo "  public key $pub"
    echo
    echo "To make signatures from this identity mean something, knot this into Quipu:"
    echo
    echo "@prefix aegis: <http://aegis.gastown.local/ontology/> ."
    echo "@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> ."
    echo
    echo "<http://aegis.gastown.local/ontology/verifier_yupana> a aegis:VerifierRegistration ;"
    echo "    rdfs:label \"yupana\" ;"
    echo "    aegis:publicKey \"$pub\" ."
    echo
    echo "Read it first. Registering a key is declaring what you trust, and nothing"
    echo "downstream can tell a key you vouched for from one that vouched for itself."

# Per-directive attention and override rates from a run's decision log — na-mmp.
#
# `just coverage` reports adherence in aggregate; this reports it PER DIRECTIVE, because the two
# failures worth catching are invisible in an average: a priority-7 directive overridden every
# time was mispriced, and one never overridden may be costing more than it admits. Both are
# properties of one directive across many decisions.
#
# `unmeasurable` — the world view did not report the directive's metric — is an ADAPTER GAP, not
# a directive that failed. It is excluded from the rates and reported separately, because the
# fix for the two is in different repositories.
#
# Refuses a log too short for a rate to mean anything. Ten replays of one captured observation
# show the mechanism works and say nothing about whether directives help.
directive-report log="decisions.jsonl":
    @scripts/directive_report.py "{{log}}"

# Compare two runs' trajectories — the A/B half of na-6db that needs no game.
#
# Producing the two logs needs a running game; reading them does not, which is why this exists
# now. Run the same save forward twice — llm_factions=0, then the brain — and point this at both
# observation logs.
#
# It REFUSES a comparison whose arms do not share a fairness profile, rather than reporting one
# with a caveat. An AI slot inherits handicaps a human slot does not (invariant 6), so comparing
# across that gap measures the handicap and not the brain — and yields a clean number with a
# confident sign, which is the dangerous kind of wrong. It also refuses a baseline containing
# llm decisions, a brain arm containing none, and a run shorter than 30 turns.
#
# It reports no verdict. One save's trajectory is evidence, not a result.
ab-outcomes baseline brain:
    @scripts/ab_outcomes.py "{{baseline}}" "{{brain}}"

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

# mdBook renders docs/ in place — see book.toml for why `src` points there
# rather than at a copy. `check` is the docs gate: lint, then prove it builds.
# Documentation: just docs <cmd>
# Commands: build serve lint fix fmt check
docs cmd="check":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{cmd}}" in
        build) mdbook build ;;
        serve) mdbook serve --open ;;
        lint)  npx --yes {{markdownlint}} "**/*.md" ;;
        fix)   npx --yes {{markdownlint}} --fix "**/*.md" ;;
        fmt)   npx prettier --write "**/*.md" --prose-wrap preserve ;;
        check) npx --yes {{markdownlint}} "**/*.md" && mdbook build ;;
        *)     echo "Unknown: {{cmd}}. Try: build serve lint fix fmt check" ;;
    esac
