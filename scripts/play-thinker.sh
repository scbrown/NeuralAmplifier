#!/usr/bin/env bash
#
# One command: build our Thinker fork, install it over a real SMAC install, and
# launch the game with the Neural Amplifier gate enabled.
#
#   scripts/play-thinker.sh              # launch on your current display
#   scripts/play-thinker.sh headless     # launch on a virtual display (Xvfb)
#   scripts/play-thinker.sh build        # build and install, don't launch
#   scripts/play-thinker.sh restore      # put the stock Thinker build back
#
# Run scripts/setup-host.sh once first for the toolchain.
#
# Two things this is careful about, because both are easy to get wrong and
# expensive to debug:
#
#   1. **It never touches Steam's Proton prefix.** It creates its own Wine prefix,
#      so the game you launch from Steam and the game this launches stay
#      independent. A prefix upgraded by a different Wine version is not
#      reversible.
#
#   2. **It backs up the stock Thinker build before overwriting it, once.** The
#      backup is the *original* files, never a previous run's output — so
#      `restore` always gets you back to upstream, however many times you rebuild.
#
set -euo pipefail

log()  { printf '\033[1m== %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$1" >&2; }
die()  { printf '\033[31mxx %s\033[0m\n' "$1" >&2; exit 1; }

cmd="${1:-launch}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
THINKER_DIR="${THINKER_DIR:-$REPO_ROOT/../thinker}"
WINEPREFIX="${NA_WINEPREFIX:-$HOME/.local/share/na-wine}"
BACKUP_SUBDIR="na-backup-stock"

# ── Find the game ───────────────────────────────────────────────────────────
#
# This is the *play* directory: a real install with the mod on top. Deliberately
# distinct from $SMAC_DIR, the pristine fixture — mixing them is what
# contaminates the canonical alphax.txt (docs/headless-harness.md §2.4).
find_play_dir() {
    if [ -n "${SMAC_PLAY_DIR:-}" ]; then
        printf '%s' "$SMAC_PLAY_DIR"; return
    fi
    local candidates=(
        "$HOME/.steam/steam/steamapps/common/Sid Meier's Alpha Centauri"
        "$HOME/.local/share/Steam/steamapps/common/Sid Meier's Alpha Centauri"
        "$HOME/.steam/root/steamapps/common/Sid Meier's Alpha Centauri"
    )
    # Any additional Steam library folders the user has configured.
    local vdf="$HOME/.steam/steam/steamapps/libraryfolders.vdf"
    if [ -f "$vdf" ]; then
        while read -r p; do
            candidates+=("$p/steamapps/common/Sid Meier's Alpha Centauri")
        done < <(grep -oP '"path"\s*"\K[^"]+' "$vdf" 2>/dev/null || true)
    fi
    for d in "${candidates[@]}"; do
        [ -f "$d/terranx.exe" ] && { printf '%s' "$d"; return; }
    done
    return 1
}

PLAY_DIR="$(find_play_dir)" || die "no SMAC install found — set SMAC_PLAY_DIR to the directory holding terranx.exe"
log "game:    $PLAY_DIR"
log "fork:    $THINKER_DIR"
log "prefix:  $WINEPREFIX"

# ── restore ─────────────────────────────────────────────────────────────────
if [ "$cmd" = "restore" ]; then
    B="$PLAY_DIR/$BACKUP_SUBDIR"
    [ -d "$B" ] || die "no backup at $B — nothing to restore"
    for f in thinker.dll thinker.exe thinker.ini; do
        [ -f "$B/$f" ] && cp -p "$B/$f" "$PLAY_DIR/$f" && echo "  restored $f"
    done
    log "stock Thinker restored"
    exit 0
fi

# ── Build ───────────────────────────────────────────────────────────────────
[ -d "$THINKER_DIR/src" ] || die "no Thinker checkout at $THINKER_DIR (clone your fork, or set THINKER_DIR)"
command -v i686-w64-mingw32-g++ >/dev/null || die "cross-compiler missing — run scripts/setup-host.sh"

log "building thinker (release)"
# `cmake --preset` resolves CMakePresets.json relative to the working directory,
# not to -S, so this has to run from inside the checkout.
( cd "$THINKER_DIR" && cmake --preset release >/dev/null \
    && cmake --build --preset release --parallel "$(nproc)" >/dev/null )

BUILT_DLL="$THINKER_DIR/build/release/thinker.dll"
BUILT_EXE="$THINKER_DIR/build/release/thinker.exe"
[ -f "$BUILT_DLL" ] || die "build produced no thinker.dll"

# Confirm we cross-compiled rather than accidentally building a host binary —
# CMakeLists sets the compiler after project(), which is fragile enough to check.
case "$(file -b "$BUILT_DLL")" in
    *"PE32"*"80386"*|*"PE32"*"Intel i386"*) : ;;
    *) die "built thinker.dll is not a 32-bit Windows DLL: $(file -b "$BUILT_DLL")" ;;
esac
echo "  $(cd "$THINKER_DIR" && git log --oneline -1)"

# ── Install, backing up the stock build exactly once ────────────────────────
B="$PLAY_DIR/$BACKUP_SUBDIR"
mkdir -p "$B"
built_dll_sha="$(sha1sum "$BUILT_DLL" | cut -d' ' -f1)"
for f in thinker.dll thinker.exe thinker.ini; do
    if [ ! -f "$B/$f" ] && [ -f "$PLAY_DIR/$f" ]; then
        # Refuse to record one of our own builds as "stock". Without this, a build
        # installed by hand before the first scripted run gets frozen in as the
        # restore point, and `restore` then silently restores our DLL forever.
        if [ "$f" = "thinker.dll" ] \
        && [ "$(sha1sum "$PLAY_DIR/$f" | cut -d' ' -f1)" = "$built_dll_sha" ]; then
            warn "installed thinker.dll is already our build — not recording it as stock."
            warn "Reinstall upstream Thinker into $B/ if you want a working 'restore'."
            break
        fi
        cp -p "$PLAY_DIR/$f" "$B/$f"
        echo "  backed up stock $f"
    fi
done
cp "$BUILT_DLL" "$PLAY_DIR/thinker.dll"
[ -f "$BUILT_EXE" ] && cp "$BUILT_EXE" "$PLAY_DIR/thinker.exe"
log "installed our build"

# ── Configure the gate ──────────────────────────────────────────────────────
#
# Without llm_factions the build behaves exactly like stock Thinker, so an
# unconfigured ini means "nothing to see" rather than an error. Add it if absent
# and leave any existing value alone — the operator's choice wins.
INI="$PLAY_DIR/thinker.ini"
if [ -f "$INI" ] && ! grep -q '^llm_factions' "$INI"; then
    printf '\n; ****** Neural Amplifier ******\n; Bitmask of faction ids routed to the LLM orchestrator (bit N = faction N).\nllm_factions=254\nllm_endpoint=http://127.0.0.1:8000\n' >> "$INI"
    log "added llm_factions=254 to thinker.ini"
fi

# ── Resolution sanity ───────────────────────────────────────────────────────
#
# video_mode=0 is fullscreen at the native desktop resolution. On a 4K display
# that renders SMAC's fixed-size UI at a quarter of its intended scale — legible
# in a screenshot, unusable to play. The game is not wrong and neither is the
# setting; they just combine badly, so say so rather than let it surprise someone.
if [ -f "$INI" ] && grep -q '^video_mode=0' "$INI"; then
    width=""
    if command -v xrandr >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
        width="$(DISPLAY="$DISPLAY" xrandr 2>/dev/null \
            | grep -oP '\bconnected primary \K[0-9]+' | head -1)"
    fi
    if [ -n "$width" ] && [ "$width" -gt 2560 ]; then
        warn "video_mode=0 (fullscreen at native ${width}px) will render the UI very small."
        warn "For a playable window, set in $INI:"
        warn "    video_mode=2      # borderless windowed"
        warn "    window_width=2560"
        warn "    window_height=1440"
    fi
fi

[ "$cmd" = "build" ] && { log "built and installed; not launching"; exit 0; }

# ── Launch ──────────────────────────────────────────────────────────────────
export WINEPREFIX
export WINEDEBUG="${WINEDEBUG:--all}"
command -v wine >/dev/null || die "wine missing — run scripts/setup-host.sh"

if [ ! -d "$WINEPREFIX" ]; then
    log "creating wine prefix (first run, takes a moment)"
    wineboot -u >/dev/null 2>&1 || warn "wineboot reported an error; continuing"
fi

OBS="$PLAY_DIR/na-observations.jsonl"
before=0
[ -f "$OBS" ] && before="$(wc -l < "$OBS")"

cd "$PLAY_DIR"
if [ "$cmd" = "headless" ]; then
    command -v xvfb-run >/dev/null || die "xvfb-run missing — run scripts/setup-host.sh"
    log "launching on a virtual display (Xvfb)"
    xvfb-run -a --server-args="-screen 0 1280x1024x24" wine thinker.exe || true
else
    log "launching on display ${DISPLAY:-<none>}"
    wine thinker.exe || true
fi

# ── Report ──────────────────────────────────────────────────────────────────
after=0
[ -f "$OBS" ] && after="$(wc -l < "$OBS")"
new=$(( after - before ))
echo
if [ "$new" -gt 0 ]; then
    log "$new new observation(s) in $OBS"
    tail -n 3 "$OBS"
else
    warn "no new observations in $OBS"
    cat <<'EOF'
   The gate only fires at a real decision point, so a session that never got
   past the main menu produces nothing. Start or load a game and end a turn.
   If you did play a turn, check that llm_factions in thinker.ini covers the
   faction you were playing, and that manage_player_bases=1.
EOF
fi
