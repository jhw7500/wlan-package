#!/bin/bash

tag=$(basename "$0")
IFACE=${1:-}

LOGGER_COMMAND_LIB="${WIFI_LOGGER_COMMAND_LIB:-/usr/local/scripts/wifi_logger_command_lib.sh}"
if [ ! -r "$LOGGER_COMMAND_LIB" ]; then
    LOGGER_COMMAND_LIB="$(dirname "$0")/wifi_logger_command_lib.sh"
fi
# shellcheck source=wifi_logger_command_lib.sh
. "$LOGGER_COMMAND_LIB"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.crit "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

LOG_PATH="${WIFI_LOGGER_SNAPSHOT_PATH:-/var/log/cantops/stat/$IFACE/snap.log}"
INITIAL_DELAY_SEC="${WIFI_LOGGER_INITIAL_DELAY_SEC:-10}"
COMMAND_TIMEOUT_SEC="${WIFI_LOGGER_COMMAND_TIMEOUT_SEC:-5}"
SNAPSHOT_INTERVAL_SEC="${WIFI_LOGGER_SNAPSHOT_INTERVAL_SEC:-600}"
ONESHOT="${WIFI_LOGGER_ONESHOT:-0}"

mkdir -p "$(dirname "$LOG_PATH")" || exit 1
sleep "$INITIAL_DELAY_SEC"

append_command_failure() {
    local command_name=$1
    local rc=$2

    case "$rc" in
        124|137)
            printf '%s timeout\n' "$command_name"
            ;;
        *)
            printf '%s error (rc=%s)\n' "$command_name" "$rc"
            ;;
    esac
}

snapshot_once() {
    local ts wpa_output wpa_rc iw_output iw_rc

    logger -p local0.info "[$tag:$LINENO] [$IFACE] snapshot -> $LOG_PATH"
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    wpa_output=$(logger_run_bounded "$COMMAND_TIMEOUT_SEC" \
        wpa_cli -i "$IFACE" status 2>/dev/null)
    wpa_rc=$?
    iw_output=$(logger_run_bounded "$COMMAND_TIMEOUT_SEC" \
        iw dev "$IFACE" station dump 2>/dev/null)
    iw_rc=$?

    {
        printf '%s\n' "$ts"
        if [ "$wpa_rc" -eq 0 ]; then
            printf '%s\n' "$wpa_output" \
                | grep -E '^(bssid|freq|ssid|wpa_state)=' || true
        else
            append_command_failure wpa_cli "$wpa_rc"
        fi
        if [ "$iw_rc" -eq 0 ]; then
            printf '%s\n' "$iw_output"
        else
            append_command_failure iw "$iw_rc"
        fi
        printf '\n'
    } >> "$LOG_PATH" 2>&1
}

while true; do
    snapshot_once
    [ "$ONESHOT" = "1" ] && break
    sleep "$SNAPSHOT_INTERVAL_SEC"
done
