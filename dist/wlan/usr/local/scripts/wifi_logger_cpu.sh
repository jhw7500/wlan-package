#!/bin/sh
tag=$(basename "$0")

# Defaults
CPU_LOG_INTERVAL=60

# Load from JSON config
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    CPU_LOG_INTERVAL=$(jq -r '.logger.cpu_interval_sec // 60' "$WIFI_INIT_CONF_JSON")
fi

cleanup() {
    #logger -p local3.info "[$tag:$LINENO] stop"
    exit 0
}
trap cleanup INT TERM


#logger -p local3.info "[$tag:$LINENO] start"

while :; do
    cpu_usage=$(mpstat 1 1|tail -1 | awk '{print 100-$NF}')
    mem_usage=$(sar -r 0 |tail -1 | awk '{print $5}')
    clks_log_part=""
    for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*; do
        core_id=${cpu_dir##*cpu}
        freq_file="$cpu_dir/cpufreq/cpuinfo_cur_freq"
        if [ -f "$freq_file" ]; then
            freq=$(cat "$freq_file")
            clks_log_part="$clks_log_part, clk${core_id}:${freq}"
        fi
    done
    logger -p local3.debug "[$tag:$LINENO] CPU:$cpu_usage%, MEM:$mem_usage%${clks_log_part}"
    sleep "$CPU_LOG_INTERVAL"
done
