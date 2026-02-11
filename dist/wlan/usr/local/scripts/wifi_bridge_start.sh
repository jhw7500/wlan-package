#!/bin/sh
set -eu

IFACE=$1

echo "[*] Starting WiFi bridge on $IFACE"

# proxy_arp 설정
for i in mlan0 mlan1; do
    if [ "$i" = "$IFACE" ]; then
        echo 1 > /proc/sys/net/ipv4/conf/$i/proxy_arp
    else
        echo 0 > /proc/sys/net/ipv4/conf/$i/proxy_arp
    fi
done

# routing 테이블 조작
if [ "$IFACE" = "mlan0" ]; then
    #ip rule replace iif eth0 table to5g
    ip rule del iif eth0 table to5g 2>/dev/null
    ip rule del iif eth0 table to2g 2>/dev/null
    ip rule add iif eth0 table to5g
    ip route replace table to5g 192.168.4.0/24 dev mlan0
    iptables -t nat -D POSTROUTING -o mlan1 -j MASQUERADE 2>/dev/null
    iptables -t nat -A POSTROUTING -o mlan0 -j MASQUERADE
else
    #ip rule replace iif eth0 table to2g
    ip rule del iif eth0 table to5g 2>/dev/null
    ip rule del iif eth0 table to2g 2>/dev/null
    ip rule add iif eth0 table to2g
    ip route replace table to2g 192.168.4.0/24 dev mlan1
    iptables -t nat -D POSTROUTING -o mlan0 -j MASQUERADE 2>/dev/null
    iptables -t nat -A POSTROUTING -o mlan1 -j MASQUERADE
fi
