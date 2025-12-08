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
EMERG_MLAN_TEMP=$((EMERG_CPU_TEMP-10))
CRIT_MLAN_TEMP=$((CRIT_CPU_TEMP-10))
ERR_MLAN_TEMP=$((ERR_CPU_TEMP-10))
WARN_MLAN_TEMP=$((WARN_CPU_TEMP-10))
MLAN0_TEMP=0
MLAN1_TEMP=0
LOG_LEVEL=info
max_cpu_temp=0
emerg_cnt=0

logger -p local3.info "[$tag:$LINENO] cpu_temp warn : $WARN_CPU_TEMP, err : $ERR_CPU_TEMP, crit : $CRIT_CPU_TEMP, emerg : $EMERG_CPU_TEMP"

sleep 5

while true; do
    CPU_TMP_VAL=$(cat /sys/devices/virtual/thermal/thermal_zone0/temp)
    CPU_TEMP=$(echo "$CPU_TMP_VAL/1000" | bc)
    MLAN0_TEMP=$(mlanutl mlan0 get_sensor_temp 2>/dev/null | awk '{print int($4)}')
    MLAN1_TEMP=$(mlanutl mlan1 get_sensor_temp 2>/dev/null | awk '{print int($4)}')
    max_cpu_temp=$(cat /var/log/cantops/max_temp)
        
    if (( CPU_TEMP > max_cpu_temp )); then
        echo $CPU_TEMP > /var/log/cantops/max_temp
    fi

    if (( CPU_TEMP >= EMERG_CPU_TEMP )); then
        ((emerg_cnt++))
        if (( emerg_cnt > 2 )); then
            logger -p local0.emerg "[$tag:$LINENO] temperature critical reboot..."
            /usr/local/scripts/journald_snapshot.sh
            sleep 3
            reboot
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
