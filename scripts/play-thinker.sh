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
#   NA_AUTO_TURN=<secs> end our own turn after this long with no turn change
#   NA_EXIT_TURN=<n>    stop AT ABSOLUTE TURN n (docs/headless-harness.md §3.2)
#   NA_TIMEOUT=<secs>   kill the run if it has not stopped by then (§3.3)
#
# NA_AUTO_TURN is what makes the other two mean anything. A loaded save resumes
# at the PLAYER's turn and the engine then waits for the player, so without it an
# unattended run advances no turns — and since mod_turn_upkeep is only reached by
# ending a turn, NA_EXIT_TURN never fires either and NA_TIMEOUT is what stops the
# run. NA_EXIT_TURN is an absolute turn number, not a count: resuming a save at
# turn 44 and asking for 2 means "already past", not "two more turns".
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

# ── The adapter's half of invariant 9's agent exception (na-t3h) ────────────
#
# AGENTS.md invariant 9 carves out ONE exception: with NA_BRAIN=agent the game
# *does* wait, "the fallback is not removed, only made explicit: set
# NA_AGENT_TIMEOUT (and `llm_timeout_ms` on the adapter)". NOTHING SET IT. Before
# this, `llm_timeout_ms` appeared only in docs, comments and tests — never in a
# file the engine reads — so every agent run inherited main.h's default of 2500ms.
#
# 2500 is not a bug in itself: main.h picks it "to cover a Haiku call on a warm
# connection", which is right for the LLM tier and keeps the game from stalling on
# a dead orchestrator. It is wrong for the ONE brain the exception exists for. No
# attached agent answers in 2.5s, so an agent run could never win the race — and
# na-t3h measured what that looks like from outside: the adapter correctly logged
# `fallback_reason="orchestrator unreachable or slow"` while the orchestrator wrote
# a second, contradictory record marking the same decision tier=llm, applied,
# degraded=false. Silent, and flattering, which is the worst combination.
#
# So the timeout FOLLOWS THE BRAIN, which is the coupling AGENTS.md already
# requires ("the two waits are coupled, and must stay coupled"). Export NA_BRAIN
# the same way `just play` does and the adapter's deadline matches the orchestrator
# that is actually serving.
#
# Finite, not 0. `timeout_ms <= 0` means wait indefinitely, and an unattended run
# that hangs forever is the exact failure NA_TIMEOUT exists to catch (na-ie9) — a
# hung game and a working one are indistinguishable under Xvfb. 300000 is long
# enough for a human or an agent to think and short enough that the run still ends.
if [ -f "$INI" ] && ! grep -q '^llm_timeout_ms' "$INI"; then
    if [ -n "${NA_LLM_TIMEOUT_MS:-}" ]; then
        _timeout_ms="$NA_LLM_TIMEOUT_MS"; _why="NA_LLM_TIMEOUT_MS"
    elif [ "${NA_BRAIN:-}" = "agent" ]; then
        _timeout_ms=300000; _why="NA_BRAIN=agent"
    else
        _timeout_ms=2500; _why="default brain (main.h)"
    fi
    printf '; How long a decision may wait on the orchestrator before the engine applies its own\n; answer (invariant 9). Follows the brain: 2500 suits a fast model, an attached agent needs\n; minutes. Written explicitly because inheriting the built-in default silently broke agent\n; play (na-t3h).\nllm_timeout_ms=%s\n' "$_timeout_ms" >> "$INI"
    log "set llm_timeout_ms=$_timeout_ms in thinker.ini ($_why)"
    if [ "$_timeout_ms" = "2500" ] && [ -z "${NA_BRAIN:-}" ]; then
        warn "llm_timeout_ms=2500 suits a fast model, NOT an attached agent."
        warn "For agent play export NA_BRAIN=agent (as \`just play\` does) or set"
        warn "NA_LLM_TIMEOUT_MS — otherwise every decision degrades to Thinker while"
        warn "the orchestrator still reports it as an applied LLM-tier decision (na-t3h)."
    fi
else
    [ -f "$INI" ] && log "llm_timeout_ms already set in thinker.ini — leaving it ($(grep -m1 '^llm_timeout_ms' "$INI"))"
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
    log "run stops at absolute turn $NA_EXIT_TURN"
fi
if [ -n "${NA_AUTO_TURN:-}" ]; then
    case "$NA_AUTO_TURN" in
        ''|*[!0-9]*) die "NA_AUTO_TURN must be a positive integer (seconds), got '$NA_AUTO_TURN'" ;;
    esac
    [ "$NA_AUTO_TURN" -gt 0 ] || die "NA_AUTO_TURN must be a positive integer (seconds), got '$NA_AUTO_TURN'"
    exit_args+=(-na-auto-turn "$NA_AUTO_TURN")
    log "ending own turn after ${NA_AUTO_TURN}s with no turn change"
elif [ -n "${NA_EXIT_TURN:-}" ]; then
    # Worth saying out loud rather than letting the run time out looking healthy:
    # a bounded run that cannot advance a turn can never reach its bound.
    warn "NA_EXIT_TURN set without NA_AUTO_TURN — nothing will end a turn, so the"
    warn "limit can only be reached if something else is driving the game."
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
