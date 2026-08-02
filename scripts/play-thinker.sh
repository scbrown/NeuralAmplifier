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
# For an UNATTENDED run, two knobs, and neither is optional in practice:
#
#   NA_EXIT_TURN=<n>    stop after n complete turns (docs/headless-harness.md §3.2)
#   NA_TIMEOUT=<secs>   kill the run if it has not stopped by then (§3.3)
#
# They are not redundant, which is why both exist. NA_EXIT_TURN is the game
# ending itself on a condition it can observe; NA_TIMEOUT is this script ending a
# game that can no longer observe anything. The second is what covers the failure
# NA_EXIT_TURN cannot: a run that hangs before the turn counter ever moves again.
# Under Xvfb both failures look identical from outside — a live process drawing
# nothing — so "treat a hung run as a failure, not a flake" needs a clock that is
# not the game's.
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

# ── Wine virtual desktop ────────────────────────────────────────────────────
#
# Without this, borderless windowed mode gives a window at the game's resolution
# while Wine maps pointer coordinates against the full desktop — so on a 4K screen
# running the game at 2560x1440 the cursor lands "waaay off" from where you click.
# Measured on the gaming host.
#
# A Wine virtual desktop at exactly the game's resolution makes Wine own the
# window and the coordinate space together, and the pointer lines up. It also
# keeps the game windowed, which is what you want if you're watching the decision
# log while playing.
if [ "$cmd" != "headless" ]; then
    res="$(grep -oP '^window_width=\K[0-9]+' "$INI" 2>/dev/null || true)x$(grep -oP '^window_height=\K[0-9]+' "$INI" 2>/dev/null || true)"
    if [ "$res" != "x" ] && [ -n "${NA_VIRTUAL_DESKTOP:-1}" ]; then
        wine reg add 'HKEY_CURRENT_USER\Software\Wine\Explorer' \
            /v Desktop /t REG_SZ /d Default /f >/dev/null 2>&1 || true
        wine reg add 'HKEY_CURRENT_USER\Software\Wine\Explorer\Desktops' \
            /v Default /t REG_SZ /d "$res" /f >/dev/null 2>&1 || true
        log "wine virtual desktop $res (set NA_VIRTUAL_DESKTOP= to disable)"
    fi
fi

OBS="$PLAY_DIR/na-observations.jsonl"
before=0
[ -f "$OBS" ] && before="$(wc -l < "$OBS")"

# ── Resume where you left off ───────────────────────────────────────────────
#
# The point of this is iteration: rebuilding the DLL means restarting the game, and
# restarting should not cost you your position. Thinker autosaves every turn when
# autosave_interval=1, so the newest saves/auto/Autosave_<year>.sav is the last
# completed turn — that is what we hand to -na-autoload.
#
# Newest by mtime, not by the year in the filename: a restarted or reloaded game can
# write a lower year than one already on disk, and mtime is what "where I left off"
# actually means. NA_RESUME=0 opts out and boots to the menu.
resume_args=()
if [ "${NA_RESUME:-1}" != "0" ]; then
    latest_save="$(ls -t "$PLAY_DIR"/saves/auto/*.sav 2>/dev/null | head -1 || true)"
    if [ -n "$latest_save" ]; then
        # The game resolves this relative to its own directory.
        rel="saves/auto/$(basename "$latest_save")"
        resume_args=(-na-autoload "$rel")
        log "resuming $rel ($(date -r "$latest_save" +%H:%M:%S))"
    else
        warn "no autosave found — booting to the main menu"
    fi
fi

# ── Bound the run ───────────────────────────────────────────────────────────
#
# NA_EXIT_TURN is forwarded, not inferred. It is deliberately NOT implied by
# `headless`: a headless run is one nobody is watching, which is not the same
# claim as one that should stop at a fixed turn, and an unattended run of
# indefinite length is a legitimate thing to ask for.
#
# The flag is also NOT part of what makes the DLL suppress error dialogs — that
# follows from -na-headless / -na-autoload alone (thinker src/neural.cpp
# na_headless). So a bounded run still shows its errors to whoever is sitting there.
exit_args=()
if [ -n "${NA_EXIT_TURN:-}" ]; then
    case "$NA_EXIT_TURN" in
        ''|*[!0-9]*) die "NA_EXIT_TURN must be a positive integer, got '$NA_EXIT_TURN'" ;;
    esac
    [ "$NA_EXIT_TURN" -gt 0 ] || die "NA_EXIT_TURN must be a positive integer, got '$NA_EXIT_TURN'"
    exit_args=(-na-exit-turn "$NA_EXIT_TURN")
    log "run bounded to $NA_EXIT_TURN complete turn(s)"
fi

# The timeout is the outer bound and applies to every mode. `timeout` sends TERM,
# then KILL after a grace period, because a wine process that is wedged in the way
# this exists to catch is exactly the kind that ignores TERM.
#
# Default off for an attended launch — a human at the keyboard is the timeout —
# and on for headless, where nobody will notice a hang. Set NA_TIMEOUT= to disable
# it explicitly even in headless.
run_timeout="${NA_TIMEOUT-}"
if [ -z "${NA_TIMEOUT+set}" ] && [ "$cmd" = "headless" ]; then
    run_timeout=1800
fi
timeout_args=()
if [ -n "$run_timeout" ]; then
    command -v timeout >/dev/null || die "timeout(1) missing — coreutils required for NA_TIMEOUT"
    timeout_args=(timeout --kill-after=30s "${run_timeout}s")
    log "hard timeout ${run_timeout}s (set NA_TIMEOUT= to disable)"
fi

# ── Wait for the GAME, not the launcher ─────────────────────────────────────
#
# thinker.exe is an injector, not a supervisor. It CreateProcess-es terranx.exe
# suspended, injects thinker.dll, resumes the thread, and `return 0` — see the
# fork's src/launch.cpp:110-118. It never calls WaitForSingleObject on the game,
# so it exits within a couple of seconds of a run that has barely started.
#
# Measured 2026-08-01, and this was silently breaking the headless lane outright:
#
#   timeout 120 xvfb-run -a … wine thinker.exe -na-autoload … -na-exit-turn 2
#   -> LAUNCHER rc=0 elapsed=3s
#      XIO: fatal IO error 2 on X server ":99" … explicit kill or server shutdown
#
# xvfb-run tears the display down as soon as ITS command returns, so the game died
# ~3s in, and na-observations.jsonl gained nothing. The autoload state machine
# waits 12s for engine startup to FINISH before it does anything, so it had not
# yet taken its first action. Every `play-thinker.sh headless` run was a
# three-second no-op that reported "no new observations" and looked like a
# configuration problem.
#
# Attended mode hid this: with no xvfb-run to kill the display the game survives
# as an orphan and plays on, so only the script's report was premature. That is
# why the bug reached here — the mode a human watches is the mode that works.
#
# `wineserver -w` blocks until every process in the prefix has exited, which is
# the wait thinker.exe declines to do. It is correct here specifically because the
# prefix is ours and holds nothing else (NA_WINEPREFIX, §3.0).
#
# The game's exit code is NOT recoverable this way — thinker.exe discarded it
# before we could see it, and wineserver reports on the prefix, not on a process.
# So NA_EXIT_TURN_LIMIT vs NA_EXIT_UNANSWERABLE has to be read from the run's own
# records in na-observations.jsonl, which is where the report below looks. The one
# outcome this layer CAN state by itself is the timeout, and that is the one the
# game cannot report about itself.
cd "$PLAY_DIR"
# `set -e` would exit here before the report runs, and the report is the point of
# the run. Capture the status instead and decide below.
rc=0
if [ "$cmd" = "headless" ]; then
    command -v xvfb-run >/dev/null || die "xvfb-run missing — run scripts/setup-host.sh"
    log "launching on a virtual display (Xvfb)"
    "${timeout_args[@]}" xvfb-run -a --server-args="-screen 0 1280x1024x24" \
        bash -c 'wine thinker.exe "$@" && wineserver -w' _ \
        "${resume_args[@]}" "${exit_args[@]}" || rc=$?
else
    log "launching on display ${DISPLAY:-<none>}"
    "${timeout_args[@]}" bash -c 'wine thinker.exe "$@" && wineserver -w' _ \
        "${resume_args[@]}" "${exit_args[@]}" || rc=$?
fi

# timeout(1) reports 124 when it fired, 137 when the KILL escalation was needed.
# Both mean the same thing to a harness: the run did not stop on its own.
timed_out=0
case "$rc" in 124|137) timed_out=1 ;; esac

# ── Report ──────────────────────────────────────────────────────────────────
after=0
[ -f "$OBS" ] && after="$(wc -l < "$OBS")"
new=$(( after - before ))
echo

# How the run ENDED is a separate question from what it produced, and reporting
# only the second is what let a three-second no-op read as a configuration
# problem for as long as it did. So say the ending first, and say it from
# evidence: the timeout is ours to know, and the turn limit is the DLL's, written
# to the log by na_exit_turn_check at the moment it decided to stop.
if [ "$timed_out" = "1" ]; then
    warn "run did NOT stop on its own — killed by the ${run_timeout}s timeout"
    warn "Treat this as a failure, not a flake (docs/headless-harness.md §3.3)."
    warn "Artifacts produced before the kill are kept; they are the diagnosis."
elif [ -n "${NA_EXIT_TURN:-}" ] \
  && [ -f "$OBS" ] && tail -n 40 "$OBS" 2>/dev/null | grep -q '"surface_id":"na.exit_turn"'; then
    log "run stopped itself at the turn limit"
    tail -n 40 "$OBS" | grep '"surface_id":"na.exit_turn"' | tail -1
elif [ -n "${NA_EXIT_TURN:-}" ]; then
    warn "run ended without reaching -na-exit-turn $NA_EXIT_TURN"
    warn "It stopped for some other reason — check the tail of $OBS."
fi

# A suppressed dialog is the other way an unattended run ends early, and it is
# invisible unless looked for: na_message_box writes the record and returns, and
# the caller's own next statement decides whether the process dies.
if [ -f "$OBS" ] && tail -n 40 "$OBS" 2>/dev/null | grep -q '"surface_id":"na.headless"'; then
    warn "a dialog was suppressed during this run:"
    tail -n 40 "$OBS" | grep '"surface_id":"na.headless"' | tail -3
fi

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
