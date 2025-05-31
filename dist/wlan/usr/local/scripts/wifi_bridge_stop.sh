#!/bin/sh
IFACE=$1

echo "[*] Stopping WiFi bridge on $IFACE"
echo 0 > /proc/sys/net/ipv4/conf/$IFACE/proxy_arp
iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE 2>/dev/null
ip rule del iif eth0 table to5g 2>/dev/null
ip rule del iif eth0 table to2g 2>/dev/null
