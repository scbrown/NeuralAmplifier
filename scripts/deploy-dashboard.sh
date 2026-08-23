#!/usr/bin/env bash
# Fast-forward the shared checkout and restart the dashboard exactly once when main advances.
set -euo pipefail

repo=${NA_DASHBOARD_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/neural-amplifier"
log_file="$state_dir/dashboard-deploy.log"
unit_name=neural-amplifier-dashboard.service
mkdir -p "$state_dir"

log() {
    printf '%s %s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$log_file"
}

exec 9>"$state_dir/dashboard-deploy.lock"
if ! flock -n 9; then
    log "SKIP another dashboard deploy check is running"
    exit 0
fi

cd "$repo"
before=$(git rev-parse HEAD)
if ! fetch_output=$(git fetch origin 2>&1); then
    log "FAIL fetch origin at $before: $fetch_output"
    exit 1
fi
target=$(git rev-parse origin/main)
if [[ $before == "$target" ]]; then
    exit 0
fi

# Intentionally no stash, reset, force, rebase, or conflict repair. A dirty checkout may still
# fast-forward when its edits do not overlap; otherwise git refuses and preserves every byte.
if ! merge_output=$(git merge --ff-only origin/main 2>&1); then
    log "FAIL ff-only $before -> $target; checkout preserved: $merge_output"
    exit 1
fi
after=$(git rev-parse HEAD)
if [[ $after == "$before" ]]; then
    log "FAIL origin/main moved to $target but HEAD stayed at $before"
    exit 1
fi

log "DEPLOY pulled $before -> $after; restarting $unit_name"
if ! systemctl --user restart "$unit_name"; then
    log "FAIL restart $unit_name at $after"
    exit 1
fi
if ! verify_output=$(scripts/install-dashboard-service.sh check 2>&1); then
    log "FAIL verify $after after one restart: $verify_output"
    exit 1
fi
log "PASS deployed $after; $verify_output"
