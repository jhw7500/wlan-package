#!/bin/bash
set -u

tag=$(basename "$0")
DEV="/sys/bus/iio/devices/iio:device0"
gain0="1.52439"   # CH0: 전류
gain1="15.6552"   # CH1: 전압
FACILITY="local3"

cleanup() {
    logger -p ${FACILITY}.info "[$tag:$LINENO] wifi_logger_mcp stop"
    exit 0
}
trap cleanup INT TERM

logger -p ${FACILITY}.info "[$tag:$LINENO] wifi_logger_mcp start"

while true; do
    raw0=$(cat "$DEV/in_voltage0_raw")
    scale0=$(cat "$DEV/in_voltage0_scale")
    a=$(echo "$raw0 * $scale0 * $gain0" | bc -l)

    raw1=$(cat "$DEV/in_voltage1_raw")
    scale1=$(cat "$DEV/in_voltage1_scale")
    v=$(echo "$raw1 * $scale1 * $gain1" | bc -l)

    if  (( $(echo "$v >= 4.0"  | bc -l) )) && (( $(echo "$v <= 6.0"  | bc -l) )); then
        EMERG_A=2.5
        CRIT_A=2.0
        ERR_A=1.5
        WARN_A=1.0
        break
    elif (( $(echo "$v >= 20.0" | bc -l) )) && (( $(echo "$v <= 30.0" | bc -l) )); then
        EMERG_A=0.5
        CRIT_A=0.4
        ERR_A=0.3
        WARN_A=0.2
        break
    else
        logger -p ${FACILITY}.emerg "[$tag:$LINENO] Invalid Voltage!! CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"
        sleep 5
    fi
done

logger -p ${FACILITY}.info "[$tag:$LINENO] EMERG_A=$EMERG_A, CRIT_A=$CRIT_A, ERR_A=$CRIT_A, WARN_A=$WARN_A"

while true; do
    raw0=$(cat "$DEV/in_voltage0_raw")
    scale0=$(cat "$DEV/in_voltage0_scale")
    a=$(echo "$raw0 * $scale0 * $gain0" | bc -l)

    raw1=$(cat "$DEV/in_voltage1_raw")
    scale1=$(cat "$DEV/in_voltage1_scale")
    v=$(echo "$raw1 * $scale1 * $gain1" | bc -l)

    if   (( $(echo "$a >= $EMERG_A" | bc -l) )); then LOG_LEVEL=emerg
    elif (( $(echo "$a >= $CRIT_A"  | bc -l) )); then LOG_LEVEL=crit
    elif (( $(echo "$a >= $ERR_A"   | bc -l) )); then LOG_LEVEL=err
    elif (( $(echo "$a >= $WARN_A"  | bc -l) )); then LOG_LEVEL=warn
    else LOG_LEVEL=info
    fi

    logger -p ${FACILITY}.${LOG_LEVEL} "[$tag:$LINENO] CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"

    sleep 5
done
