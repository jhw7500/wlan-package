#!/bin/bash

wifi_init_json_key_exists() {
    local file="$1"
    local query="$2"

    [ -f "$file" ] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    jq -e "$query != null" "$file" >/dev/null 2>&1
}

wifi_init_json_read_raw() {
    local file="$1"
    local query="$2"

    jq -r "$query" "$file" 2>/dev/null
}

wifi_init_normalize_bool() {
    local value="${1:-}"
    local default="${2:-true}"
    local value_lc

    value_lc=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "$value_lc" in
        true|1|yes|on|enabled)
            printf 'true\n'
            ;;
        false|0|no|off|disabled)
            printf 'false\n'
            ;;
        *)
            printf '%s\n' "$default"
            ;;
    esac
}

wifi_init_get_iface_enabled() {
    local iface="$1"
    local default="${2:-true}"
    local value="$default"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local overlay_json="${JSON_FILE:-/usr/local/etc/config.json}"
    local query=".${iface}.enabled"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    if wifi_init_json_key_exists "$overlay_json" "$query"; then
        value=$(wifi_init_json_read_raw "$overlay_json" "$query")
    fi

    wifi_init_normalize_bool "$value" "$default"
}

wifi_init_get_iface_frequency() {
    local iface="$1"
    local default="${2:-auto}"
    local value="$default"
    local conf_json="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
    local overlay_json="${JSON_FILE:-/usr/local/etc/config.json}"
    local query=".${iface}.Frequency"

    if wifi_init_json_key_exists "$conf_json" "$query"; then
        value=$(wifi_init_json_read_raw "$conf_json" "$query")
    fi

    if wifi_init_json_key_exists "$overlay_json" "$query"; then
        value=$(wifi_init_json_read_raw "$overlay_json" "$query")
    fi

    if [ -z "$value" ] || [ "$value" = "null" ]; then
        value="$default"
    fi

    printf '%s\n' "$value"
}

wifi_init_iface_is_enabled() {
    [ "$(wifi_init_get_iface_enabled "$1" "${2:-true}")" = "true" ]
}
