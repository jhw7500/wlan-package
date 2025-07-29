#!/bin/bash
IFACE=$1
CONF_FILE=""
tag=$(basename "$0")
IP_LIST=""
IP_LIST_NEW=""
TMP_LIST=()
INIT_LIMIT=5
ERR_CNT=0
ERR_LIMIT=1
INIT_CNT=0
REBOOT_CNT=0
REBOOT_LIMIT=2
REBOOT_FLAG=0
INTERVAL=5
BAD_IP_CNT=0
BAD_IP_CNT_LIMIT=2
GATEWAY=""
GATEWAY2=""
CMD=""
if [ -z "$BASH_VERSION" ]; then
  echo "This script requires bash." >&2
  exit 1
fi

declare -A uniq_map
declare -A ERR_CNT_MAP
declare -A INIT_CNT_MAP
declare -A REBOOT_CNT_MAP

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

reset_global_counters() {
    INIT_CNT=0
    ERR_CNT=0
    REBOOT_CNT=0
}

function ip_in_subnet() {
    local ip=$1
    local subnet=$2
    local ip_dec subnet_ip subnet_mask subnet_dec

    IFS=/ read subnet_ip subnet_mask <<< "$subnet"

    ip_dec=$(ip_to_dec "$ip")
    subnet_dec=$(ip_to_dec "$subnet_ip")
    mask_dec=$(( 0xFFFFFFFF << (32 - subnet_mask) & 0xFFFFFFFF ))

    [[ $(( ip_dec & mask_dec )) -eq $(( subnet_dec & mask_dec )) ]]
}

function ip_to_dec() {
    local IFS=.
    read -r a b c d <<< "$1"
    echo $(( (a<<24) + (b<<16) + (c<<8) + d ))
}

reset_all_counters() {
    INIT_CNT=0
    ERR_CNT=0
    REBOOT_CNT=0
    BAD_IP_CNT=0
    declare -gA ERR_CNT_MAP=()
    declare -gA INIT_CNT_MAP=()
    declare -gA REBOOT_CNT_MAP=()
    #logger -p local1.info "[reset_all_counters] All counters and maps reset"
}


logger -p local0.info "[$tag:$LINENO] [$IFACE] arping start"
logger -p local1.info "[$tag:$LINENO] [$IFACE] arping start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    logger -p local1.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

#ip neigh flush dev $IFACE

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


:<<'END'
for i in {2..254}; do
    arping -c 1 -I $IFACE 192.168.4.$i &
done
wait

TARGET=$(ip neigh show dev eth0 | grep REACHABLE | awk '{print $1}')
logger -p local1.info "[$tag:$LINENO] [$IFACE] target : $TARGET"
ip route add $TARGET dev eth0 scope link
END

:<<'END'
sleep 5
TARGET=$(avahi-resolve -n PIM-CAMERA-V016.local |awk '{print $2}')
logger -p local1.info "[$tag:$LINENO] [$IFACE] target : $TARGET"
ip route add $TARGET dev eth0 scope link
arping $TARGET -c 3 -w 2
END

#ip route del 192.168.1.0/24 dev eth0
#ip route add 192.168.4.10 dev scope link

if [[ "$IFACE" == "mlan0" ]]; then
    CONF_FILE=/etc/systemd/network/20-mlan0.network
fi

if [[ "$IFACE" == "mlan1" ]]; then
    CONF_FILE=/etc/systemd/network/21-mlan1.network
fi

if [[ "$IFACE" == "eth0" ]]; then
    CONF_FILE=/etc/systemd/network/22-eth0.network
fi

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

while true; do
    GATEWAY=$(get_gateway)
    GATEWAY2=$(grep -E '^Gateway=' "$CONF_FILE" | head -n1 | cut -d= -f2)
    IP_LIST_NEW=$IP_LIST
    #BAD_IP_LIST=$(ip neigh show dev "$IFACE" | awk '!/lladdr/ {print $1}' | grep -vE '^224\.|^169\.')

:<<'END'
    BAD_IP_LIST=$(ip neigh show dev "$IFACE" | awk '!/lladdr/ {print $1}')
    
    if [ ! -z "$BAD_IP_LIST" ]; then
        logger -p local1.info "[$tag:$LINENO] [$IFACE] ip neigh del $IP dev $IFACE"
        ip neigh del $IP dev $IFACE
    fi
END

    #IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')
    IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}' | grep -vE '^224\.|^169\.')

    #for IP in $IP_LIST; do
        #logger -p local1.info "[$tag:$LINENO] [$IFACE] $IP in $IFACE"
    #done

    if [[ "$IFACE" == "eth0" ]]; then
        #IFACE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oP 'wifi_bridge@\K[^\.]+')
        #SUBNET_CIDR=$(ip -o -f inet addr show "$IFACE_BRIDGE" | awk '{print $4}')
        #IP_LIST=$(ip neigh show dev "$IFACE_BRIDGE" | grep 'lladdr' | awk '{print $1}')
        LINK_STATE=$(jq -r '.eth_stats.phy.link' "/var/log/cantops/json/eth0/link.json")
        if [[ "$LINK_STATE" != "up" ]]; then
            logger -p local1.info "[$tag:$LINENO] [$IFACE] link is down"
            reset_global_counters
            sleep $INTERVAL
            continue
        fi
    else
        if ! is_wpa_active || ! is_connected; then
            reset_global_counters
            sleep $INTERVAL
            continue
:<<'END'
        else
            declare -A uniq_map
            SUBNET_CIDR=$(ip -o -f inet addr show "$IFACE" | awk '{print $4}')
            #IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}')
            for ip in $IP_LIST; do
                #logger -p local1.info "[$tag:$LINENO] [$IFACE] $ip in $IFACE cidr"
                if ip_in_subnet "$ip" "$SUBNET_CIDR"; then
                    if [[ -z "${uniq_map[$ip]}" ]]; then
                        logger -p local1.info "[$tag:$LINENO] [$IFACE] $ip add"
                        TMP_LIST+=("$ip")
                        uniq_map[$ip]=1
                    fi
                fi
            done
            IP_LIST="${TMP_LIST[@]}"
END
        fi
    fi

    #IP_LIST=$(ip neigh show dev "$IFACE" | grep 'lladdr' | awk '{print $1}' | grep -vE '^224\.|^169\.')

    #if [[ "$IFACE" != eth0 ]] && ! is_wpa_active && ! is_connected; then
    #    sleep $INTERVAL
    #    continue
    #fi

    if [ -z "$IP_LIST" ]; then
        if [ ! -z "$GATEWAY" ] || [ ! -z "$GATEWAY2" ]; then
            if [ ! -z "$GATEWAY" ]; then
                logger -p local1.info "[$tag:$LINENO] [$IFACE] ping to Gateway : $GATEWAY"
                ping -I $IFACE -c 1 -w 2 $GATEWAY -q
            fi

            if [ ! -z "$GATEWAY2" ]; then
                logger -p local1.info "[$tag:$LINENO] [$IFACE] ping to FILE Gateway : $GATEWAY2"
                ping -I $IFACE -c 1 -w 2 $GATEWAY2 -q
            fi

            reset_all_counters
            sleep $INTERVAL
            continue
        fi

        #echo "No IP found on $iface"
        ((ERR_CNT++))
        logger -p local1.err "[$tag:$LINENO] [$IFACE] No IP found ($ERR_CNT)"

        if [[ "$ERR_CNT" -gt "$ERR_LIMIT" ]]; then
            ((INIT_CNT++))
            logger -p local1.err "[$tag:$LINENO] [$IFACE] arping err($ERR_CNT) over limit($ERR_LIMIT), init err($INIT_CNT)"
            #logger -p local1.err "[$tag:$LINENO] [$IFACE] route cache and arp table flush"
            #ip neigh flush dev $IFACE
            #ip route flush cache
            #ip -s neigh flush dev $IFACE
            #if [[ "$IFACE" == "eth0" ]]; then
            if [[ "$INIT_CNT" -gt "$INIT_LIMIT" ]]; then
                ((REBOOT_CNT++))
                logger -p local1.err "[$tag:$LINENO] [$IFACE] init err($INIT_CNT) over limit($INIT_LIMIT), reset err($REBOOT_CNT)"
                INIT_CNT=0
                #ACTIVE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oE 'wifi_bridge@[^ ]+')
                IFACE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oP 'wifi_bridge@\K[^\.]+')
                if [ "$REBOOT_CNT" -gt "$REBOOT_LIMIT" ]; then
                    #logger -p local0.err "[$tag:$LINENO] [$IFACE] reset network because reset err($REBOOT_CNT) over limit($REBOOT_LIMIT)"
                    #logger -p local1.err "[$tag:$LINENO] [$IFACE] reset network because reset err($REBOOT_CNT) over limit($REBOOT_LIMIT)"
                    #systemctl restart systemd-networkd
                    REBOOT_CNT=0
                    sleep 1
                fi

                if [[ -n "$IFACE_BRIDGE" ]]; then
                    logger -p local0.err "[$tag:$LINENO] [$IFACE] restarting wifi_bridge@$IFACE_BRIDGE"
                    logger -p local1.err "[$tag:$LINENO] [$IFACE] restarting wifi_bridge@$IFACE_BRIDGE"
                    systemctl restart wifi_bridge@$IFACE_BRIDGE
                else
                    logger -p local0.warn "[$tag:$LINENO] [$IFACE] no active wifi_bridge@ service found"
                    logger -p local1.warn "[$tag:$LINENO] [$IFACE] no active wifi_bridge@ service found"                    
                fi
            fi
            /usr/local/scripts/arping_sweep.sh $IFACE
            ERR_CNT=0
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
            SRC_IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1)
            #logger -p local1.info "IFACE : $IFACE, IP : $SRC_IP"
        fi


        if [[ -n "$SRC_IP" ]]; then
            CMD="arping -I "$IFACE" -s "$SRC_IP" -c 1 -w 2 "$IP" 2>&1"
            #logger -p local1.info "[arping] $IFACE ($SRC_IP) → $TARGET_IP : $OUTPUT"
        else
            CMD="arping -I "$IFACE" -c 1 -w 2 "$IP" 2>&1"
            #logger -p local1.err "[arping] Failed to get source IP from $IFACE"
        fi

        OUTPUT=$(eval "$CMD")

        if echo "$OUTPUT" | grep -q "Received 0"; then
            #logger -p local1.err "[$tag:$LINENO] [$IFACE] arping to $IP failed: no reply"
            if ping -I "$IFACE" -c 1 -W 2 "$IP" > /dev/null 2>&1; then
                logger -p local1.info "[$tag:$LINENO] [$IFACE] ($CMD) failed but success(ping -I $IFACE -c 1 -W 2 $IP)"
                #ERR_CNT_MAP["$IP"]=0
                #INIT_CNT_MAP["$IP"]=0
                #REBOOT_CNT_MAP["$IP"]=0
                reset_all_counters
                sleep $INTERVAL
                continue
            else
                if [ ! -z "$GATEWAY" ]; then
                    if ping -I "$IFACE" -c 1 -W 2 "$GATEWAY" > /dev/null 2>&1; then
                        logger -p local1.info "[$tag:$LINENO] [$IFACE] arping, ping to $IP failed but ping success(ping -I $IFACE -c 1 -W 2 $GATEWAY)"
                        ping -I $IFACE -c 1 -w 2 $GATEWAY -q
                        reset_all_counters
                        sleep $INTERVAL
                        continue
                    else
                        if [ ! -z "$GATEWAY2" ]; then
                            if ping -I "$IFACE" -c 1 -W 2 "$GATEWAY2" > /dev/null 2>&1; then
                                logger -p local1.info "[$tag:$LINENO] [$IFACE] arping, ping to $IP failed but success(ping -I $IFACE -c 1 -W2 $GATEWAY2)"
                                ping -I $IFACE -c 1 -w 2 $GATEWAY2 -q
                                reset_all_counters
                                sleep $INTERVAL
                                continue
                            fi
                        fi
                    fi
                fi

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
                                logger -p local0.err "[$tag:$LINENO] [$IFACE] restarting $ACTIVE_BRIDGE (${REBOOT_CNT_MAP["$IP"]})"
                                logger -p local1.err "[$tag:$LINENO] [$IFACE] restarting $ACTIVE_BRIDGE (${REBOOT_CNT_MAP["$IP"]})"
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
            logger -p local1.info "[$tag:$LINENO] [$IFACE] success($CMD)"
            ERR_CNT_MAP["$IP"]=0
            INIT_CNT_MAP["$IP"]=0
            REBOOT_CNT_MAP["$IP"]=0
        fi
    done


    #IP_LIST_NEW="$IP_LIST"

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
        logger -p local1.emerg "[$tag:$LINENO] [$IFACE] reboot because arp/route is not recovery"
        sleep $INTERVAL
        reboot
    fi
done
