#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1
MODULE_NAME="moal"

MAX_UNSTABLE_DURATION=15
UNSTABLE_START=0
LIMIT_CNT=5
ERR_CNT=0
STATE=""
PRE_STATE=""

LOG_DIR="/var/log/cantops/dmesg"

mkdir -p "$LOG_DIR"

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_checker"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

get_state() {
    wpa_cli -i "$IFACE" status | grep "^wpa_state=" | cut -d= -f2
}

is_wpa_active() {
    systemctl is-active --quiet "wpa_supplicant@${IFACE}.service"
}

is_connected() {
    local state
    state=$(get_state)
    [[ "$state" == "COMPLETED" ]]
}

sleep 3

#python3 /usr/local/logger/wifi_module_check.py $IFACE

:<<"END"
while true; do
    if lsmod |grep -q "^$MODULE_NAME"; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] $MODULE_NAME is loading..."
        #sleep 5
        break
    fi
    sleep 5
done
END

sleep 5

while true; do
    #sleep 3
    if [[ "$IFACE" ==  "mlan0" && ! -d /sys/class/net/$IFACE ]]; then
        ((ERR_CNT++))
        logger -p local0.err "[$tag:$LINENO] [$IFACE] is not exist becase F/W dump...(ERR_CNT:$ERR_CNT)"
        if [ "$ERR_CNT" -gt "$LIMIT_CNT" ]; then
            logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot because $IFACE is cannot recovery(ERR_CNT:$ERR_CNT > LIMIT_CNT:$LIMIT_CNT)"
            TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
            LOG_FILE="$LOG_DIR/${TIMESTAMP}.log"
            #logger -p local0.info "[$tag:$LINENO] [$IFACE] dmesg |tail -1000 > $LOG_FILE"
            #dmesg |tail -1000 > "$LOG_FILE"
            #LOG_FILE="$LOG_DIR/${TIMESTAMP}_jo.log"
            logger -p local0.info "[$tag:$LINENO] [$IFACE] saving kernel logs to '$LOG_FILE'"
            journalctl -k --since "1 min ago" > "$LOG_FILE"
            sync
            sleep 3
            reboot
        fi
        sleep 3
        continue
    fi

    ERR_CNT=0

#:<<'END'
    if ! is_wpa_active; then
        #log "wpa_supplicant@${IFACE}.service not active — waiting..."
        UNSTABLE_START=0
        #sleep $CHECK_INTERVAL
        sleep 3
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
            logger -p local0.info  "[$tag:$LINENO] [$IFACE] restart wpa_supplicant@$IFACE because wifi is not connected during $MAX_UNSTABLE_DURATION" 
            #wpa_cli disable_network 0
            systemctl stop wpa_supplicant@$IFACE
            sleep 1
            #wpa_cli enable_network 0
            systemctl start wpa_supplicant@$IFACE
            #log "State=$STATE for ${DURATION}s → triggering reconnect on $IFACE"
            #wpa_cli -i "$IFACE" reconnect
            UNSTABLE_START=0
        fi
    else
        UNSTABLE_START=0
    fi
#END
    PRE_STATE=$STATE
    LINK_OUTPUT=$(iw "$IFACE" link 2>&1)
    if echo "$LINK_OUTPUT" | grep -q "Connected to" && echo "$LINK_OUTPUT" | grep -q "command failed"; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Detected invalid link state. Triggering reconnect..."
        wpa_cli -i "$IFACE" disconnect
        sleep 1
        wpa_cli -i "$IFACE" reconnect
        sleep 2
    fi

    sleep 1
done
