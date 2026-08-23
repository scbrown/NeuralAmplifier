#!/usr/bin/env bash
# Install/check the read-only dashboard as a user service. No root and no system crontab.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
unit_name=neural-amplifier-dashboard.service
source_unit="$repo/deploy/systemd/$unit_name"
user_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/neural-amplifier"
installed_unit="$user_unit_dir/$unit_name"
environment_file="$config_dir/dashboard.env"

usage() {
    echo "usage: $0 install|check|set-run [LOG STORE [PORT]]" >&2
    exit 2
}

write_environment() {
    local log=${1:-$repo/orchestrator/decisions.jsonl}
    local store=${2:-$repo/orchestrator/worldviews}
    local port=${3:-8088}
    local temporary
    temporary=$(mktemp "$config_dir/dashboard.env.XXXXXX")
    {
        printf 'NA_DASHBOARD_PORT=%q\n' "$port"
        printf 'NA_DASHBOARD_LOG=%q\n' "$log"
        printf 'NA_DASHBOARD_STORE=%q\n' "$store"
    } >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$environment_file"
}

check() {
    cmp -s "$source_unit" "$installed_unit" || {
        echo "FAIL: installed unit differs from $source_unit" >&2
        return 1
    }
    systemctl --user is-enabled --quiet "$unit_name" || {
        echo "FAIL: $unit_name is not enabled" >&2
        return 1
    }
    systemctl --user is-active --quiet "$unit_name" || {
        echo "FAIL: $unit_name is not active" >&2
        return 1
    }
    curl --fail --silent --show-error \
        "http://127.0.0.1:$(sed -n 's/^NA_DASHBOARD_PORT=//p' "$environment_file")/dashboard" \
        >/dev/null
    echo "PASS: $unit_name enabled, active, installed from $source_unit, dashboard HTTP 200"
}

case ${1:-} in
    install)
        mkdir -p "$user_unit_dir" "$config_dir"
        install -m 0644 "$source_unit" "$installed_unit"
        if [[ ! -f "$environment_file" ]]; then
            write_environment
        fi
        systemctl --user daemon-reload
        systemctl --user enable --now "$unit_name"
        check
        ;;
    check)
        check
        ;;
    set-run)
        [[ $# -ge 3 && $# -le 4 ]] || usage
        mkdir -p "$config_dir"
        write_environment "$2" "$3" "${4:-8088}"
        systemctl --user restart "$unit_name"
        check
        ;;
    *)
        usage
        ;;
esac
