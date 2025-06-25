#!/bin/bash
IFACE=$1
tag=$(basename "$0")
IP_LIST=""
IP_LIST_NEW=""
INIT_LIMIT=2
ERR_CNT=0
ERR_LIMIT=2
INIT_CNT=0
REBOOT_CNT=0
REBOOT_LIMIT=2
REBOOT_FLAG=0
INTERVAL=2

get_state() {
    wpa_cli -i "$IFACE" status | grep "^wpa_state=" | cut -d= -f2
}

is_connected() {
    local state
    state=$(get_state)
    [[ "$state" == "COMPLETED" ]]
}

is_wpa_active() {
    systemctl is-active --quiet "wpa_supplicant@${IFACE}.service"
}

get_gateway() {
    ip route show default dev "$IFACE" | awk '/default/ {print $3}'
}

if [ -z "$BASH_VERSION" ]; then
  echo "This script requires bash." >&2
  exit 1
fi

declare -A ERR_CNT_MAP
declare -A INIT_CNT_MAP
declare -A REBOOT_CNT_MAP

logger -p local0.info "[$tag:$LINENO] [$IFACE] arping start"
logger -p local1.info "[$tag:$LINENO] [$IFACE] arping start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    logger -p local1.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
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
    logger -p local1.info "[$tag:$LINENO] [$IFACE] Found client IP: $IP"
done
END

while true; do
    #IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')
    #IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}' | grep -vE '^224\.|^169\.')

    BAD_IP_LIST=$(ip neigh show dev "$IFACE" | awk '!/lladdr/ {print $1}' | grep -vE '^224\.|^169\.')
    
    for IP in $BAD_IP_LIST; do
         logger -p local1.info "[$tag:$LINENO] [$IFACE] $IP is del in neigh"
         ip neigh del "$IP" dev "$IFACE"
    done

    IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}' | grep -vE '^224\.|^169\.')

    if [[ "$IFACE" != eth0 ]] && ! is_wpa_active && ! is_connected; then
        sleep $INTERVAL
        continue
    fi

    if [ -z "$IP_LIST" ]; then
        #echo "No IP found on $iface"
        ((ERR_CNT++))
        logger -p local1.err "[$tag:$LINENO] [$IFACE] No IP found ($ERR_CNT)"
        IP_LIST_NEW=""
        GATEWAY=$(get_gateway)
        if [ ! -z "$GATEWAY" ]; then
            logger -p local1.info "[$tag:$LINENO] [$IFACE] ping to Gateway : $GATEWAY"
            ping -I $IFACE -c 1 -w 2 $GATEWAY -q
        fi

        if [[ "$ERR_CNT" -gt "$ERR_LIMIT" ]]; then
            ((INIT_CNT++))
            logger -p local1.err "[$tag:$LINENO] [$IFACE] arping err($ERR_CNT) over limit($ERR_LIMIT), init err($INIT_CNT)"
            logger -p local1.err "[$tag:$LINENO] [$IFACE] route cache and arp table flush"
            ip neigh flush dev $IFACE
            ip route flush cache
            ERR_CNT=0
            #if [[ "$IFACE" == "eth0" ]]; then
            if [ "$INIT_CNT" -gt "$INIT_LIMIT" ]; then
                ((REBOOT_CNT++))
                logger -p local1.err "[$tag:$LINENO] [$IFACE] init err($INIT_CNT) over limit($INIT_LIMIT), reset err($REBOOT_CNT)"
                ACTIVE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oE 'wifi_bridge@[^ ]+')
                if [[ -n "$ACTIVE_BRIDGE" ]]; then
                    logger -p local1.info "[$tag:$LINENO] [$IFACE] restarting $ACTIVE_BRIDGE"
                    systemctl restart "$ACTIVE_BRIDGE"
                else
                    logger -p local1.warn "[$tag:$LINENO] [$IFACE] no active wifi_bridge@ service found"                    
                fi
                INIT_CNT=0
                if [ "$REBOOT_CNT" -gt "$REBOOT_LIMIT" ]; then
                    logger -p local1.err "[$tag:$LINENO] [$IFACE] reset network because reset err($REBOOT_CNT) over limit($REBOOT_LIMIT)"
                    systemctl restart systemd-networkd
                    systemctl restart wifi_bridge@mlan0
                    REBOOT_CNT=0
                fi
            fi
        fi
        sleep $INTERVAL
        continue
    fi

    ERR_CNT=0
    INIT_CNT=0
    REBOOT_CNT=0

    #if [[ "$IP_LIST" != "$IP_LIST_NEW" ]]; then
    if [[ "$(echo "$IP_LIST" | tr ' ' '\n' | sort)" != "$(echo "$IP_LIST_NEW" | tr ' ' '\n' | sort)" ]]; then
        logger -p local1.info "[$tag:$LINENO] [$IFACE] IP_LIST update"
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
        ACTIVE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oE 'wifi_bridge@[^ ]+')
        SRC_IFACE=$(echo "$ACTIVE_BRIDGE" | sed 's/^wifi_bridge@//' | sed 's/.service$//')
        SRC_IP=$(ip -4 -o addr show dev "$SRC_IFACE" | awk '{print $4}' | cut -d/ -f1)
        #logger -p local1.info "SRC_IFACE : $SRC_IFACE, SRC_IP : $SRC_IP"

        #if [[ ! -n "$SRC_IP" ]]; then
        #    SRC_IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1 | awk 'NR==2')
        #    #logger -p local1.info "lo IP : $SRC_IP"
        #fi

        if [[ ! -n "$SRC_IP" ]]; then
            SRC_IP=$(ip -4 -o addr show dev lo | awk '{print $4}' | cut -d/ -f1 | awk 'NR==2')
            #logger -p local1.info "lo IP : $SRC_IP"
        fi


        if [[ -n "$SRC_IP" ]]; then
            OUTPUT=$(arping -I "$IFACE" -s "$SRC_IP" -c 1 -w 2 "$IP" 2>&1)
            #logger -p local1.info "[arping] $IFACE ($SRC_IP) → $TARGET_IP : $OUTPUT"
        else
            OUTPUT=$(arping -I "$IFACE" -c 1 -w 2 "$IP" 2>&1)
            #logger -p local1.err "[arping] Failed to get source IP from $IFACE"
        fi

        if echo "$OUTPUT" | grep -q "Received 0"; then
            logger -p local1.err "[$tag:$LINENO] [$IFACE] arping to $IP failed: no reply"
            if ping -I "$IFACE" -c 1 -W 2 "$IP" > /dev/null 2>&1; then
                logger -p local1.info "[$tag:$LINENO] [$IFACE] arping failed but ping to $IP successful"
                ERR_CNT_MAP["$IP"]=0
                INIT_CNT_MAP["$IP"]=0
                REBOOT_CNT_MAP["$IP"]=0
            else
                #((ERR_CNT_MAP["$IP"]++))
                ERR_CNT_MAP["$IP"]=$(( ${ERR_CNT_MAP["$IP"]:-0} + 1 ))
                logger -p local1.err "[$tag:$LINENO] [$IFACE] ping to $IP failed: no reply (${ERR_CNT_MAP["$IP"]})"
                if [[ ${ERR_CNT_MAP[$IP]:-0} -gt $ERR_LIMIT ]]; then
                    INIT_CNT_MAP["$IP"]=$(( ${INIT_CNT_MAP["$IP"]:-0} + 1 ))
                    logger -p local1.err "[$tag:$LINENO] [$IFACE] err(${ERR_CNT_MAP["$IP"]}) over limit($ERR_LIMIT), init err(${INIT_CNT_MAP["$IP"]})"
                    logger -p local1.err "[$tag:$LINENO] [$IFACE] route cache & arp table (${INIT_CNT_MAP["$IP"]})"
                    ip neigh flush dev mlan1
                    ip neigh flush dev mlan0
                    ip route flush cache
                    ERR_CNT_MAP["$IP"]=0
                    #((INIT_CNT_MAP["$IP"]++))
                    if [ ${INIT_CNT_MAP["$IP"]:-0} -gt $INIT_LIMIT ]; then
                        REBOOT_CNT_MAP["$IP"]=$(( ${REBOOT_CNT_MAP["$IP"]:-0} + 1 ))
                        logger -p local1.err "[$tag:$LINENO] [$IFACE] init err(${INIT_CNT_MAP["$IP"]}) over limit($INIT_LIMIT), reboot err(${REBOOT_CNT_MAP["$IP"]})"
                        ACTIVE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oE 'wifi_bridge@[^ ]+')
                        INIT_CNT_MAP["$IP"]=0
                        if [[ -n "$ACTIVE_BRIDGE" ]]; then
                            if [ ${REBOOT_CNT_MAP["$IP"]:-0} -gt $REBOOT_LIMIT ]; then
                                logger -p local0.crit "[$tag:$LINENO] [$IFACE] reboot err(${REBOOT_CNT_MAP["$IP"]}) over limit($REBOOT_LIMIT)"
                                logger -p local1.crit "[$tag:$LINENO] [$IFACE] reboot err(${REBOOT_CNT_MAP["$IP"]}) over limit($REBOOT_LIMIT)"
                                REBOOT_FLAG=1
                            else
                                logger -p local1.info "[$tag:$LINENO] [$IFACE] restarting $ACTIVE_BRIDGE (${REBOOT_CNT_MAP["$IP"]})"
                                systemctl restart "$ACTIVE_BRIDGE"
                            fi
                        else
                            logger -p local1.warn "[$tag:$LINENO] [$IFACE] no active wifi_bridge@ service found, reboot err clear"
                            REBOOT_CNT_MAP["$IP"]=0
                        fi
                    fi
                fi
            fi
        else
            logger -p local1.info "[$tag:$LINENO] [$IFACE] arping from $SRC_IP to $IP successful"
            ERR_CNT_MAP["$IP"]=0
            INIT_CNT_MAP["$IP"]=0
            REBOOT_CNT_MAP["$IP"]=0
        fi
    done


    IP_LIST_NEW="$IP_LIST"

    #if [ "$REBOOT_CNT" -ge "$REBOOT_LIMIT" ]; then
    #    logger -p local0.emerg "[$IFACE] reboot because of error over($REBOOT_LIMIT * $LIMIT_CNT)"         
    #    REBOOT_FLAG=1
    #fi

    #if [ "$ERR_CNT" -ge 1 ]; then
    #    sleep 2
    #else
    #sleep 3
    #fi

    sleep $INTERVAL

    if [ "$REBOOT_FLAG" -eq 1 ]; then
        logger -p local0.emerg "[$tag:$LINENO] [$IFACE] reboot because arp/route is not recovery"
        sleep $INTERVAL
        reboot
    fi
done
