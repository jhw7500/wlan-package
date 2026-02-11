#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1
MODULE_NAME="moal"

MAX_UNSTABLE_DURATION=4
UNSTABLE_START=0
LIMIT_CNT=3
ERR_CNT=0
STATE=""
PRE_STATE=""
PCI_BUS=""
REBOOT_F=0

# Reboot cooldown configuration
REBOOT_COUNT_FILE="/var/log/cantops/reboot_count_${IFACE}"
MAX_REBOOT_COUNT=3
REBOOT_COOLDOWN_SEC=300  # 5 minutes

cleanup() {
    logger -p local0.info "[$tag:$LINENO] [$IFACE] stop"
    exit 0
}
trap cleanup INT TERM

LOG_DIR="/var/log/cantops/err"

mkdir -p "$LOG_DIR"

logger -p local0.info "[$tag:$LINENO] [$IFACE] start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
elif [ "$IFACE" == "mlan0" ]; then
    PCI_BUS="/sys/bus/pci/devices/0000:01:00.0"
elif [ "$IFACE" == "mlan1" ]; then
    PCI_BUS="/sys/bus/pci/devices/0000:01:00.1"
fi

get_state() {
    #wpa_cli -i "$IFACE" status | grep "^wpa_state=" | cut -d= -f2
    #iw "$IFACE" link | grep 'Connected to' >/dev/null && echo "COMPLETED" || echo "DISCONNECTED"
    cat /sys/class/net/"$IFACE"/operstate
}

is_wpa_active() {
    systemctl is-active --quiet "wpa_supplicant@${IFACE}.service"
}

is_connected() {
    local state
    state=$(get_state)
    [[ "$state" == "COMPLETED" ]]
}

if [ "$IFACE" != "eth0" ]; then
    for i in {1..3}; do
        if lsmod |grep -q "^$MODULE_NAME"; then
            logger -p local0.info "[$tag:$LINENO] [$IFACE] $MODULE_NAME is loading..."
            break
        fi
        sleep 5
    done
fi

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
                CAUSE="PCI link"
            else
                CAUSE="Wifi F/W"
            fi
            logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot because $CAUSE error ($ERR_CNT > $LIMIT_CNT)"
            print red "Reboot because $CAUSE error ($ERR_CNT > $LIMIT_CNT)"
            #logger -p local0.info "[$tag:$LINENO] [$IFACE] dmesg |tail -1000 > $LOG_FILE"
            #dmesg |tail -1000 > "$LOG_FILE"
            #LOG_FILE="$LOG_DIR/${TIMESTAMP}_jo.log"
            logger -p local0.info "[$tag:$LINENO] [$IFACE] saving kernel logs to '$LOG_FILE'"
            #journalctl -k --since "1 min ago" > "$LOG_FILE"
            dmesg > $LOG_FILE
            /usr/local/scripts/journald_snapshot.sh
            sync
            REBOOT_F=1
        fi
    else
        ERR_CNT=0
        if ! is_wpa_active; then
            #log "wpa_supplicant@${IFACE}.service not active  ^`^t waiting..."
            UNSTABLE_START=0
            #sleep $CHECK_INTERVAL
            sleep 3
            continue
        fi

        STATE=$(get_state)
        TIMESTAMP=$(date +%s)

        if [[ "$STATE" == "DISCONNECTED" || "$STATE" == "SCANNING" || "$STATE" == "down" ]]; then
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
                #log "State=$STATE for ${DURATION}s  ^f^r triggering reconnect on $IFACE"
                #wpa_cli -i "$IFACE" reconnect
                UNSTABLE_START=0
            fi
        else
            UNSTABLE_START=0
        fi
    fi

    sleep 5

    if (( REBOOT_F == 1 )); then
        # Check reboot cooldown and count
        CURRENT_TIME=$(date +%s)

        if [ -f "$REBOOT_COUNT_FILE" ]; then
            read -r LAST_REBOOT_TIME REBOOT_COUNT < "$REBOOT_COUNT_FILE"
            TIME_DIFF=$((CURRENT_TIME - LAST_REBOOT_TIME))

            if (( TIME_DIFF < REBOOT_COOLDOWN_SEC )); then
                REBOOT_COUNT=$((REBOOT_COUNT + 1))
                logger -p local0.warning "[$tag:$LINENO] [$IFACE] Reboot attempt $REBOOT_COUNT within cooldown period"

                if (( REBOOT_COUNT >= MAX_REBOOT_COUNT )); then
                    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Reboot loop detected ($REBOOT_COUNT reboots), disabling auto-reboot"
                    print red "Reboot loop detected, manual intervention required"
                    REBOOT_F=0
                    ERR_CNT=0
                    sleep 60
                    continue
                fi
            else
                # Cooldown expired, reset count
                REBOOT_COUNT=1
            fi
        else
            REBOOT_COUNT=1
        fi

        # Save reboot info
        mkdir -p "$(dirname "$REBOOT_COUNT_FILE")"
        echo "$CURRENT_TIME $REBOOT_COUNT" > "$REBOOT_COUNT_FILE"

        logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Initiating reboot (attempt $REBOOT_COUNT/$MAX_REBOOT_COUNT)"
        sync
        ERR_CNT=0
        REBOOT_F=0
        reboot
    fi
done
