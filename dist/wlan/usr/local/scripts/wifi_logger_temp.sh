#!/bin/bash

tag=$(basename "$0")

LOGGER_COMMAND_LIB="${WIFI_LOGGER_COMMAND_LIB:-/usr/local/scripts/wifi_logger_command_lib.sh}"
if [ ! -r "$LOGGER_COMMAND_LIB" ]; then
    LOGGER_COMMAND_LIB="$(dirname "$0")/wifi_logger_command_lib.sh"
fi
# shellcheck source=wifi_logger_command_lib.sh
. "$LOGGER_COMMAND_LIB"

cleanup() {
    exit 0
}
trap cleanup INT TERM

CPU_TEMP=unknown
CPU_TEMP_VALID=0
MLAN0_TEMP=unknown
MLAN0_TEMP_VALID=0
MLAN0_PRESENT=0
MLAN1_TEMP=unknown
MLAN1_TEMP_VALID=0
MLAN1_PRESENT=0
LOG_LEVEL=info
max_cpu_temp=0
emerg_cnt=0

EMERG_CPU_TEMP=93
CRIT_CPU_TEMP=90
ERR_CPU_TEMP=85
WARN_CPU_TEMP=80
EMERG_MLAN_TEMP=85
CRIT_MLAN_TEMP=80
ERR_MLAN_TEMP=75
WARN_MLAN_TEMP=70
COOLDOWN_SEC=60
EMERG_COUNT_THRESHOLD=2
CHECK_INTERVAL_SEC=5
CONFIG_RECOVER_CPU_TEMP=""
CONFIG_RECOVER_MLAN_TEMP=""

WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    EMERG_CPU_TEMP=$(jq -r '.temperature.emerg_cpu // 93' "$WIFI_INIT_CONF_JSON")
    CRIT_CPU_TEMP=$(jq -r '.temperature.crit_cpu // 90' "$WIFI_INIT_CONF_JSON")
    ERR_CPU_TEMP=$(jq -r '.temperature.error_cpu // 85' "$WIFI_INIT_CONF_JSON")
    WARN_CPU_TEMP=$(jq -r '.temperature.warn_cpu // 80' "$WIFI_INIT_CONF_JSON")
    EMERG_MLAN_TEMP=$(jq -r '.temperature.emerg_mlan // 85' "$WIFI_INIT_CONF_JSON")
    CRIT_MLAN_TEMP=$(jq -r '.temperature.crit_mlan // 80' "$WIFI_INIT_CONF_JSON")
    ERR_MLAN_TEMP=$(jq -r '.temperature.error_mlan // 75' "$WIFI_INIT_CONF_JSON")
    WARN_MLAN_TEMP=$(jq -r '.temperature.warn_mlan // 70' "$WIFI_INIT_CONF_JSON")
    COOLDOWN_SEC=$(jq -r '.temperature.cooldown_sec // 60' "$WIFI_INIT_CONF_JSON")
    EMERG_COUNT_THRESHOLD=$(jq -r '.temperature.emerg_count_threshold // 2' "$WIFI_INIT_CONF_JSON")
    CHECK_INTERVAL_SEC=$(jq -r '.temperature.check_interval_sec // 5' "$WIFI_INIT_CONF_JSON")
    CONFIG_RECOVER_CPU_TEMP=$(jq -r '.temperature.recover_cpu // empty' "$WIFI_INIT_CONF_JSON")
    CONFIG_RECOVER_MLAN_TEMP=$(jq -r '.temperature.recover_mlan // empty' "$WIFI_INIT_CONF_JSON")
fi

RECOVER_CPU_TEMP="${RECOVER_CPU_TEMP:-${CONFIG_RECOVER_CPU_TEMP:-$CRIT_CPU_TEMP}}"
RECOVER_MLAN_TEMP="${RECOVER_MLAN_TEMP:-${CONFIG_RECOVER_MLAN_TEMP:-$CRIT_MLAN_TEMP}}"
CPU_TEMP_PATH="${WIFI_CPU_TEMP_PATH:-/sys/devices/virtual/thermal/thermal_zone0/temp}"
MAX_TEMP_PATH="${WIFI_MAX_TEMP_PATH:-/var/log/cantops/max_temp}"
NET_CLASS_ROOT="${WIFI_NET_CLASS_ROOT:-/sys/class/net}"
COMMAND_TIMEOUT_SEC="${WIFI_LOGGER_COMMAND_TIMEOUT_SEC:-5}"
TEMP_TIMEOUT_SEC="${WIFI_LOGGER_TEMP_TIMEOUT_SEC:-3}"
INITIAL_DELAY_SEC="${WIFI_LOGGER_INITIAL_DELAY_SEC:-$CHECK_INTERVAL_SEC}"
COOLDOWN_RETRY_SEC="${WIFI_LOGGER_COOLDOWN_RETRY_SEC:-5}"
REBOOT_DELAY_SEC="${WIFI_LOGGER_REBOOT_DELAY_SEC:-3}"
ONESHOT="${WIFI_LOGGER_ONESHOT:-0}"
JOURNALD_SNAPSHOT_SH="${WIFI_JOURNALD_SNAPSHOT_SH:-/usr/local/scripts/journald_snapshot.sh}"
REBOOT_POLICY_SH="${WIFI_REBOOT_POLICY_SH:-/usr/local/scripts/wlan_reboot_policy.sh}"

WIFI_STOP_UNITS=${WIFI_STOP_UNITS:-"wifi_bridge@mlan0 wifi_bridge@mlan1 wifi_checker@mlan0 wifi_checker@mlan1 wifi_arping@mlan0 wifi_arping@mlan1 wifi_bgscan@mlan0 wifi_bgscan@mlan1 wifi_roam@mlan0 wifi_roam@mlan1 wifi_capture@mlan0 wifi_capture@mlan1 wpa_supplicant@mlan0 wpa_supplicant@mlan1 wifi_logger@mlan0 wifi_logger@mlan1 wifi_logger@eth0"}

stop_wifi_and_bridge() {
    command -v systemctl >/dev/null 2>&1 || return 0
    for unit in $WIFI_STOP_UNITS; do
        systemctl stop "$unit" 2>/dev/null || true
    done
}

read_cpu_temp() {
    local raw
    raw=$(logger_read_bounded "$COMMAND_TIMEOUT_SEC" "$CPU_TEMP_PATH" 2>/dev/null) \
        || return 1
    [[ "$raw" =~ ^-?[0-9]+$ ]] || return 1
    printf '%s\n' "$((raw / 1000))"
}

read_wlan_temp() {
    local iface=$1 output value
    output=$(logger_run_bounded "$TEMP_TIMEOUT_SEC" \
        mlanutl "$iface" get_sensor_temp 2>/dev/null) || return 1
    value=$(printf '%s\n' "$output" | awk '
        $4 ~ /^-?[0-9]+([.][0-9]+)?$/ {
            printf "%d\n", $4
            found=1
            exit
        }
        END { if (!found) exit 1 }
    ') || return 1
    printf '%s\n' "$value"
}

read_temps() {
    local value

    CPU_TEMP=unknown
    CPU_TEMP_VALID=0
    if value=$(read_cpu_temp); then
        CPU_TEMP=$value
        CPU_TEMP_VALID=1
    fi

    MLAN0_TEMP=unknown
    MLAN0_TEMP_VALID=0
    MLAN0_PRESENT=0
    if [ -d "$NET_CLASS_ROOT/mlan0" ]; then
        MLAN0_PRESENT=1
        if value=$(read_wlan_temp mlan0); then
            MLAN0_TEMP=$value
            MLAN0_TEMP_VALID=1
        fi
    fi

    MLAN1_TEMP=unknown
    MLAN1_TEMP_VALID=0
    MLAN1_PRESENT=0
    if [ -d "$NET_CLASS_ROOT/mlan1" ]; then
        MLAN1_PRESENT=1
        if value=$(read_wlan_temp mlan1); then
            MLAN1_TEMP=$value
            MLAN1_TEMP_VALID=1
        fi
    fi
}

sample_ge() {
    local valid=$1 value=$2 threshold=$3
    [ "$valid" -eq 1 ] && (( value >= threshold ))
}

temperatures_recovered() {
    [ "$CPU_TEMP_VALID" -eq 1 ] && (( CPU_TEMP < RECOVER_CPU_TEMP )) \
        || return 1
    if [ "$MLAN0_PRESENT" -eq 1 ]; then
        [ "$MLAN0_TEMP_VALID" -eq 1 ] && (( MLAN0_TEMP < RECOVER_MLAN_TEMP )) \
            || return 1
    fi
    if [ "$MLAN1_PRESENT" -eq 1 ]; then
        [ "$MLAN1_TEMP_VALID" -eq 1 ] && (( MLAN1_TEMP < RECOVER_MLAN_TEMP )) \
            || return 1
    fi
    return 0
}

cooldown_until_recover() {
    logger -p local0.emerg "[$tag:$LINENO] overtemp: stopping wifi/bridge units and cooling down (${COOLDOWN_SEC}s)"
    stop_wifi_and_bridge
    sleep "$COOLDOWN_SEC"

    while true; do
        read_temps
        if temperatures_recovered; then
            logger -p local0.emerg "[$tag:$LINENO] overtemp recovered: cpu=${CPU_TEMP} (<${RECOVER_CPU_TEMP}), mlan0=${MLAN0_TEMP}, mlan1=${MLAN1_TEMP}; rebooting"
            "$JOURNALD_SNAPSHOT_SH"
            sleep "$REBOOT_DELAY_SEC"
            "$REBOOT_POLICY_SH" \
                --source wifi_logger_temp \
                --reason "overtemp recovered -> reboot cpu=${CPU_TEMP} mlan0=${MLAN0_TEMP} mlan1=${MLAN1_TEMP}" \
                --force
            return 0
        fi

        logger -p local0.warning "[$tag:$LINENO] overtemp cooldown: cpu=${CPU_TEMP} (target<${RECOVER_CPU_TEMP}), mlan0=${MLAN0_TEMP}, mlan1=${MLAN1_TEMP}"
        sleep "$COOLDOWN_RETRY_SEC"
    done
}

update_max_cpu_temp() {
    local recorded=0
    [ "$CPU_TEMP_VALID" -eq 1 ] || return 0
    if current=$(logger_read_bounded "$COMMAND_TIMEOUT_SEC" "$MAX_TEMP_PATH" 2>/dev/null) \
        && [[ "$current" =~ ^-?[0-9]+$ ]]; then
        recorded=$current
    fi
    if (( CPU_TEMP > recorded )); then
        printf '%s\n' "$CPU_TEMP" > "$MAX_TEMP_PATH"
    fi
}

logger -p local3.info "[$tag:$LINENO] cpu_temp warn : $WARN_CPU_TEMP, err : $ERR_CPU_TEMP, crit : $CRIT_CPU_TEMP, emerg : $EMERG_CPU_TEMP"

mkdir -p "$(dirname "$MAX_TEMP_PATH")" 2>/dev/null || true
sleep "$INITIAL_DELAY_SEC"

while true; do
    read_temps
    update_max_cpu_temp

    if sample_ge "$CPU_TEMP_VALID" "$CPU_TEMP" "$EMERG_CPU_TEMP"; then
        ((emerg_cnt++))
        if (( emerg_cnt > EMERG_COUNT_THRESHOLD )); then
            logger -p local0.emerg "[$tag:$LINENO] temperature critical (cpu=${CPU_TEMP} >= ${EMERG_CPU_TEMP})"
            cooldown_until_recover
            emerg_cnt=0
        fi
    else
        emerg_cnt=0
    fi

    if sample_ge "$CPU_TEMP_VALID" "$CPU_TEMP" "$CRIT_CPU_TEMP" \
        || sample_ge "$MLAN0_TEMP_VALID" "$MLAN0_TEMP" "$CRIT_MLAN_TEMP" \
        || sample_ge "$MLAN1_TEMP_VALID" "$MLAN1_TEMP" "$CRIT_MLAN_TEMP"; then
        LOG_LEVEL=crit
    elif sample_ge "$CPU_TEMP_VALID" "$CPU_TEMP" "$ERR_CPU_TEMP" \
        || sample_ge "$MLAN0_TEMP_VALID" "$MLAN0_TEMP" "$ERR_MLAN_TEMP" \
        || sample_ge "$MLAN1_TEMP_VALID" "$MLAN1_TEMP" "$ERR_MLAN_TEMP"; then
        LOG_LEVEL=err
    elif sample_ge "$CPU_TEMP_VALID" "$CPU_TEMP" "$WARN_CPU_TEMP" \
        || sample_ge "$MLAN0_TEMP_VALID" "$MLAN0_TEMP" "$WARN_MLAN_TEMP" \
        || sample_ge "$MLAN1_TEMP_VALID" "$MLAN1_TEMP" "$WARN_MLAN_TEMP"; then
        LOG_LEVEL=warn
    else
        LOG_LEVEL=debug
    fi

    logger -p local3.$LOG_LEVEL "[$tag:$LINENO] temp cpu : $CPU_TEMP, mlan0 : $MLAN0_TEMP, mlan1 : $MLAN1_TEMP"

    [ "$ONESHOT" = "1" ] && break
    sleep "$CHECK_INTERVAL_SEC"
done
