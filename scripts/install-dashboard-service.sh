#!/usr/bin/env bash
# Install/check the read-only dashboard as a user service. No root and no system crontab.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
unit_name=neural-amplifier-dashboard.service
deploy_service=neural-amplifier-dashboard-deploy.service
deploy_timer=neural-amplifier-dashboard-deploy.timer
source_unit="$repo/deploy/systemd/$unit_name"
user_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/neural-amplifier"
local_lib_dir="${XDG_DATA_HOME:-$HOME/.local}/lib/neural-amplifier"
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
        # Carry forward every other NA_* setting rather than dropping it. This function
        # rewrites the file from scratch, so anything it does not know about is destroyed on
        # the next install — silently, because the dashboard then simply reports that feature
        # as unconfigured and looks healthy. That has already happened once:
        # NA_DASHBOARD_GAME_STATE was live in the file and is not written here, so a re-install
        # would have removed the all-player faction census with nothing to say why.
        # Preserving unknown keys is deliberately a MECHANISM and not a longer list, so the
        # next setting somebody adds is safe without this function having to learn about it.
        if [ -f "$environment_file" ]; then
            grep -E '^NA_[A-Z_]+=' "$environment_file" 2>/dev/null \
                | grep -vE '^NA_DASHBOARD_(PORT|LOG|STORE)=' || true
        fi
    } >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$environment_file"
}

check() {
    cmp -s "$source_unit" "$installed_unit" || {
        echo "FAIL: installed unit differs from $source_unit" >&2
        return 1
    }
    for companion in "$deploy_service" "$deploy_timer"; do
        cmp -s "$repo/deploy/systemd/$companion" "$user_unit_dir/$companion" || {
            echo "FAIL: installed $companion differs from repository" >&2
            return 1
        }
    done
    cmp -s "$repo/scripts/deploy-dashboard.sh" "$local_lib_dir/deploy-dashboard.sh" || {
        echo "FAIL: installed deploy script differs from repository" >&2
        return 1
    }
    systemctl --user is-enabled --quiet "$unit_name" || {
        echo "FAIL: $unit_name is not enabled" >&2
        return 1
    }
    systemctl --user is-enabled --quiet "$deploy_timer" || {
        echo "FAIL: $deploy_timer is not enabled" >&2
        return 1
    }
    systemctl --user is-active --quiet "$deploy_timer" || {
        echo "FAIL: $deploy_timer is not active" >&2
        return 1
    }
    local port main_pid control_group listener_pid
    port=$(sed -n 's/^NA_DASHBOARD_PORT=//p' "$environment_file")
    for _attempt in {1..10}; do
        main_pid=$(systemctl --user show "$unit_name" -p MainPID --value)
        if systemctl --user is-active --quiet "$unit_name" && [[ $main_pid != 0 ]] && \
            curl --fail --silent "http://127.0.0.1:$port/dashboard" >/dev/null; then
            break
        fi
        sleep 1
    done
    main_pid=$(systemctl --user show "$unit_name" -p MainPID --value)
    [[ $main_pid != 0 ]] || {
        echo "FAIL: $unit_name has no running main process" >&2
        return 1
    }
    control_group=$(systemctl --user show "$unit_name" -p ControlGroup --value)
    listener_pid=$(ss -H -ltnp "sport = :$port" | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
    [[ -n $listener_pid ]] && /usr/bin/grep -Fq "$control_group" "/proc/$listener_pid/cgroup" || {
        echo "FAIL: port $port is not owned by $unit_name (listener pid ${listener_pid:-none})" >&2
        return 1
    }
    curl --fail --silent --show-error \
        "http://127.0.0.1:$port/dashboard" \
        >/dev/null
    echo "PASS: $unit_name enabled, active, installed from $source_unit, dashboard HTTP 200"
}

case ${1:-} in
    install)
        mkdir -p "$user_unit_dir" "$config_dir" "$local_lib_dir"
        install -m 0644 "$source_unit" "$installed_unit"
        install -m 0644 "$repo/deploy/systemd/$deploy_service" "$user_unit_dir/$deploy_service"
        install -m 0644 "$repo/deploy/systemd/$deploy_timer" "$user_unit_dir/$deploy_timer"
        install -m 0755 "$repo/scripts/deploy-dashboard.sh" "$local_lib_dir/deploy-dashboard.sh"
        if [[ ! -f "$environment_file" ]]; then
            write_environment
        fi
        systemctl --user daemon-reload
        systemctl --user enable --now "$unit_name"
        systemctl --user enable --now "$deploy_timer"
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
