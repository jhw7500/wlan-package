#!/bin/bash
tag=$(basename "$0")

cleanup() {
    logger -p $local3.info "[$tag:$LINENO] wifi_logger_temp stop"
    exit 0
}
trap cleanup INT TERM

logger -p local3.info "[$tag:$LINENO] wifi_logger_temp start"

CPU_TMP_VAL=0
CPU_TEMP=0
EMERG_CPU_TEMP=90
CRIT_CPU_TEMP=85
WARN_CPU_TEMP=75
EMERG_MLAN_TEMP=$((EMERG_CPU_TEMP-10))
CRIT_MLAN_TEMP=$((CRIT_CPU_TEMP-10))
WARN_MLAN_TEMP=$((WARN_CPU_TEMP-10))
MLAN0_TEMP=0
MLAN1_TEMP=0
LOG_LEVEL=info

while true; do
    CPU_TMP_VAL=$(cat /sys/devices/virtual/thermal/thermal_zone0/temp)
    CPU_TEMP=$(echo "$CPU_TMP_VAL/1000" | bc)
    MLAN0_TEMP=$(mlanutl mlan0 get_sensor_temp | awk '{print int($4)}')
    MLAN1_TEMP=$(mlanutl mlan1 get_sensor_temp | awk '{print int($4)}')

    if [ "$CPU_TEMP" -ge "$EMERG_CPU_TEMP" ] || [ "$MLAN0_TEMP" -ge "$EMERG_MLAN_TEMP" ] || [ "$MLAN1_TEMP" -ge "$EMERG_MLAN_TEMP" ]; then
        LOG_LEVEL=emerg
    elif [ "$CPU_TEMP" -ge "$CRIT_CPU_TEMP" ] || [ "$MLAN0_TEMP" -ge "$CRIT_MLAN_TEMP" ] || [ "$MLAN1_TEMP" -ge "$CRIT_MLAN_TEMP" ]; then
        LOG_LEVEL=crit
    elif [ "$CPU_TEMP" -ge "$WARN_CPU_TEMP" ] || [ "$MLAN0_TEMP" -ge "$EMERG_MLAN_TEMP" ] || [ "$MLAN1_TEMP" -ge "$EMERG_MLAN_TEMP" ]; then
        LOG_LEVEL=warn
    else
        LOG_LEVEL=info
        :
    fi
    
    #if [ "$LOG_LEVEL" != info ]; then
        logger -p local3.$LOG_LEVEL "[$tag:$LINENO] temp cpu : $CPU_TEMP, mlan0 : $MLAN0_TEMP, mlan1 : $MLAN1_TEMP"
    #fi
    sleep 5
done

logger -p local3.info "[$tag:$LINENO] wifi_logger_temp stop"
