#!/usr/bin/env bash
# Run one save-state fixture under several configurations and print the pod's position series.
#
#   scripts/fixture_arms.sh <fixture-name> <play-dir> <turns> "arm-label:cmd" "arm-label:cmd" ...
#
# The whole point of a fixture: every arm starts byte-identical, so a difference between arms is
# the CONFIGURATION and not the map. Before this, two runs were never the same experiment.
set -u
FIX="$1"; G="$2"; TURNS="${3:-8}"; shift 3
NA="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SP="$(dirname "$G")/.."
# The Thinker checkout to run the arms against. An env var with no default on purpose: this
# named one operator's worktree, which is both unusable on any other machine and an internal
# path in a PUBLIC repo. Unset is a loud failure below rather than a silent run against
# whatever the ambient environment happened to hold.
THINKER_DIR="${THINKER_DIR:-}"
if [ -z "$THINKER_DIR" ]; then
  echo "THINKER_DIR is unset — point it at your Thinker checkout, e.g." >&2
  echo "  THINKER_DIR=~/workspace/thinker $0 $*" >&2
  exit 2
fi

kill_all() {
  for p in $(ps -eo pid,args | grep -E 'drive-unattended|terranx.exe' | grep -v grep | awk '{print $1}'); do
    kill -CONT "$p" 2>/dev/null; kill "$p" 2>/dev/null
  done
  sleep 3
}

for arm in "$@"; do
  label="${arm%%:*}"; setup="${arm#*:}"
  echo "=== ARM $label   setup: ${setup:-none}"
  kill_all
  ( cd "$NA" && SMAC_PLAY_DIR="$G" NA_WINEPREFIX="$SP/lad1/wine" \
      THINKER_DIR="$THINKER_DIR" \
      NA_SAVE="evals/fixtures/$FIX.sav" NA_EXIT_TURN=999 NA_AUTO_TURN=20 NA_TIMEOUT=900 \
      setsid nohup ./scripts/play-thinker.sh headless > "/tmp/arm-$label.log" 2>&1 & )
  sleep 55
  if [ -n "$setup" ]; then
    rm -f "$G/na-command-result"; printf '%s' "$setup" > "$G/na-command"; sleep 4
    echo "    setup -> $(cat "$G/na-command-result" 2>/dev/null | head -c 120)"
  fi
  ( cd "$NA" && setsid nohup python3 scripts/drive-unattended.py "$G" "/tmp/arm-$label-turns.log" 900 \
      --no-viability > /dev/null 2>&1 & )
  for i in $(seq 1 "$TURNS"); do
    sleep 22
    D=$(ps -eo pid,args | grep 'drive-unattended.py' | grep -v grep | awk '{print $1}' | head -1)
    [ -z "$D" ] && break
    kill -STOP "$D"; sleep 1
    rm -f "$G/na-command-result"; printf 'move-stats' > "$G/na-command"
    for j in $(seq 1 10); do [ -f "$G/na-command-result" ] && break; sleep 1; done
    [ -f "$G/na-command-result" ] && python3 -c "
import json
d=json.load(open('$G/na-command-result')); t=dict(k.split('=',1) for k in d.get('detail','').split() if '=' in k)
print('    turn=%-4s pod=%-9s wp=%-9s spent=%-3s built=%-3s enroute=%-4s mode=%-2s ut=%s'
      % (d.get('turn'),t.get('pod'),t.get('wp'),t.get('spent'),t.get('colony_built'),
         t.get('enroute'),t.get('mode'),t.get('unit_turn')))"
    kill -CONT "$D"
  done
done
kill_all
echo "=== done"
