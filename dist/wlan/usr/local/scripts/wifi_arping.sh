#!/bin/bash
IFACE=$1
target_ip=""
tag=$(basename "$0")

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

target_ip=$(ip neigh show dev $IFACE | grep 'lladdr' | awk '{print $1}')

if [ -z "$target_ip" ]; then
  #echo "No IP found on $iface"
  logger -p local0.err "[$tag:$LINENO] [$IFACE] No IP found"
  exit 1
fi

#echo "Found client IP: $target_ip"
logger -p local0.info "[$tag:$LINENO] [$IFACE] Found client IP: $target_ip"

while true; do
  #ping -c 1 -I $iface $target_ip > /dev/null
  arping -c 1 -I $IFACE $target_ip
  sleep 20
done
