#!/bin/sh

tag=$(basename "$0")

LOGGER_COMMAND_LIB="${WIFI_LOGGER_COMMAND_LIB:-/usr/local/scripts/wifi_logger_command_lib.sh}"
if [ ! -r "$LOGGER_COMMAND_LIB" ]; then
    LOGGER_COMMAND_LIB="$(dirname "$0")/wifi_logger_command_lib.sh"
fi
# shellcheck source=wifi_logger_command_lib.sh
. "$LOGGER_COMMAND_LIB"

CPU_LOG_INTERVAL=60
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    CPU_LOG_INTERVAL=$(jq -r '.logger.cpu_interval_sec // 60' "$WIFI_INIT_CONF_JSON")
fi

COMMAND_TIMEOUT_SEC="${WIFI_LOGGER_COMMAND_TIMEOUT_SEC:-5}"
CPU_SYSFS_ROOT="${WIFI_CPU_SYSFS_ROOT:-/sys/devices/system/cpu}"
ONESHOT="${WIFI_LOGGER_ONESHOT:-0}"

cleanup() {
    exit 0
}
trap cleanup INT TERM

for required_command in mpstat sar; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        logger -p local3.err "[$tag:$LINENO] $required_command not found; CPU monitoring disabled"
        exit 1
    fi
done

read_cpu_usage() {
    output=$(logger_run_bounded "$COMMAND_TIMEOUT_SEC" mpstat 1 1 2>/dev/null) \
        || return 1
    printf '%s\n' "$output" | awk '
        NF { idle=$NF }
        END {
            if (idle ~ /^[0-9]+([.][0-9]+)?$/) {
                printf "%.2f", 100-idle
            } else {
                exit 1
            }
        }
    '
}

read_mem_usage() {
    output=$(logger_run_bounded "$COMMAND_TIMEOUT_SEC" sar -r 0 2>/dev/null) \
        || return 1
    printf '%s\n' "$output" | awk '
        NF { used=$5 }
        END {
            if (used ~ /^[0-9]+([.][0-9]+)?$/) {
                printf "%s", used
            } else {
                exit 1
            }
        }
    '
}

while :; do
    cpu_usage=$(read_cpu_usage) || cpu_usage=unknown
    mem_usage=$(read_mem_usage) || mem_usage=unknown
    clks_log_part=""

    for cpu_dir in "$CPU_SYSFS_ROOT"/cpu[0-9]*; do
        [ -d "$cpu_dir" ] || continue
        core_id=${cpu_dir##*cpu}
        freq_file="$cpu_dir/cpufreq/cpuinfo_cur_freq"
        if [ -f "$freq_file" ]; then
            freq=$(logger_read_bounded "$COMMAND_TIMEOUT_SEC" "$freq_file" 2>/dev/null) \
                || freq=unknown
            clks_log_part="$clks_log_part, clk${core_id}:${freq}"
        fi
    done

    logger -p local3.debug "[$tag:$LINENO] CPU:$cpu_usage%, MEM:$mem_usage%${clks_log_part}"
    [ "$ONESHOT" = "1" ] && break
    sleep "$CPU_LOG_INTERVAL"
done
