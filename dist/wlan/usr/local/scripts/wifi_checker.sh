#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1

logger -p local0.notice "[$tag:$LINENO] wifi check : $IFACE"

#python3 /usr/local/logger/wifi_module_check.py $IFACE

while true; do
    LINK_OUTPUT=$(iw "$IFACE" link 2>&1)
    if echo "$LINK_OUTPUT" | grep -q "Connected to" && echo "$LINK_OUTPUT" | grep -q "command failed"; then
        logger -p local0.notice "[$tag:$LINENO] $IFACE Detected invalid link state. Triggering reconnect..."
        wpa_cli -i "$IFACE" disconnect
        sleep 1
        wpa_cli -i "$IFACE" reconnect
    fi
    sleep 5
done
