#!/usr/bin/env bash
#
# Provision a Neural Amplifier development container.
#
# Point a Claude Code web environment's setup script at this file
# (https://claude.ai/settings/code), or run it on a fresh box. It is
# idempotent: every step skips work already done.
#
# Layered so a partial run is still useful — core tooling first, then the
# lanes. A lane that fails does not take the rest down; the summary at the end
# reports what is actually present, because a setup script that half-worked and
# exited 0 is worse than one that failed loudly.
#
set -uo pipefail

log() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

# ── Core: build, lint, test, track work ─────────────────────────────────────
log "Core tooling"

# `just` is the single entry point for every command in this repo (AGENTS.md).
# From npm rather than `cargo install just`: seconds instead of minutes, and it
# is the same binary.
have just || npm install -g rust-just

# Task tracking. npm, NOT `go install`: the Go route builds with
# CGO_ENABLED=0 and embedded Dolt needs CGO, so `bd init` then fails with
# "embedded Dolt requires a CGO build".
have bd || npm install -g @beads/bd

# Python env + deps for orchestrator/.
have uv || curl -LsSf https://astral.sh/uv/install.sh | sh

have pre-commit || pip install --quiet pre-commit

# ── Thinker lane: cross-compile the DLL, no game needed ─────────────────────
log "Thinker cross-compile toolchain"

# The fork's CMakePresets.json requires CMake >= 3.31, newer than Debian's and
# several CI images. pip is the quickest route to a current one.
if ! have cmake || [ "$(cmake --version | head -1 | cut -d. -f2)" -lt 31 ]; then
    pip install --quiet "cmake>=3.31"
fi
have i686-w64-mingw32-g++ || apt-get install -y -qq build-essential ninja-build \
    g++-mingw-w64-i686-posix

# ── Game lane: run SMAC unattended ──────────────────────────────────────────
# terranx.exe is a 32-bit Windows GUI binary, so this needs wine32 via i386
# multiarch, plus Xvfb — it still renders, it just has no visible display.
# Optional: everything above works without it.
log "Wine + Xvfb (optional; only for running a real game)"
if ! have wine; then
    dpkg --add-architecture i386 && apt-get update -qq
    # libgd3:i386 FIRST, on purpose. On Ubuntu noble, wine32:i386 pulls
    # libgphoto2-6t64:i386, which depends on libgd3:i386 that apt will not
    # auto-install — the whole transaction then dies with the famously
    # unhelpful "held broken packages" and no clue which package. Installing
    # libgd3:i386 explicitly resolves it; verified on this image.
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libgd3:i386 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine wine32:i386 xvfb \
        || echo "  wine unavailable — the harness lane will be skipped"
fi
# terranx.exe is 32-bit, so the prefix must be WINEARCH=win32; a default
# 64-bit prefix refuses to run it.
if have wine && [ ! -d "${WINEPREFIX:-$HOME/.wine32}" ]; then
    WINEARCH=win32 WINEPREFIX="${WINEPREFIX:-$HOME/.wine32}" wineboot -i >/dev/null 2>&1 || true
fi

# ── Knowledge lane: Quipu ───────────────────────────────────────────────────
log "Quipu (knowledge graph)"
#
# There is NO prebuilt binary: scbrown/quipu's releases are release-plz source
# tags with empty asset lists, so this compiles (~15 min cold, 4 cores).
#
# --features shacl,onnx is mandatory, not a preference:
#   shacl → enforces the anti-masquerade tier predicates at WRITE time. Without
#           it a Thinker house-rule stores as canonical SMAC, silently — the
#           exact bug docs/knowledge-architecture.md exists to prevent.
#   onnx  → the embedding runtime (all-MiniLM-L6-v2, 384-dim). Without it
#           quipu_context and quipu_hybrid_search degrade to SPARQL CONTAINS.
#
# quipu-server declares `required-features`, and a plain `cargo build`
# SILENTLY SKIPS it — exit 0, no warning, stale binary left in place. Quipu
# ships scripts/build-deploy-server.sh solely to kill that failure. Naming
# both bins explicitly is the cheap version of the same guard.
#
# Requires Rust >= 1.85; any recent toolchain is fine. (The 1.95 requirement
# that failed earlier was rusty-beads, not Quipu.)
if ! have quipu; then
    cargo install --locked --git https://github.com/scbrown/quipu \
        --features shacl,onnx --bin quipu --bin quipu-server \
        || echo "  quipu build failed — K2 retrieval will be unavailable"
fi
# ort loads ONNX Runtime dynamically, so a missing lib fails at first
# embedding call rather than at build time.
have quipu && (ldconfig -p | grep -q libgomp || apt-get install -y -qq libgomp1)

# ── Verify ──────────────────────────────────────────────────────────────────
log "Installed"
for tool in just bd uv pre-commit node cmake ninja i686-w64-mingw32-g++ wine quipu quipu-server; do
    if have "$tool"; then
        printf '  %-24s %s\n' "$tool" "$("$tool" --version 2>&1 | head -1)"
    else
        printf '  %-24s MISSING\n' "$tool"
    fi
done

cat <<'NEXT'

Next:
  just setup     # install the git hooks
  just check     # the full gate
  just test      # every component's tests
NEXT
