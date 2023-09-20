#!/bin/bash
IFACE=$1
tag=$(basename "$0")

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')

if [ -z "$IP_LIST" ]; then
  #echo "No IP found on $iface"
  logger -p local0.err "[$tag:$LINENO] [$IFACE] No IP found"
  exit 1
fi

for IP in $IP_LIST; do
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Found client IP: $IP"
done

while true; do
    IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')

    if [ -z "$IP_LIST" ]; then
        #echo "No IP found on $iface"
        logger -p local0.err "[$tag:$LINENO] [$IFACE] No IP found"
        exit 1
    fi

    for IP in $IP_LIST; do
        arping -I "$IFACE" -c 1 "$IP"
    done
    sleep 20
done
