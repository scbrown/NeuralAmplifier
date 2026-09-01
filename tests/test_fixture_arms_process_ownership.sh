#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
foreign_driver=""
foreign_game=""
cleanup() {
  [ -z "$foreign_driver" ] || kill -CONT "$foreign_driver" 2>/dev/null || true
  [ -z "$foreign_driver" ] || kill "$foreign_driver" 2>/dev/null || true
  [ -z "$foreign_game" ] || kill "$foreign_game" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$TMP/repo/scripts" "$TMP/play"
cp "$REPO/scripts/fixture_arms.sh" "$TMP/repo/scripts/fixture_arms.sh"

cat > "$TMP/repo/scripts/play-thinker.sh" <<'EOF'
#!/usr/bin/env bash
trap 'echo game-cont >> "$FIXTURE_EVENTS"' CONT
trap 'echo game-term >> "$FIXTURE_EVENTS"; exit 0' TERM
while :; do sleep 0.05; done
EOF
cat > "$TMP/repo/scripts/drive-unattended.py" <<'EOF'
import os
import signal
import time

events = os.environ["FIXTURE_EVENTS"]
def record(name):
    with open(events, "a", encoding="utf-8") as stream:
        stream.write(name + "\n")
signal.signal(signal.SIGCONT, lambda *_: record("driver-cont"))
signal.signal(signal.SIGTERM, lambda *_: (record("driver-term"), exit(0)))
while True:
    time.sleep(0.05)
EOF
chmod +x "$TMP/repo/scripts/fixture_arms.sh" "$TMP/repo/scripts/play-thinker.sh"

events="$TMP/events"
run_fixture() {
  FIXTURE_EVENTS="$events" THINKER_DIR="$TMP/thinker" \
    FIXTURE_START_DELAY=0.1 FIXTURE_TURN_DELAY=0.1 FIXTURE_SIGNAL_DELAY=0.05 \
    FIXTURE_CLEANUP_DELAY=0.1 FIXTURE_COMMAND_WAIT_ATTEMPTS=1 \
    FIXTURE_COMMAND_WAIT_DELAY=0.01 \
    "$TMP/repo/scripts/fixture_arms.sh" sample "$TMP/play" 1 "$@" >/dev/null
}

# A: an owned driver and game are sampled and cleaned up.
run_fixture 'owned:'
grep -q '^driver-cont$' "$events"
grep -q '^driver-term$' "$events"
grep -q '^game-term$' "$events"

# These deliberately look like the old global matches. They are not children of the fixture.
bash -c 'exec -a drive-unattended.py sleep 30' & foreign_driver=$!
bash -c 'exec -a terranx.exe sleep 30' & foreign_game=$!

# B: final cleanup with no owned processes leaves foreign matches alone.
run_fixture
kill -0 "$foreign_driver"
kill -0 "$foreign_game"

# C: foreign matches also survive while owned matches receive the expected signals.
: > "$events"
run_fixture 'mixed:'
kill -0 "$foreign_driver"
kill -0 "$foreign_game"
grep -q '^driver-cont$' "$events"
grep -q '^driver-term$' "$events"
grep -q '^game-term$' "$events"
echo "fixture arms signal only owned process groups: ok"
