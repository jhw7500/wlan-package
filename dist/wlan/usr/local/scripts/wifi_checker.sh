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
PCI_BUS=""

cleanup() {
    logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_checker stop"
    exit 0
}
trap cleanup INT TERM


LOG_DIR="/var/log/cantops/err"

mkdir -p "$LOG_DIR"

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_checker"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
elif [ "$IFACE" == "mlan0" ]; then
    PCI_BUS="/sys/bus/pci/devices/0000:01:00.0"
elif [ "$IFACE" == "mlan1" ]; then
    PCI_BUS="/sys/bus/pci/devices/0000:01:00.1"
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

sleep 10

while true; do
    #sleep 3
    if [[ "$IFACE" == "eth0" ]]; then
          #STATE=$(jq -r '.eth_stats.phy.link' "/var/log/cantops/json/eth0/link.json")
          STATE=$(cat /sys/class/net/eth0/operstate)
          if [[ "$STATE" == "up" && "$PRE_STATE" != "up" ]]; then
              logger -p local0.info "[$tag:$LINENO] [$IFACE] link change down -> up"
              #systemctl stop wifi_bridge@mlan0
              #sleep 0.5
              #systemctl start wifi_bridge@mlan0
              #ACTIVE_BRIDGE=$(systemctl list-units --type=service --state=running | grep -oE 'wifi_bridge@[^ ]+')
              #if [[ -n "$ACTIVE_BRIDGE" ]]; then
              #    logger -p local0.info "[$tag:$LINENO] [$IFACE] $ACTIVE_BRIDGE restart"
              #    touch /tmp/bridge_en
              #    systemctl restart $ACTIVE_BRIDGE
              #fi
          elif [[ "$STATE" == "down" && "$PRE_STATE" == "up" ]]; then
              logger -p local0.info "[$tag:$LINENO] [$IFACE] link change up -> down"
          fi
          PRE_STATE=$STATE
          sleep 1
          continue
    elif [[ ! -d /sys/class/net/$IFACE ]]; then
        ((ERR_CNT++))
        logger -p local0.err "[$tag:$LINENO] [$IFACE] is not exist becase F/W dump...(ERR_CNT:$ERR_CNT)"
        if [ "$ERR_CNT" -gt "$LIMIT_CNT" ]; then
            TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
            LOG_FILE="$LOG_DIR/module_${TIMESTAMP}.log"
            if [ ! -d "$PCI_BUS" ]; then
                logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot because PCI link error ($ERR_CNT > $LIMIT_CNT)"
            else
                logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot because WiFi F/W error ($ERR_CNT > $LIMIT_CNT)"
            fi
            #logger -p local0.info "[$tag:$LINENO] [$IFACE] dmesg |tail -1000 > $LOG_FILE"
            #dmesg |tail -1000 > "$LOG_FILE"
            #LOG_FILE="$LOG_DIR/${TIMESTAMP}_jo.log"
            logger -p local0.info "[$tag:$LINENO] [$IFACE] saving kernel logs to '$LOG_FILE'"
            #journalctl -k --since "1 min ago" > "$LOG_FILE"
            dmesg > $LOG_FILE
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
            logger -p local0.err "[$tag:$LINENO] [$IFACE] restart wpa_supplicant@$IFACE because wifi is not connected during $MAX_UNSTABLE_DURATION" 
            #wpa_cli disable_network 0
            wifi $IFACE restart
            #wpa_cli enable_network 0
            #systemctl restart wifi_bridge@$IFACE
            #log "State=$STATE for ${DURATION}s → triggering reconnect on $IFACE"
            #wpa_cli -i "$IFACE" reconnect
            UNSTABLE_START=0
        fi
    else
        UNSTABLE_START=0
    fi
#END

:<<'END'
    PRE_STATE=$STATE
    LINK_OUTPUT=$(iw "$IFACE" link 2>&1)
    #if echo "$LINK_OUTPUT" | grep -q "Connected to" && echo "$LINK_OUTPUT" | grep -q "command failed" && echo "$LINK_OUTPUT" | grep -q "Not connected"; then
    if echo "$LINK_OUTPUT" | grep -q "command failed" || echo "$LINK_OUTPUT" | grep -q "Not connected"; then
        logger -p local0.err "[$tag:$LINENO] [$IFACE] Detected invalid link state. Triggering reconnect..."
        wpa_cli -i "$IFACE" disconnect
        sleep 1
        wpa_cli -i "$IFACE" reconnect
        sleep 2
        systemctl restart wifi_bridge@$IFACE
    fi
END
    sleep 1
done
