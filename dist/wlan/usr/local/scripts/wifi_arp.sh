#!/bin/bash
tag=$(basename "$0")
IFACE=$1

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

logger -p local0.info "[$tag:$LINENO] WPA Event Detect" 

for i in {1..10}; do
    ip_info=$(ip -4 addr show dev eth0 | grep inet | awk '{print $2}')
    if [ -n "$ip_info" ]; then
        break
    fi
    sleep 0.5
done

if [ -z "$ip_info" ]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE]  No IP found, skipping broadcast ping"
    exit 1
fi

broadcast=$(python3 -c "import ipaddress; print(ipaddress.IPv4Interface('$ip_info').network.broadcast_address)")

logger -p local0.info "[$tag:$LINENO] [$IFACE] IP: $ip_info, Broadcast: $broadcast"

# ARP flush
ip neigh flush dev eth0
ip neigh flush dev $IFACE

# 유도용 ping
ping -b -c 1 "$broadcast"

# optional: gratuitous ARP
# arping -U -I $IFACE "$ip_addr"

logger -p local0.info "[$tag:$LINENO] [$IFACE] ARP trigger sent to $broadcast"
