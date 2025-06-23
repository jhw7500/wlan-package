#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1
logger -p local0.notice "[$tag:$LINENO] [$IFACE] wifi config"

if [[ "$IFACE" == "" ]]; then
    IFACE="mlan0"
fi

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface $IFACE is wrong!!"
    exit 1
fi

systemctl restart systemd-networkd
systemctl restart wifi_bridge@$IFACE
systemctl restart wifi_arping@eth0
