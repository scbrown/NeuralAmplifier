#!/usr/bin/env bash
#
# Local setup for a machine that has a real SMAC install — a "gaming host".
#
# The companion to setup-environment.sh, which targets Claude Code cloud
# environments: that one is pasted into a web form, runs as root with no repo
# present, and must exit 0 no matter what. This one is the opposite: run it from
# a clone, with sudo, and let it fail loudly.
#
# Installs only what Track A needs to build the Thinker fork and run the real
# game. Idempotent — safe to re-run.
#
set -euo pipefail

log()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    have sudo || { echo "need root or sudo" >&2; exit 1; }
    SUDO="sudo"
fi

# ── Cross-compiler ──────────────────────────────────────────────────────────
#
# Thinker is a 32-bit Windows DLL. Building it on Linux is officially supported
# (thinker/Technical.md §"Alternative ways to compile using CMake"), and its
# CMakeLists pins the compiler to i686-w64-mingw32-g++. GCC < 8.1.0 is
# unsupported upstream; Ubuntu's 13.2 is fine.
log "Cross-compiler (i686 MinGW)"
$SUDO apt-get update -qq
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential g++-mingw-w64-i686-posix

# ── Wine + Xvfb ─────────────────────────────────────────────────────────────
#
# terranx.exe is a 32-bit Windows GUI binary with a render loop: it cannot run
# truly headless. Xvfb gives it a virtual display so it renders with nobody
# watching, which is the property we actually need (docs/headless-harness.md §1).
#
# Note on prefix architecture: modern Wine (9+) runs 32-bit executables inside a
# default 64-bit prefix via new-wow64, and SMAC has been verified to boot that
# way here with wine 11.9. On older Wine a dedicated WINEARCH=win32 prefix is
# required, and the architecture cannot be changed after creation. play-thinker.sh
# creates its own prefix rather than reusing Steam's Proton prefix, so the game
# you play by hand and the game the harness drives can never corrupt each other.
log "Wine + Xvfb"
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y wine xvfb

# ── CMake ≥ 3.31 ────────────────────────────────────────────────────────────
#
# The fork's CMakePresets.json requires it, which is newer than several distro
# packages. pip is the quickest route that does not fight the package manager.
log "CMake"
cmake_minor="$(cmake --version 2>/dev/null | head -1 | cut -d. -f2 || echo 0)"
if ! have cmake || [ "${cmake_minor:-0}" -lt 31 ]; then
    pip install --quiet --break-system-packages "cmake>=3.31"
else
    echo "  cmake $(cmake --version | head -1 | awk '{print $3}') already sufficient"
fi

# ── Verify ──────────────────────────────────────────────────────────────────
log "Installed"
missing=0
for tool in cmake make i686-w64-mingw32-g++ wine Xvfb; do
    if have "$tool"; then
        printf '  %-24s %s\n' "$tool" "$("$tool" --version 2>&1 | head -1)"
    else
        printf '  %-24s MISSING\n' "$tool"
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    echo
    echo "Some tools are missing — see above." >&2
    exit 1
fi

cat <<'EOF'

Next:
  just thinker-play          # build the fork, install it, launch the game
  just thinker-play headless # same, on a virtual display (no window)

The game directory is found automatically, or set SMAC_PLAY_DIR.
Note that SMAC_PLAY_DIR (where you play, mod installed) is deliberately not the
same as SMAC_DIR (the pristine fixture) — docs/headless-harness.md §2.4.
EOF
