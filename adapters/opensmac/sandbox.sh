#!/usr/bin/env bash
# Run a command inside the OpenSMAC sandbox: no network, a tmpfs HOME and /tmp, the host system
# read-only, and only the work directory writable (mounted at /tmp/w). An optional game directory is
# mounted read-only at /tmp/game. Third-party OpenSMAC code is built and run only through this.
#
#   sandbox.sh WORK_DIR [GAME_DIR|-] -- command args...
set -euo pipefail
work=$(realpath "$1")
game=${2:--}
shift 2
[[ ${1:-} == -- ]] && shift
args=(
  --ro-bind / /
  --dev /dev
  --proc /proc
  --tmpfs /tmp
  --tmpfs "$HOME"
  --bind "$work" /tmp/w
  --unshare-all
  --die-with-parent
  --new-session
  --setenv HOME "$HOME"
  --chdir /tmp/w
)
if [[ $game != - ]]; then
  args+=(--ro-bind "$(realpath "$game")" /tmp/game)
fi
exec bwrap "${args[@]}" "$@"
