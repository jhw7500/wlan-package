#!/bin/bash

IFACE=$1

IP_ADDR=$(ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1)

GATEWAY=$(ip route show default dev "$IFACE" | awk '/default/ {print $3}')

echo "IP: $IP_ADDR"
echo "Gateway: $GATEWAY"

arping -I $IFACE -s $IP_ADDR $GATEWAY -q
