#!/usr/bin/env bash
#
# bisect-thinker-launch.sh — classify the thinker checkout at HEAD as good or bad
# for the "does the game reach a live session at launch" question (na-nnn).
#
# Written for `git bisect run`, so it obeys that contract:
#   exit 0   good  — the autoload flow reached a session and loaded the save
#   exit 1   bad   — the flow gave up ("no session after quickstart"), or the run
#                    produced no session inside the timeout
#   exit 125 skip  — the tree would not build; the commit says nothing either way
#
# It NEVER touches the Steam install or the live game's wine prefix. Three
# separate isolations, all of them load-bearing while a real game is running on
# this host:
#
#   1. SMAC_PLAY_DIR is a staged COPY of the install. The script installs a DLL
#      over it on every iteration; doing that to the play directory would clobber
#      the running game's binary mid-session.
#   2. Its own WINEPREFIX. The launcher waits with `wineserver -w`, which blocks
#      until every process in the prefix exits — in the live prefix that is a wait
#      on the human's game, and the timeout kill that followed would land there too.
#   3. The staged copy's thinker.ini points llm_endpoint at a dead port. A bisect
#      run reaching the real orchestrator on :8000 would inject decisions into the
#      live session's record.
#
# The verdict is read from na-observations.jsonl, not from an exit code:
# thinker.exe is an injector and discards the game's status (play-thinker.sh
# documents this), so the run's own log is the only thing that knows what
# happened. na_autoload_tick writes "loaded" when GameHalted clears and the save
# takes, and "give_up" when it does not within its own 40s budget.
set -uo pipefail

WT="${THINKER_DIR:-$HOME/workspace/thinker-wt/harding}"
PLAY="${SMAC_PLAY_DIR:?set SMAC_PLAY_DIR to the STAGED copy, never the Steam install}"
export WINEPREFIX="${NA_WINEPREFIX:-$HOME/.local/share/na-wine-bisect}"
SAVE="${NA_BISECT_SAVE:-saves/auto/Autosave_2200.sav}"
TIMEOUT="${NA_BISECT_TIMEOUT:-240}"
LOGDIR="${NA_BISECT_LOGDIR:-/tmp/na-bisect}"
SCREEN="${NA_BISECT_SCREEN:-2560x1440x24}"

mkdir -p "$LOGDIR"
sha="$(git -C "$WT" rev-parse --short HEAD)"
log="$LOGDIR/$sha.log"

say() { printf '[bisect %s] %s\n' "$sha" "$1"; }

case "$PLAY" in
    *"steamapps"*) say "REFUSING: SMAC_PLAY_DIR points inside a Steam install"; exit 125 ;;
esac
[ -f "$PLAY/terranx.exe" ] || { say "no terranx.exe in $PLAY"; exit 125; }

# ── build ───────────────────────────────────────────────────────────────────
say "building $(git -C "$WT" log --oneline -1)"
if ! ( cd "$WT" && cmake --preset release >>"$log" 2>&1 \
        && cmake --build --preset release --parallel "$(nproc)" >>"$log" 2>&1 ); then
    say "BUILD FAILED — skip (see $log)"
    exit 125
fi
dll="$WT/build/release/thinker.dll"
[ -f "$dll" ] || { say "build produced no thinker.dll — skip"; exit 125; }
case "$(file -b "$dll")" in
    *PE32*80386*|*PE32*"Intel i386"*) : ;;
    *) say "not a 32-bit PE DLL — skip"; exit 125 ;;
esac

# ── can this commit answer the question at all? ─────────────────────────────
#
# The verdict is read from na-observations.jsonl, which only exists because our
# fork writes it. Six commits in this range are UPSTREAM's (the second parent of
# the v5.5 merge) and carry no Neural Amplifier code: -na-autoload is not parsed,
# na_autoload_tick never runs, nothing drives the menu and nothing is logged. Such
# a run cannot be good or bad, only silent — and silence costs the full timeout
# before the classifier reaches the same conclusion.
#
# So ask the ARTIFACT whether the instrumentation is in it, the way play-thinker.sh
# asserts a flag is in the installed DLL: the flag's own UTF-16LE literal. This is
# a fact about the build, available in milliseconds, and it turns four wasted
# minutes into an immediate, correct `skip`.
if command -v strings >/dev/null; then
    if ! strings -el "$dll" 2>/dev/null | /usr/bin/grep -qxF -- "-na-autoload"; then
        say "no -na-autoload in the built DLL: upstream-side commit, no NA instrumentation"
        say "UNTESTABLE by this predicate — skip"
        exit 125
    fi
fi

cp "$dll" "$PLAY/thinker.dll"
[ -f "$WT/build/release/thinker.exe" ] && cp "$WT/build/release/thinker.exe" "$PLAY/thinker.exe"

# ── run ─────────────────────────────────────────────────────────────────────
#
# Launched in the background and watched, rather than simply waited on. The game
# never ends by itself here — the control runs both hit the hard timeout with
# rc=124 having long since answered the question — so waiting for the process is
# waiting for the timeout, four minutes an iteration whatever the verdict. The
# run's own log states the verdict the moment it is known (~25s for a load, ~60s
# for a give-up: 12s startup + 8s planetfall + na_autoload_tick's own 40s budget),
# so poll for it and stop. The timeout stays as the outer bound for a run that
# never says anything at all.
OBS="$PLAY/na-observations.jsonl"
: > "$OBS"
say "launching headless (timeout ${TIMEOUT}s, screen $SCREEN)"
( cd "$PLAY" && timeout --kill-after=30s "${TIMEOUT}s" \
    xvfb-run -a --server-args="-screen 0 $SCREEN" \
    bash -c 'wine thinker.exe "$@" && wineserver -w' _ \
    -na-autoload "$SAVE" ) >>"$log" 2>&1 &
runner=$!

verdict=""
waited=0
while [ "$waited" -lt "$TIMEOUT" ]; do
    if /usr/bin/grep -q '"event":"loaded"' "$OBS" 2>/dev/null;  then verdict=good; break; fi
    if /usr/bin/grep -q '"event":"give_up"' "$OBS" 2>/dev/null; then verdict=bad;  break; fi
    kill -0 "$runner" 2>/dev/null || break     # the run ended without saying either
    sleep 2
    waited=$(( waited + 2 ))
done
say "verdict after ${waited}s: ${verdict:-none yet}"

# Stop our own game, then wait for the launcher we backgrounded. Only ever our
# prefix: -k in the live prefix would kill the human's session.
WINEPREFIX="$WINEPREFIX" wineserver -k 2>/dev/null || true
kill "$runner" 2>/dev/null || true
wait "$runner" 2>/dev/null

cp "$OBS" "$LOGDIR/$sha.observations.jsonl" 2>/dev/null || true

# ── classify ────────────────────────────────────────────────────────────────
if [ "$verdict" = good ]; then
    say "GOOD — reached a session: $(/usr/bin/grep '"event":"loaded"' "$OBS" | tail -1)"
    exit 0
fi
if [ "$verdict" = bad ]; then
    say "BAD — $(/usr/bin/grep '"event":"give_up"' "$OBS" | tail -1)"
    exit 1
fi
# No verdict from the run's own log. Distinguish "the hook never even ran" (which
# is a broken harness, not a broken commit) from "it ran and stalled".
if ! /usr/bin/grep -q '"event":"hook_alive"' "$OBS" 2>/dev/null; then
    say "INDETERMINATE — the autoload hook never announced itself; skip (see $log)"
    exit 125
fi
say "BAD — hook ran but no session inside ${TIMEOUT}s"
exit 1
