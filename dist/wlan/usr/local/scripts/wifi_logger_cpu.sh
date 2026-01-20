#!/bin/sh
tag=$(basename "$0")

cleanup() {
    logger -p local3.info "[$tag:$LINENO] stop"
    exit 0
}
trap cleanup INT TERM


logger -p local3.info "[$tag:$LINENO] start"

while :; do
    cpu_usage=$(mpstat 1 1|tail -1 | awk '{print 100-$NF}')
    mem_usage=$(sar -r 0 |tail -1 | awk '{print $5}')
    clk0=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq)
    clk1=$(cat /sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_cur_freq)
    clk2=$(cat /sys/devices/system/cpu/cpu2/cpufreq/cpuinfo_cur_freq)
    clk3=$(cat /sys/devices/system/cpu/cpu3/cpufreq/cpuinfo_cur_freq)
    logger -p local3.debug "[$tag:$LINENO] CPU:$cpu_usage%, MEM:$mem_usage%, clk0:$clk0, clk1:$clk1, clk2:$clk2, clk3:$clk3"
    sleep 60
done
