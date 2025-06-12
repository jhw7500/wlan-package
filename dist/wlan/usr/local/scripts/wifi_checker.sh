#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1
MODULE_NAME="moal"

MAX_UNSTABLE_DURATION=15
UNSTABLE_START=0
LIMIT_CNT=5
ERR_CNT=0

LOG_DIR="/var/log/cantops/module/dmesg"

mkdir -p "$LOG_DIR"

logger -p local0.notice "[$tag:$LINENO] [$IFACE] wifi_checker"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

get_state() {
    wpa_cli -i "$IFACE" status | grep "^wpa_state=" | cut -d= -f2
}

is_wpa_active() {
    systemctl is-active --quiet "wpa_supplicant@${IFACE}.service"
}

sleep 3

#python3 /usr/local/logger/wifi_module_check.py $IFACE

while true; do
    if lsmod |grep -q "^$MODULE_NAME"; then
        logger -p local0.notice "[$tag:$LINENO] [$IFACE] $MODULE_NAME is loading..."
        #sleep 5
        break
    fi
    sleep 5
done

while true; do
    sleep 3

    if [ ! -d /sys/class/net/$IFACE ]; then
        ((ERR_CNT++))
        logger -p local0.err "[$tag:$LINENO] [$IFACE] is not exist becase F/W dump...(ERR_CNT:$ERR_CNT)"
        if [ "$ERR_CNT" -gt "$LIMIT_CNT" ]; then
            logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot because $IFACE is cannot recovery(ERR_CNT:$ERR_CNT > LIMIT_CNT:$LIMIT_CNT)"
            TIMESTAMP=$(date + "%Y%m%d_%H%M%S")
            LOG_FILE="$LOG_DIR/${TIMESTAMP}.log"
            dmesg |tail -1000 > "$LOF_FILE"
            sync
            sleep 3
            reboot
        fi
        continue
    fi

    ERR_CNT=0

    if ! is_wpa_active; then
        #log "wpa_supplicant@${IFACE}.service not active — waiting..."
        UNSTABLE_START=0
        #sleep $CHECK_INTERVAL
        continue
    fi

    STATE=$(get_state)
    TIMESTAMP=$(date +%s)

    if [[ "$STATE" == "DISCONNECTED" || "$STATE" == "SCANNING" ]]; then
        if [[ $UNSTABLE_START -eq 0 ]]; then
            UNSTABLE_START=$TIMESTAMP
        fi

        DURATION=$((TIMESTAMP - UNSTABLE_START))

        if (( DURATION >= MAX_UNSTABLE_DURATION )); then
            logger -p local0.notice  "[$tag:$LINENO] [$IFACE] restart wpa_supplicant@$IFACE because wifi is not connected during $MAX_UNSTABLE_DURATION" 
            systemctl restart wpa_supplicant@$IFACE
            #log "State=$STATE for ${DURATION}s → triggering reconnect on $IFACE"
            #wpa_cli -i "$IFACE" reconnect
            UNSTABLE_START=0
        fi
    else
        UNSTABLE_START=0
    fi

    if [ -d /sys/class/net/$IFACE ]; then
        LINK_OUTPUT=$(iw "$IFACE" link 2>&1)
        if echo "$LINK_OUTPUT" | grep -q "Connected to" && echo "$LINK_OUTPUT" | grep -q "command failed"; then
            logger -p local0.notice "[$tag:$LINENO] [$IFACE] Detected invalid link state. Triggering reconnect..."
            wpa_cli -i "$IFACE" disconnect
            sleep 1
            wpa_cli -i "$IFACE" reconnect
        fi
    fi
done
