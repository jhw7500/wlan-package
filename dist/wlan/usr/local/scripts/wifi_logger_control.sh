#!/bin/bash
set -u

JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
SYSTEMCTL="${WIFI_LOGGER_SYSTEMCTL:-systemctl}"
APPLY_ENABLED="${WIFI_LOGGER_APPLY_ENABLED_SH:-/usr/local/scripts/wifi_apply_enabled.sh}"
SYNC_BIN="${WIFI_LOGGER_SYNC:-sync}"

usage() {
    echo "Usage: $0 {system|mlan0|mlan1|eth0} {start|stop|restart|status|enable|disable}" >&2
    return 2
}

valid_scope() {
    case "$1" in
        system|mlan0|mlan1|eth0) return 0 ;;
        *) return 1 ;;
    esac
}

valid_action() {
    case "$1" in
        start|stop|restart|status|enable|disable) return 0 ;;
        *) return 1 ;;
    esac
}

controller_for() {
    case "$1" in
        system) printf '%s\n' wifi_logger.service ;;
        mlan0|mlan1|eth0) printf 'wifi_logger@%s.service\n' "$1" ;;
        *) return 2 ;;
    esac
}

children_for() {
    local scope="$1" child
    case "$scope" in
        system)
            printf '%s\n' \
                wifi_logger_cpu.service \
                wifi_logger_mmc.service \
                wifi_logger_temp.service \
                wifi_logger_mcp.service \
                wifi_logger_summary.service
            ;;
        mlan0|mlan1)
            for child in link scan stat; do
                printf 'wifi_logger_%s@%s.service\n' "$child" "$scope"
            done
            printf 'wifi_link_snapshot@%s.service\n' "$scope"
            ;;
        eth0)
            printf '%s\n' wifi_logger_link@eth0.service
            ;;
    esac
}

warn_system_thermal() {
    echo "Warning: stopping or disabling system logging also stops temperature checks and overtemperature protection." >&2
}

read_policy() {
    local scope="$1"
    case "$scope" in
        system)
            jq -r 'if .logger.enabled == null then true else .logger.enabled end' "$JSON"
            ;;
        mlan0|mlan1|eth0)
            jq -r --arg scope "$scope" \
                'if .[$scope].logger.enabled == null then true else .[$scope].logger.enabled end' \
                "$JSON"
            ;;
    esac
}

read_parent_policy() {
    local scope="$1"
    case "$scope" in
        mlan0|mlan1)
            jq -r --arg scope "$scope" \
                'if .[$scope].enabled == null then false else .[$scope].enabled end' \
                "$JSON"
            ;;
        *) printf '%s\n' true ;;
    esac
}

show_unit() {
    local unit="$1" output active sub restarts
    output=$(
        "$SYSTEMCTL" show "$unit" \
            --property=ActiveState --property=SubState --property=NRestarts 2>/dev/null
    ) || output=""
    active=$(printf '%s\n' "$output" | awk -F= '$1 == "ActiveState" { print $2; exit }')
    sub=$(printf '%s\n' "$output" | awk -F= '$1 == "SubState" { print $2; exit }')
    restarts=$(printf '%s\n' "$output" | awk -F= '$1 == "NRestarts" { print $2; exit }')
    [ -n "$active" ] || active=unknown
    [ -n "$sub" ] || sub=unknown
    [ -n "$restarts" ] || restarts=0
    printf '%-42s %-10s %-12s restarts=%s\n' "$unit" "$active" "$sub" "$restarts"
    [ "$active" = "active" ]
}

status_scope() {
    local scope="$1" controller policy parent_policy policy_text enabled_text
    local controller_active=0 child_failed=0 child

    [ -f "$JSON" ] || {
        echo "Error: logger config not found: $JSON" >&2
        return 2
    }
    jq empty "$JSON" 2>/dev/null || {
        echo "Error: malformed logger config: $JSON" >&2
        return 2
    }

    controller=$(controller_for "$scope") || return 2
    policy=$(read_policy "$scope") || return 2
    parent_policy=$(read_parent_policy "$scope") || return 2

    if [ "$parent_policy" != "true" ]; then
        policy_text="blocked-by-interface"
    elif [ "$policy" = "true" ]; then
        policy_text="enabled"
    else
        policy_text="disabled"
    fi
    if "$SYSTEMCTL" is-enabled --quiet "$controller" 2>/dev/null; then
        enabled_text="enabled"
    else
        enabled_text="disabled"
    fi

    echo "scope=$scope policy=$policy_text systemd=$enabled_text"
    if show_unit "$controller"; then
        controller_active=1
    fi
    while IFS= read -r child; do
        if ! show_unit "$child"; then
            child_failed=1
        fi
    done < <(children_for "$scope")

    if [ "$controller_active" -eq 1 ] && { [ "$policy" != "true" ] || [ "$parent_policy" != "true" ]; }; then
        echo "state=runtime-override"
        [ "$child_failed" -eq 0 ] && return 0
        return 1
    fi
    if [ "$policy" != "true" ] || [ "$parent_policy" != "true" ]; then
        if [ "$scope" = "system" ]; then
            warn_system_thermal
        fi
        return 3
    fi
    if [ "$controller_active" -ne 1 ] || [ "$child_failed" -ne 0 ] || [ "$enabled_text" != "enabled" ]; then
        return 1
    fi
    return 0
}

render_policy() {
    local scope="$1" value="$2" src="$3" dst="$4"
    case "$scope" in
        system)
            jq --argjson value "$value" '.logger.enabled = $value' "$src" > "$dst"
            ;;
        mlan0|mlan1|eth0)
            jq --arg scope "$scope" --argjson value "$value" \
                '.[$scope].logger.enabled = $value' "$src" > "$dst"
            ;;
        *) return 2 ;;
    esac
}

update_policy() {
    local scope="$1" value="$2" dir tmp rc=0
    [ -f "$JSON" ] || {
        echo "Error: logger config not found: $JSON" >&2
        return 2
    }
    command -v jq >/dev/null 2>&1 || {
        echo "Error: jq not installed" >&2
        return 2
    }
    jq empty "$JSON" 2>/dev/null || {
        echo "Error: malformed logger config: $JSON" >&2
        return 2
    }

    dir=$(dirname "$JSON")
    tmp=$(mktemp "${JSON}.logger.XXXXXX") || return 1
    trap 'rm -f "$tmp"' RETURN

    if ! render_policy "$scope" "$value" "$JSON" "$tmp" || ! jq empty "$tmp" 2>/dev/null; then
        echo "Error: failed to render logger policy" >&2
        return 1
    fi
    chmod 0644 "$tmp" || return 1
    if [ "$(id -u)" -eq 0 ]; then
        chown root:root "$tmp" || return 1
    fi
    if ! "$SYNC_BIN" "$tmp"; then
        echo "Error: failed to sync staged logger policy" >&2
        return 1
    fi
    if ! mv -f "$tmp" "$JSON"; then
        echo "Error: failed to commit logger policy" >&2
        return 1
    fi
    trap - RETURN
    if ! "$SYNC_BIN" "$JSON" || ! "$SYNC_BIN" -d "$dir"; then
        echo "Error: logger policy committed but durability sync failed" >&2
        return 1
    fi
    if ! WIFI_APPLY_STRICT=1 "$APPLY_ENABLED"; then
        echo "Error: logger policy committed but systemd synchronization failed" >&2
        rc=1
    fi
    return "$rc"
}

runtime_action() {
    local scope="$1" action="$2" controller child failed=0
    controller=$(controller_for "$scope") || return 2
    case "$action" in
        start|stop)
            [ "$scope" = "system" ] && [ "$action" = "stop" ] && warn_system_thermal
            "$SYSTEMCTL" "$action" "$controller" || return 1
            ;;
        restart)
            while IFS= read -r child; do
                "$SYSTEMCTL" reset-failed "$child" || failed=1
            done < <(children_for "$scope")
            "$SYSTEMCTL" restart "$controller" || failed=1
            return "$failed"
            ;;
    esac
}

main() {
    local scope="${1:-}" action="${2:-}" value
    [ "$#" -eq 2 ] || {
        usage
        return 2
    }
    valid_scope "$scope" && valid_action "$action" || {
        usage
        return 2
    }
    case "$action" in
        start|stop|restart) runtime_action "$scope" "$action" ;;
        status) status_scope "$scope" ;;
        enable|disable)
            [ "$scope" = "system" ] && [ "$action" = "disable" ] && warn_system_thermal
            [ "$action" = "enable" ] && value=true || value=false
            update_policy "$scope" "$value"
            ;;
    esac
}

main "$@"
