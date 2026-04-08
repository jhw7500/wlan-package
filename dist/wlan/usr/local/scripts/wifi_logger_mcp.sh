#!/bin/bash
set -u

tag=$(basename "$0")
DEV="/sys/bus/iio/devices/iio:device0"
FACILITY="local3"
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

# Defaults
gain0="0.5203"
gain1="15.6552"
MCP_CHECK_INTERVAL=5

# 5V system thresholds
EMERG_A_5V=2.5; CRIT_A_5V=2.0; ERR_A_5V=1.5; WARN_A_5V=1.0
# 24V system thresholds
EMERG_A_24V=0.5; CRIT_A_24V=0.4; ERR_A_24V=0.3; WARN_A_24V=0.2

# Load from JSON config
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    DEV=$(jq -r '.mcp.iio_device // "/sys/bus/iio/devices/iio:device0"' "$WIFI_INIT_CONF_JSON")
    gain0=$(jq -r '.mcp.gain_current // 0.5203' "$WIFI_INIT_CONF_JSON")
    gain1=$(jq -r '.mcp.gain_voltage // 15.6552' "$WIFI_INIT_CONF_JSON")
    MCP_CHECK_INTERVAL=$(jq -r '.mcp.check_interval_sec // 5' "$WIFI_INIT_CONF_JSON")
    EMERG_A_5V=$(jq -r '.mcp.system_5v.emerg_a // 2.5' "$WIFI_INIT_CONF_JSON")
    CRIT_A_5V=$(jq -r '.mcp.system_5v.crit_a // 2.0' "$WIFI_INIT_CONF_JSON")
    ERR_A_5V=$(jq -r '.mcp.system_5v.error_a // 1.5' "$WIFI_INIT_CONF_JSON")
    WARN_A_5V=$(jq -r '.mcp.system_5v.warn_a // 1.0' "$WIFI_INIT_CONF_JSON")
    EMERG_A_24V=$(jq -r '.mcp.system_24v.emerg_a // 0.5' "$WIFI_INIT_CONF_JSON")
    CRIT_A_24V=$(jq -r '.mcp.system_24v.crit_a // 0.4' "$WIFI_INIT_CONF_JSON")
    ERR_A_24V=$(jq -r '.mcp.system_24v.error_a // 0.3' "$WIFI_INIT_CONF_JSON")
    WARN_A_24V=$(jq -r '.mcp.system_24v.warn_a // 0.2' "$WIFI_INIT_CONF_JSON")
fi

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
        EMERG_A=$EMERG_A_5V; CRIT_A=$CRIT_A_5V; ERR_A=$ERR_A_5V; WARN_A=$WARN_A_5V
        break
    elif (( $(echo "$v >= 20.0" | bc -l) )) && (( $(echo "$v <= 30.0" | bc -l) )); then
        EMERG_A=$EMERG_A_24V; CRIT_A=$CRIT_A_24V; ERR_A=$ERR_A_24V; WARN_A=$WARN_A_24V
        break
    else
        logger -p ${FACILITY}.emerg "[$tag:$LINENO] Invalid Voltage!! CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"
        sleep "$MCP_CHECK_INTERVAL"
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
    else LOG_LEVEL=debug
    fi

    logger -p ${FACILITY}.${LOG_LEVEL} "[$tag:$LINENO] CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"

    sleep "$MCP_CHECK_INTERVAL"
done
