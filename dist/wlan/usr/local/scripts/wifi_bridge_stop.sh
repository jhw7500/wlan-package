#!/bin/bash
tag=$(basename "$0")
IFACE=$1

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi bridge stop"
if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] $IFACE is wrong!!"
    exit 1
fi

#systemctl stop wifi_ping@$IFACE
#echo "[*] Stopping WiFi bridge on $IFACE"
#echo 0 > /proc/sys/net/ipv4/conf/$IFACE/proxy_arp
#iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE 2>/dev/null
#ip rule del iif eth0 table to5g 2>/dev/null
#ip rule del iif eth0 table to2g 2>/dev/null
