#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1
MODULE_NAME="moal"

sleep 3

logger -p local0.notice "[$tag:$LINENO] $IFACE wifi_checker"

#python3 /usr/local/logger/wifi_module_check.py $IFACE

while true; do
    if lsmod |grep -q "^$MODULE_NAME"; then
        logger -p local0.notice "[$tag:$LINEO] $IFACE $MODULE_NAME is loading..."
        #sleep 5
        break
    fi
    sleep 5
done

err_cnt=0
while true; do
    if [ ! -d /sys/class/net/$IFACE ]; then
        ((err_cnt++))
        logger -p local0.err "[$tag:$LINEO] $IFACE is not exist becase F/W dump..."        
        if [ "$err_cnt" -ge 5 ]; then
            logger -p local0.emerg "[$tag:$LINEO] Reboot because $IFACE is cannot recovery"
            reboot            
        fi
    else
        err_cnt=0
        LINK_OUTPUT=$(iw "$IFACE" link 2>&1)
        if echo "$LINK_OUTPUT" | grep -q "Connected to" && echo "$LINK_OUTPUT" | grep -q "command failed"; then
            logger -p local0.notice "[$tag:$LINENO] $IFACE Detected invalid link state. Triggering reconnect..."
            wpa_cli -i "$IFACE" disconnect
            sleep 1
            wpa_cli -i "$IFACE" reconnect
        fi
    fi
    sleep 5
done
