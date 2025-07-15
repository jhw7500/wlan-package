#!/bin/bash
tag=$(basename "$0")
IFACE="eth0"
FILE_PATH="/opt/wlan/mac"
FILE_NAME="wired"

mac_addr=$(cat /sys/class/net/$IFACE/address)
if [[ "$mac_addr" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    logger -p local0.info "[$tag:$LINENO] [eth0] valid mac address : $mac_addr"
else
    logger -p local0.err "[$tag:$LINENO] [eth0] invalid mac address : $mac_addr"
fi

echo "$mac_addr" > "$FILE_PATH/$FILE_NAME"


