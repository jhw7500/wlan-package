#!/bin/bash
tag=$(basename "$0")
state="00"
prestate="00"
while true; do
    state=$(cat /sys/kernel/debug/regmap/0-004b/registers |grep 2d:|awk '{print $2}')
    if [ "$state" != "$prestate" ]; then
        logger -p local0.info "[$tag:$LINENO] pmic state change : 0x$prestate -> 0x$state"
        prestate=$state
    fi
    sleep 0.5
done
