#!/bin/bash
tag=$(basename "$0")
IFACE=$1

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

sleep 1

IP_ADDR=$(ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1)

GATEWAY=$(ip route show default dev "$IFACE" | awk '/default/ {print $3}')


logger -p local0.notice "[$tag:$LINENO] [$IFACE] IP : $IP_ADDR, Gateway : $GATEWAY"

#arping -I $IFACE -s $IP_ADDR $GATEWAY -q

while true; do
    #IP_ADDR=$(ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1)
    GATEWAY=$(ip route show default dev "$IFACE" | awk '/default/ {print $3}')
    if [ -z "$GATEWAY" ]; then
        sleep 1
        continue
    fi

    if [ "$GATEWAY" != "$PRE_GATEWAY" ]; then
        logger -p local0.notice "[$tag:$LINENO] [$IFACE] Gateway change from $GATEWAY to $GATEWAY_NEW"
    fi

    #for i in $(seq 1 2); do
    #    arping -c 1 -I mlan0 $GATEWAY
    #    sleep 1
    #done

    arping -c 1 -I mlan0 $GATEWAY -q

    PRE_GATEWAY=$GATEWAY
    
    sleep 3
done
