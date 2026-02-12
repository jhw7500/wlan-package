#!/bin/bash
tag=$(basename "$0")

cleanup() {
    logger -p local3.info "[$tag:$LINENO] stop"
    exit 0
}
trap cleanup INT TERM

CPU_TMP_VAL=0
CPU_TEMP=0
EMERG_CPU_TEMP=93
CRIT_CPU_TEMP=90
ERR_CPU_TEMP=85
WARN_CPU_TEMP=80
EMERG_MLAN_TEMP=$((EMERG_CPU_TEMP-8))
CRIT_MLAN_TEMP=$((CRIT_CPU_TEMP-10))
ERR_MLAN_TEMP=$((ERR_CPU_TEMP-10))
WARN_MLAN_TEMP=$((WARN_CPU_TEMP-10))
MLAN0_TEMP=0
MLAN1_TEMP=0
LOG_LEVEL=info
max_cpu_temp=0
emerg_cnt=0

to_int() {
    local v
    v=${1:-}
    if [[ "$v" =~ ^-?[0-9]+$ ]]; then
        echo "$v"
    else
        echo 0
    fi
}

COOLDOWN_SEC=${COOLDOWN_SEC:-60}
RECOVER_CPU_TEMP=${RECOVER_CPU_TEMP:-$CRIT_CPU_TEMP}
RECOVER_MLAN_TEMP=${RECOVER_MLAN_TEMP:-$CRIT_MLAN_TEMP}

stop_wifi_and_bridge() {
    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi

    for unit in \
        "wifi_bridge@mlan0" "wifi_bridge@mlan1" \
        "wifi_checker@mlan0" "wifi_checker@mlan1" \
        "wifi_arping@mlan0" "wifi_arping@mlan1" \
        "wifi_bgscan@mlan0" "wifi_bgscan@mlan1" \
        "wifi_roam@mlan0" "wifi_roam@mlan1" \
        "wifi_capture@mlan0" "wifi_capture@mlan1" \
        "wpa_supplicant@mlan0" "wpa_supplicant@mlan1" \
        "wifi_logger@mlan0" "wifi_logger@mlan1" \
        "wifi_logger@eth0" \
        ; do
        systemctl stop "$unit" 2>/dev/null || true
    done
}

read_temps() {
    CPU_TMP_VAL=$(to_int "$(cat /sys/devices/virtual/thermal/thermal_zone0/temp 2>/dev/null || echo 0)")
    CPU_TEMP=$((CPU_TMP_VAL / 1000))
    MLAN0_TEMP=$(to_int "$(mlanutl mlan0 get_sensor_temp 2>/dev/null | awk '{print int($4)}' || true)")
    MLAN1_TEMP=$(to_int "$(mlanutl mlan1 get_sensor_temp 2>/dev/null | awk '{print int($4)}' || true)")
}

cooldown_until_recover() {
    logger -p local0.emerg "[$tag:$LINENO] overtemp: stopping wifi/bridge units and cooling down (${COOLDOWN_SEC}s)"
    stop_wifi_and_bridge
    sleep "$COOLDOWN_SEC"

    while true; do
        read_temps
        if (( CPU_TEMP < RECOVER_CPU_TEMP && MLAN0_TEMP < RECOVER_MLAN_TEMP && MLAN1_TEMP < RECOVER_MLAN_TEMP )); then
            logger -p local0.emerg "[$tag:$LINENO] overtemp recovered: cpu=${CPU_TEMP} (<${RECOVER_CPU_TEMP}), mlan0=${MLAN0_TEMP}, mlan1=${MLAN1_TEMP}; rebooting"
            /usr/local/scripts/journald_snapshot.sh
            sleep 3
            /usr/local/scripts/wlan_reboot_policy.sh \
              --source wifi_logger_temp \
              --reason "overtemp recovered -> reboot cpu=${CPU_TEMP} mlan0=${MLAN0_TEMP} mlan1=${MLAN1_TEMP}" \
              --force
            return 0
        fi

        logger -p local0.warning "[$tag:$LINENO] overtemp cooldown: cpu=${CPU_TEMP} (target<${RECOVER_CPU_TEMP}), mlan0=${MLAN0_TEMP}, mlan1=${MLAN1_TEMP}"
        sleep 5
    done
}

logger -p local3.info "[$tag:$LINENO] cpu_temp warn : $WARN_CPU_TEMP, err : $ERR_CPU_TEMP, crit : $CRIT_CPU_TEMP, emerg : $EMERG_CPU_TEMP"

sleep 5

while true; do
    read_temps
    mkdir -p /var/log/cantops 2>/dev/null || true
    max_cpu_temp=$(to_int "$(cat /var/log/cantops/max_temp 2>/dev/null || echo 0)")
        
    if (( CPU_TEMP > max_cpu_temp )); then
        echo "$CPU_TEMP" > /var/log/cantops/max_temp
    fi

    if (( CPU_TEMP >= EMERG_CPU_TEMP )); then
        ((emerg_cnt++))
        if (( emerg_cnt > 2 )); then
            logger -p local0.emerg "[$tag:$LINENO] temperature critical (cpu=${CPU_TEMP} >= ${EMERG_CPU_TEMP})"
            cooldown_until_recover
        fi
    elif (( CPU_TEMP >= CRIT_CPU_TEMP || MLAN0_TEMP >= CRIT_MLAN_TEMP || MLAN1_TEMP >= CRIT_MLAN_TEMP )); then
        LOG_LEVEL=crit
        emerg_cnt=0
    elif (( CPU_TEMP >= ERR_CPU_TEMP  || MLAN0_TEMP >= ERR_MLAN_TEMP  || MLAN1_TEMP >= ERR_MLAN_TEMP )); then
        LOG_LEVEL=err
        emerg_cnt=0
    elif (( CPU_TEMP >= WARN_CPU_TEMP  || MLAN0_TEMP >= WARN_MLAN_TEMP  || MLAN1_TEMP >= WARN_MLAN_TEMP )); then
        LOG_LEVEL=warn
        emerg_cnt=0
    else
        LOG_LEVEL=debug
        emerg_cnt=0
    fi

    logger -p local3.$LOG_LEVEL "[$tag:$LINENO] temp cpu : $CPU_TEMP, mlan0 : $MLAN0_TEMP, mlan1 : $MLAN1_TEMP"

    sleep 5
done
