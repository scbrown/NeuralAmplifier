#!/usr/bin/env bash
# Deploy the dashboard from a clean release snapshot of origin/main, and restart it once.
#
# The dashboard used to run from the shared checkout and deploy by `git merge --ff-only`. Any
# local edit to a file that main also changes (the embedded item export is the usual one) then
# blocks every deployment forever, and the dashboard silently stays on an old revision, one
# that can fail on records a newer orchestrator writes. Now the checkout is only fetched, never
# changed. Each revision is exported with `git archive` into its own directory under
# $releases, synced there, and the `current` symlink is swapped atomically. The unit runs
# from `current`.
#
# One restart per new revision. If the verification after it fails, `current` goes back to the
# previous release with one more restart, and the failure is logged. Never stash, reset, force
# or rebase. Never loop restarts.
set -euo pipefail
repo=${NA_DASHBOARD_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/neural-amplifier"
releases=${NA_DASHBOARD_RELEASES:-${XDG_DATA_HOME:-$HOME/.local}/lib/neural-amplifier/dashboard-releases}
keep=${NA_DASHBOARD_KEEP_RELEASES:-3}
log_file="$state_dir/dashboard-deploy.log"
unit_name=neural-amplifier-dashboard.service
mkdir -p "$state_dir" "$releases"
log() {
    printf '%s %s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$log_file"
}
exec 9>"$state_dir/dashboard-deploy.lock"
if ! flock -n 9; then
    log "SKIP another dashboard deploy check is running"
    exit 0
fi

# --build-only: export and sync the release for origin/main and point `current` at it, without
# restarting anything (the installer uses this before the unit first starts).
build_only=false
[[ ${1:-} == --build-only ]] && build_only=true

cd "$repo"
if ! fetch_output=$(git fetch origin 2>&1); then
    log "FAIL fetch origin: $fetch_output"
    exit 1
fi
target=$(git rev-parse origin/main)
before=$(readlink "$releases/current" 2>/dev/null || true)
before=${before##*/}
if [[ $before == "$target" ]]; then
    exit 0
fi

release="$releases/$target"
# A revision that already failed verification is not retried on every tick (that would be a
# restart every five minutes). It waits for main to move; delete the marker to retry by hand.
if [[ -f $release/.verify-failed ]] && ! $build_only; then
    exit 0
fi
if [[ ! -f $release/.release-complete ]]; then
    rm -rf "$release.partial"
    mkdir -p "$release.partial"
    if ! archive_output=$(git archive "$target" | tar -x -C "$release.partial" 2>&1); then
        log "FAIL export $target: $archive_output"
        rm -rf "$release.partial"
        exit 1
    fi
    if ! sync_output=$(uv sync --frozen --directory "$release.partial/orchestrator" 2>&1); then
        log "FAIL uv sync for $target: $sync_output"
        rm -rf "$release.partial"
        exit 1
    fi
    printf '%s\n' "$target" >"$release.partial/.release-complete"
    rm -rf "$release"
    mv "$release.partial" "$release"
fi

point() {  # atomically repoint `current` at a release directory name
    ln -sfn "$1" "$releases/current.next"
    mv -T "$releases/current.next" "$releases/current"
}
point "$target"
if $build_only; then
    log "BUILT release $target (no restart)"
    exit 0
fi

log "DEPLOY release ${before:-none} -> $target; restarting $unit_name"
verify() {
    systemctl --user restart "$unit_name" && "$releases/current/scripts/install-dashboard-service.sh" check
}
if ! verify_output=$(verify 2>&1); then
    log "FAIL verify $target after one restart: $verify_output"
    date --utc +%Y-%m-%dT%H:%M:%SZ >"$release/.verify-failed"
    if [[ -n $before && -f $releases/$before/.release-complete ]]; then
        point "$before"
        if rollback_output=$(verify 2>&1); then
            log "ROLLBACK to $before: $rollback_output"
        else
            log "FAIL rollback to $before: $rollback_output"
        fi
    fi
    exit 1
fi
log "PASS deployed $target; $verify_output"

# Keep the newest $keep complete releases, and never the one `current` points at.
mapfile -t old < <(
    find "$releases" -mindepth 1 -maxdepth 1 -type d -name '[0-9a-f]*' -printf '%T@ %f\n' \
        | sort -rn | awk -v keep="$keep" 'NR > keep {print $2}'
)
for name in "${old[@]}"; do
    [[ $name == "$target" ]] || rm -rf "${releases:?}/$name"
done
