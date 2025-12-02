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
    logger -p local3.debug "[$tag:$LINENO] CPU : $cpu_usage%, MEM : $mem_usage%"
    sleep 60
done
