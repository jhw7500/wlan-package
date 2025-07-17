#!/bin/bash
tag=$(basename "$0")
IFACE=$1
ERR_CNT=0
ERR_LIMIT=4
INIT_CNT=0
INIT_LIMIT=2
GATEWAY=""
GATEWAY2=""
RET=0
OUTPUT=""
INTERVAL=5

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

get_ipaddr() {
    ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1
}


if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

if [[ "$IFACE" == "mlan0" ]]; then
    CONF_FILE=/etc/systemd/network/20-mlan0.network
fi

if [[ "$IFACE" == "mlan1" ]]; then
    CONF_FILE=/etc/systemd/network/21-mlan1.network
fi

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

#sleep 1

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi ping start"

#IP_ADDR=$(ip -4 addr show dev "$IFACE" | awk '/inet / {print $2}' | cut -d/ -f1)
GATEWAY=$(grep -E '^Gateway=' "$CONF_FILE" | head -n1 | cut -d= -f2)
IP_ADDR=$(grep -E '^Address=' "$CONF_FILE" | head -n1 | cut -d= -f2)
SRC_IP=$(grep -E '^Address=' "$CONF_FILE" | head -n1 | cut -d= -f2 | cut -d/ -f1)
#SRC_IP=$(ip -4 -o addr show dev "$SRC_IFACE" | awk '{print $4}' | cut -d/ -f1)
#GATEWAY=192.168.4.2
#IP_ADDR=$(get_ipaddr)
#GATEWAY=$(get_gateway)

logger -p local0.notice "[$tag:$LINENO] [$IFACE] IP : $IP_ADDR, SRC_IP : $SRC_IP, Gateway : $GATEWAY"

#arping -I $IFACE -s $IP_ADDR $GATEWAY -q

while true; do
    #sleep 3
    GATEWAY=$(grep -E '^Gateway=' "$CONF_FILE" | head -n1 | cut -d= -f2)
    GATEWAY2=$(ip route | awk '/^default/ && /mlan0/ {print $3}')

    if [ "$IFACE" != "eth0" ]; then
        if ! is_wpa_active || ! is_connected; then
            sleep 3
            continue
        #else
        #    echo "muyaho"
        fi
    fi

    if [ -z "$GATEWAY" ] || [ -z "$GATEWAY2" ]; then
        sleep 5
        continue
    fi

    SRC_IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1)

:<<'END'
    GATEWAY=$(get_gateway)
    IP_ADDR=$(get_ipaddr)

    if [ "$GATEWAY" != "$PRE_GATEWAY" ]; then
        logger -p local1.info "[$tag:$LINENO] [$IFACE] $IP_ADDR Gateway change from $PRE_GATEWAY to $GATEWAY"
    fi

    PRE_GATEWAY=$GATEWAY

    if [ -z "$GATEWAY" ]; then
        sleep 1
        continue
    fi
END

    RET=0
    if [ -n "$GATEWAY" ]; then
        if [[ -n "$SRC_IP" ]]; then
            CMD="arping -I $IFACE -s $SRC_IP -c 1 -w 2 $GATEWAY"
        else
            CMD="arping -I $IFACE -c 1 -w 2 $GATEWAY"
        fi
        OUTPUT=$($CMD 2>&1)
        RET=$?
        if [ $RET -eq 0 ]; then
            logger -p local1.info "[$tag:$LINENO] [$IFACE] success($CMD)"
        fi
    fi

    if [ -n "$GATEWAY2" ] && [ "$GATEWAY" != "$GATEWAY2" ]; then
        if [[ -n "$SRC_IP" ]]; then
            CMD="arping -I $IFACE -s $SRC_IP -c 1 -w 2 $GATEWAY2"
        else
            CMD="arping -I $IFACE -c 1 -w 2 $GATEWAY2"
        fi
        OUTPUT=$($CMD 2>&1)
        RET=$?
        if [ $RET -eq 0 ]; then
            logger -p local1.info "[$tag:$LINENO] [$IFACE] success($CMD)"
        fi
    fi

    OUTPUT=$($CMD 2>&1)
    RET=$?

    #if echo "$OUTPUT" | grep -q "Received 0"; then
    if [ $RET -eq 0 ]; then
        #logger -p local1.info "[$tag:$LINENO] [$IFACE] success($CMD)"
        ERR_CNT=0
    else
        #logger -p local1.err "[$tag:$LINENO] [$IFACE] arping to $IP failed: no reply"
        if ping -I "$IFACE" -c 1 -W 2 "$GATEWAY" > /dev/null 2>&1; then
            logger -p local1.info "[$tag:$LINENO] [$IFACE] arping to $GATEWAY failed but success(ping -I $IFACE -c 1 -W 2 $GATEWAY)"
            ERR_CNT=0
        else
            if ping -I "$IFACE" -c 1 -W 2 "$GATEWAY2" > /dev/null 2>&1; then
                logger -p local1.info "[$tag:$LINENO] [$IFACE] arping to $GATEWAY2 failed but success(ping -I $IFACE -c 1 -W 2 $GATEWAY2)"
                ERR_CNT=0
            else
                ((ERR_CNT++))
                logger -p local1.info "[$tag:$LINENO] [$IFACE] ping err to Gateway $GATEWAY, $GATEWAY2 ($ERR_CNT)"
                if [ "$ERR_CNT" -gt "$ERR_LIMIT" ]; then
                    ((INIT_CNT++))
                    if [ "$INIT_CNT" -gt "$INIT_LIMIT" ]; then
                        #logger -p local0.err "[$tag:$LINENO] [$IFACE] systemd-networkd reset because INIT_CNT($INIT_CNT) over INIT_LIMIT($INIT_LIMIT)"
                        #logger -p local1.err "[$tag:$LINENO] [$IFACE] systemd-networkd reset because INIT_CNT($INIT_CNT) over INIT_LIMIT($INIT_LIMIT)"
                        #systemctl restart systemd-networkd
                        INIT_CNT=0
                        sleep 1
                    fi
                    logger -p local0.err "[$tag:$LINENO] [$IFACE] wifi bridge reset because ERR_CNT($ERR_CNT) over ERR_LIMIT($ERR_LIMIT) INIT_CNT($INIT_CNT)"
                    logger -p local1.err "[$tag:$LINENO] [$IFACE] wifi bridge reset because ERR_CNT($ERR_CNT) over ERR_LIMIT($ERR_LIMIT) INIT_CNT($INIT_CNT)"
                    systemctl restart wifi_bridge@$IFACE
                    ERR_CNT=0
                fi
            fi
        fi
    fi

    #logger -p local1.info "[$tag:$LINENO] [$IFACE] ret : $RET"

    #arping -c 1 -w 2 -I $IFACE $GATEWAY
    sleep $INTERVAL

done
