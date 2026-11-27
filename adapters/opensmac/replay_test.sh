#!/usr/bin/env bash
# Record/replay acceptance test for the OpenSMAC eval adapter.
#
#   replay_test.sh WORK_DIR GAME_DIR [TURNS]
#
# WORK_DIR holds the build from build.sh. GAME_DIR is the installed game (terranx.exe and its
# data). It is mounted read-only and never written. TURNS defaults to 300 on the std8 settings
# (standard map, 7 factions).
#
#  1. live:   play the game with the stub decider, writing one JSONL record per decision
#  2. replay: play it again from the log (no decider call). Every turn_NNN.{state,rng,sav}
#             must be sha256-identical to the live run
#  3. tamper: change inputs_sha256 on one record. Replay must exit 3 and name that record's turn
#  4. tamper: change one logged choice. Replay must stop matching the live run, either at a
#             hash divergence or at a differing snapshot, no earlier than that record's turn
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=$(realpath "$1")
game=$2
turns=${3:-300}
bin=smac/build/opensmac_headless
[[ -x $work/$bin ]] || { echo "no $work/$bin: run build.sh first" >&2; exit 1; }

if [[ ! -f $work/data_image.bin ]]; then
  "$here/sandbox.sh" "$work" "$game" -- \
    python3 -I smac/src/tools/gen_data_image.py /tmp/game/terranx.exe /tmp/w/data_image.bin
fi
cat >"$work/std$turns.cmd" <<EOF
seed 1003 2003
newgame size=2 ocean=0 erosion=2 clouds=2 orbit=0 diff=2 factions=pirates,drone,angels,fungboy,caretake,usurper,spartans
turns $turns
EOF

# run MODE LOG OUTDIR: one headless game in the sandbox, printing its exit status.
run() {
  rm -rf "${work:?}/$3"
  local status=0
  "$here/sandbox.sh" "$work" "$game" -- env NA_OPENSMAC_MODE="$1" NA_OPENSMAC_LOG="/tmp/w/$2" \
    "$bin" "/tmp/w/std$turns.cmd" --outdir "/tmp/w/$3" --gamedir /tmp/game \
    --image /tmp/w/data_image.bin --ini "/tmp/w/smac/harness/oracle/runtime/Alpha Centauri.Ini" \
    --original-bugs >"$work/$3.out" 2>"$work/$3.err" || status=$?
  echo "$status"
}

# digest DIR: the sorted sha256 list of every turn snapshot file.
digest() { (cd "$work/$1" && sha256sum turn_*.state turn_*.rng turn_*.sav | sort -k2); }

fail() { echo "FAIL: $*" >&2; exit 1; }

t0=$(date +%s.%N)
[[ $(run live live.jsonl live) == 0 ]] || fail "live run exited non-zero: $(tail -3 "$work/live.err")"
t1=$(date +%s.%N)
records=$(wc -l <"$work/live.jsonl")
echo "live:   $turns turns, $records records in $(echo "$t1 - $t0" | bc) s"
cut -d'"' -f8 "$work/live.jsonl" | sort | uniq -c | sed 's/^/        /'

[[ $(run replay live.jsonl replay) == 0 ]] || fail "replay exited non-zero: $(tail -3 "$work/replay.err")"
digest live >"$work/live.sha256"
digest replay >"$work/replay.sha256"
files=$(wc -l <"$work/live.sha256")
[[ $files -eq $((3 * (turns + 1))) ]] || fail "expected $((3 * (turns + 1))) snapshot files, found $files"
cmp -s "$work/live.sha256" "$work/replay.sha256" || fail "replay snapshots differ: $(diff "$work/live.sha256" "$work/replay.sha256" | head -4)"
grep -q ' 0 unused' "$work/replay.err" || fail "replay left records unused: $(tail -1 "$work/replay.err")"
echo "replay: $files snapshot files sha256-identical ($(sha256sum <"$work/live.sha256" | cut -c1-16)), $(tail -1 "$work/replay.err")"

# Tamper 1: a record in the middle of the game gets a wrong inputs_sha256.
line=$((records / 2))
turn=$(sed -n "${line}p" "$work/live.jsonl" | sed 's/.*"turn":\([0-9]*\).*/\1/')
awk -v n="$line" 'NR == n { sub(/"inputs_sha256":"[0-9a-f]/, "\"inputs_sha256\":\"x") } { print }' \
  "$work/live.jsonl" >"$work/tamper_hash.jsonl"
status=$(run replay tamper_hash.jsonl tamper_hash)
[[ $status == 3 ]] || fail "tampered hash: expected exit 3, got $status"
grep -q "NA-REPLAY DIVERGENCE at turn $turn: inputs_sha256 mismatch" "$work/tamper_hash.err" ||
  fail "tampered hash did not fail at turn $turn: $(cat "$work/tamper_hash.err")"
echo "tamper: inputs_sha256 edit on record $line failed loudly at turn $turn (exit 3)"

# Tamper 2: a base_hurry record late in the game spends nothing instead of its logged amount.
# This is legal, so the game itself must diverge.
line=$(awk -v h="$((records / 2))" \
  'NR > h && /"surface":"base_hurry"/ && !/"choice":0,/ { print NR; exit }' "$work/live.jsonl")
[[ -n $line ]] || fail "no non-zero base_hurry record after the midpoint"
turn=$(sed -n "${line}p" "$work/live.jsonl" | sed 's/.*"turn":\([0-9]*\).*/\1/')
awk -v n="$line" 'NR == n { sub(/"choice":[0-9-]*/, "\"choice\":0") } { print }' \
  "$work/live.jsonl" >"$work/tamper_choice.jsonl"
status=$(run replay tamper_choice.jsonl tamper_choice)
digest tamper_choice >"$work/tamper_choice.sha256" 2>/dev/null || true
first=$(join -j 2 -o 0,1.1,2.1 -a 1 "$work/live.sha256" "$work/tamper_choice.sha256" |
  awk '$2 != $3 { sub(/[.].*/, "", $1); print $1; exit }' || true)
if [[ $status == 3 ]]; then
  at=$(grep -o 'DIVERGENCE at turn [0-9]*' "$work/tamper_choice.err" | grep -o '[0-9]*$')
  [[ $at -ge $turn ]] || fail "choice edit at turn $turn diverged earlier, at turn $at"
  echo "tamper: choice edit on record $line (turn $turn) failed loudly at turn $at (exit 3); first differing snapshot ${first:-none}"
else
  [[ -n $first ]] || fail "choice edit at turn $turn changed nothing"
  echo "tamper: choice edit on record $line (turn $turn) diverged at $first (exit $status)"
fi
echo PASS
