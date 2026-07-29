#!/usr/bin/env bash
#
# Cloud-environment setup script for Neural Amplifier.
#
# Paste the BODY of this file into the Setup script field of a Claude Code
# cloud environment (claude.ai/code → the cloud icon above the message box →
# Add/edit environment). The field takes a script, not a path, and it runs
# before the repo is available — so it cannot `bash scripts/setup-environment.sh`
# from the clone. This file is the version-controlled copy to paste from.
# https://code.claude.com/docs/en/cloud-environments#setup-scripts
#
# THREE HARD CONSTRAINTS, from the docs and from measuring this:
#
#   1. Must exit 0. A non-zero exit means the session FAILS TO START. Hence
#      `set -u` without `-e`, `|| true` on everything optional, and an
#      unconditional `exit 0`.
#   2. Must finish in ~5 minutes or the environment cache never builds — and
#      without the cache this runs on EVERY session instead of once. Quipu
#      alone is 4m19s measured, so the lanes below run in PARALLEL and the
#      total is the slowest lane, not the sum.
#   3. Needs network. Everything here is on the default Trusted allowlist
#      (crates.io, npmjs, archive.ubuntu.com); nothing needs Custom.
#
# The cache is a filesystem snapshot: installed files persist to later
# sessions, running processes do not. It rebuilds when this script changes or
# after ~7 days.
#
# NOT installed here — already in the base image: Rust, Python+uv+ruff+mypy+
# pytest, Node, ninja, git, jq, ripgrep.
# https://code.claude.com/docs/en/cloud-environments#installed-tools
#
set -u

log() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export DEBIAN_FRONTEND=noninteractive

# ── Lane 1: Quipu — the long pole, so start it first ────────────────────────
#
# There is no prebuilt binary: the repo's releases are release-plz source tags
# with empty asset lists. Measured 4m19s for a release build on 4 vCPUs.
#
# --features shacl,onnx is mandatory, not a preference:
#   shacl → enforces the anti-masquerade tier predicates at WRITE time. Without
#           it a Thinker house-rule stores as canonical SMAC, silently — the
#           exact failure docs/knowledge-architecture.md exists to prevent.
#   onnx  → the embedding runtime. Without it quipu_context and
#           quipu_hybrid_search degrade to SPARQL CONTAINS.
#
# Both bins are named explicitly because quipu-server declares
# `required-features` and a plain build SILENTLY SKIPS it — exit 0, no warning.
(
  have quipu || cargo install --locked --git https://github.com/scbrown/quipu \
      --features shacl,onnx --bin quipu --bin quipu-server >/dev/null 2>&1 || true
) &
QUIPU_PID=$!

# ── Lane 2: apt — cross-compiler, Wine, Xvfb ───────────────────────────────
(
  apt-get update -qq >/dev/null 2>&1 || true

  # Thinker builds a 32-bit Windows DLL; this cross-compiles on Linux with no
  # game present, and CI runs it on every push.
  apt-get install -y -qq build-essential g++-mingw-w64-i686-posix >/dev/null 2>&1 || true

  # Running the real game: terranx.exe is a 32-bit Windows GUI binary, so this
  # needs wine32 via i386 multiarch. Xvfb gives it a virtual display — it still
  # renders, it just has nobody watching (docs/headless-harness.md §1).
  #
  # libgd3:i386 goes FIRST, deliberately. wine32:i386 pulls libgphoto2-6t64:i386,
  # which depends on a libgd3:i386 that apt declines to auto-install, and the
  # whole transaction then dies with "E: Unable to correct problems, you have
  # held broken packages" — naming no package at all. Verified on Ubuntu 24.04.
  dpkg --add-architecture i386 >/dev/null 2>&1 && apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq libgd3:i386 >/dev/null 2>&1 || true
  apt-get install -y -qq wine wine32:i386 xvfb libgomp1 >/dev/null 2>&1 || true
) &
APT_PID=$!

# ── Lane 3: npm + pip — small, fast ────────────────────────────────────────
(
  # `just` is the single entry point for every command in this repo (AGENTS.md).
  # From npm rather than `cargo install just`: seconds, not minutes.
  have just || npm install -g rust-just >/dev/null 2>&1 || true

  # Task tracking. npm, NOT `go install`: the Go route builds with
  # CGO_ENABLED=0 and embedded Dolt needs CGO, so `bd init` then fails with
  # "embedded Dolt requires a CGO build".
  have bd || npm install -g @beads/bd >/dev/null 2>&1 || true

  have pre-commit || pip install --quiet --break-system-packages pre-commit >/dev/null 2>&1 || true

  # The Thinker fork's CMakePresets.json requires CMake >= 3.31, newer than
  # Ubuntu's package and several CI images.
  if ! have cmake || [ "$(cmake --version 2>/dev/null | head -1 | cut -d. -f2)" -lt 31 ]; then
      pip install --quiet --break-system-packages "cmake>=3.31" >/dev/null 2>&1 || true
  fi
) &
NPM_PID=$!

wait "$QUIPU_PID" "$APT_PID" "$NPM_PID" 2>/dev/null || true

# ── Post-install: things that need a lane to have finished ─────────────────
# terranx.exe is 32-bit, so the prefix must be WINEARCH=win32. A default
# 64-bit prefix will not run it, and the architecture cannot be changed after
# creation — the prefix has to be deleted and remade.
if have wine && [ ! -d "$HOME/.wine32" ]; then
    WINEARCH=win32 WINEPREFIX="$HOME/.wine32" wineboot -i >/dev/null 2>&1 || true
fi

# ── Verify: report what is actually present ────────────────────────────────
# A setup script that half-worked and exited 0 is worse than one that failed,
# so print the truth rather than assuming the installs above landed.
log "Installed"
for tool in just bd uv pre-commit cmake ninja i686-w64-mingw32-g++ wine quipu quipu-server; do
    if have "$tool"; then
        printf '  %-24s %s\n' "$tool" "$("$tool" --version 2>&1 | head -1)"
    else
        printf '  %-24s MISSING\n' "$tool"
    fi
done

# Never fail the session: a missing optional tool degrades a lane, it does not
# stop Claude from working on everything else.
exit 0
