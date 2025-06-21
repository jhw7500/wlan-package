#!/bin/bash
IFACE=$1
tag=$(basename "$0")
IP_LIST=""
IP_LIST_NEW=""
LIMIT_CNT=3
ERR_CNT=0

logger -p local0.info "[$tag:$LINENO] [$IFACE] arping start"
logger -p local1.info "[$tag:$LINENO] [$IFACE] arping start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    logger -p local1.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

ip neigh flush dev $IFACE

:<<'END'
IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')

if [ -z "$IP_LIST" ]; then
  #echo "No IP found on $iface"
  logger -p local0.err "[$tag:$LINENO] [$IFACE] No IP found"
  #exit 1
fi

for IP in $IP_LIST; do
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Found client IP: $IP"
done
END

while true; do
    IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')
    
    if [ -z "$IP_LIST" ]; then
        #echo "No IP found on $iface"
        logger -p local1.err "[$tag:$LINENO] [$IFACE] No IP found"
        IP_LIST_NEW=""
        sleep 5
        continue
    fi

    #if [[ "$IP_LIST" != "$IP_LIST_NEW" ]]; then
    if [[ "$(echo "$IP_LIST" | tr ' ' '\n' | sort)" != "$(echo "$IP_LIST_NEW" | tr ' ' '\n' | sort)" ]]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] IP_LIST update"
        for IP in $IP_LIST; do
            logger -p local1.info "[$tag:$LINENO] [$IFACE] client IP: $IP"
        done
    fi

    for IP in $IP_LIST; do
        CURRENT_IFACE=$(ip route get "$IP" 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')
        
        if [[ -z "$CURRENT_IFACE" || "$CURRENT_IFACE" != "$IFACE" ]]; then
            logger -p local1.warn "[$tag:$LINENO] [$IFACE] IP $IP is routed via [$CURRENT_IFACE], correcting to [$IFACE]"
            ip route replace "$IP" dev "$IFACE" scope link
        fi
        #arping -I "$IFACE" -c 1 "$IP" 

        OUTPUT=$(arping -I "$IFACE" -c 1 -w 2 "$IP" 2>&1)
        if echo "$OUTPUT" | grep -q "Received 0"; then
            ((ERR_CNT++))
            logger -p local1.err "[$tag:$LINENO] [$IFACE] arping to $IP failed: no reply ($ERR_CNT)"
            if [ "$ERR_CNT" -ge "$LIMIT_CNT" ]; then
                logger -p local1.err "[$tag:$LINENO] [$IFACE] arping err($ERR_CNT) over limit($LIMIT_CNT)"
                logger -p local1.err "[$tag:$LINENO] [$IFACE] route cache & arp table flush and restart wifi bridge"
                ip neigh flush dev $IFACE
                ip route flush cache
                systemctl restart wifi_bridge@$IFACE
                ERR_CNT=0
            fi
        else
            logger -p local1.info "[$tag:$LINENO] [$IFACE] arping to $IP successful"
            ERR_CNT=0
        fi
    done

    IP_LIST_NEW="$IP_LIST"

    if [ "$ERR_CNT" -ge 1 ]; then
        sleep 3
    else
        sleep 5
    fi
done
