#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE=$1
MODULE_NAME="moal"

WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

# Defaults
MAX_UNSTABLE_DURATION=10
LIMIT_CNT=5
MAX_REBOOT_COUNT=3
REBOOT_COOLDOWN_SEC=300
MIN_UPTIME_SEC=120
FAULT_REASSOC_CNT=2
FAULT_RESTART_CNT=4
FAULT_REBOOT_CNT=6

# Load from JSON config
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BUS_TYPE=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON")
    LIMIT_CNT=$(jq -r '.checker.LIMIT_CNT // 3' "$WIFI_INIT_CONF_JSON")
    MAX_UNSTABLE_DURATION=$(jq -r '.checker.MAX_UNSTABLE_DURATION // 10' "$WIFI_INIT_CONF_JSON")
    MAX_REBOOT_COUNT=$(jq -r '.checker.MAX_REBOOT_COUNT // 3' "$WIFI_INIT_CONF_JSON")
    REBOOT_COOLDOWN_SEC=$(jq -r '.checker.REBOOT_COOLDOWN_SEC // 300' "$WIFI_INIT_CONF_JSON")
    MIN_UPTIME_SEC=$(jq -r '.checker.MIN_UPTIME_SEC // 120' "$WIFI_INIT_CONF_JSON")
    FAULT_REASSOC_CNT=$(jq -r '.checker.FAULT_REASSOC_CNT // 2' "$WIFI_INIT_CONF_JSON")
    FAULT_RESTART_CNT=$(jq -r '.checker.FAULT_RESTART_CNT // 4' "$WIFI_INIT_CONF_JSON")
    FAULT_REBOOT_CNT=$(jq -r '.checker.FAULT_REBOOT_CNT // 6' "$WIFI_INIT_CONF_JSON")
fi

UNSTABLE_START=0
ERR_CNT=0
FAULT_CNT=0
STATE=""
PRE_STATE=""
BUS_LINK=""
REBOOT_F=0

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
    if [ "$BUS_TYPE" == "sdio" ]; then
        BUS_LINK="/sys/bus/sdio/devices/mmc2:0001:1"
    else
        BUS_LINK="/sys/bus/pci/devices/0000:01:00.0"
    fi
elif [ "$IFACE" == "mlan1" ]; then
    if [ "$BUS_TYPE" == "sdio" ]; then
        BUS_LINK="/sys/bus/sdio/devices/mmc2:0001:2"
    else
        BUS_LINK="/sys/bus/pci/devices/0000:01:00.1"
    fi
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

is_wpa_completed() {
    local s
    s=$(wpa_cli -i "$IFACE" status 2>/dev/null | grep "^wpa_state=" | cut -d= -f2)
    [[ "$s" == "COMPLETED" ]]
}

# Check if station dump works (returns 0=ok, 1=fault)
check_station_dump() {
    iw "$IFACE" station dump >/dev/null 2>&1
}

if [ "$IFACE" != "eth0" ]; then
    for i in {1..3}; do
        if lsmod |grep -q "^$MODULE_NAME"; then
            #logger -p local0.info "[$tag:$LINENO] [$IFACE] $MODULE_NAME is loading?"
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
            if [ -n "$BUS_LINK" ] && [ ! -d "$BUS_LINK" ]; then
                CAUSE="link"
            else
                CAUSE="fw_crash"
            fi
            logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Requesting reboot via policy: $CAUSE error ($ERR_CNT > $LIMIT_CNT)"
            print red "Requesting reboot via policy: $CAUSE error ($ERR_CNT > $LIMIT_CNT)"
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
            UNSTABLE_START=0
            FAULT_CNT=0
            sleep 3
            continue
        fi

        STATE=$(get_state)
        TIMESTAMP=$(date +%s)

        if [[ "$STATE" == "DISCONNECTED" || "$STATE" == "SCANNING" || "$STATE" == "down" ]]; then
            FAULT_CNT=0
            if [[ $UNSTABLE_START -eq 0 ]]; then
                UNSTABLE_START=$TIMESTAMP
            fi

            DURATION=$((TIMESTAMP - UNSTABLE_START))

            if (( DURATION >= MAX_UNSTABLE_DURATION )); then
                logger -p local0.err "[$tag:$LINENO] [$IFACE] restart wpa_supplicant@$IFACE because wifi is not connected during $MAX_UNSTABLE_DURATION"
                wifi $IFACE restart
                UNSTABLE_START=0
            fi
        else
            UNSTABLE_START=0

            # Station dump fault detection (only when wpa_state=COMPLETED)
            if is_wpa_completed && ! check_station_dump; then
                ((FAULT_CNT++))
                logger -p local0.err "[$tag:$LINENO] [$IFACE] station dump EFAULT (FAULT_CNT=$FAULT_CNT)"

                if (( FAULT_CNT >= FAULT_REBOOT_CNT )); then
                    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] station dump fault persistent ($FAULT_CNT >= $FAULT_REBOOT_CNT), requesting reboot"
                    CAUSE="station_dump_fault"
                    REBOOT_F=1
                    FAULT_CNT=0
                elif (( FAULT_CNT >= FAULT_RESTART_CNT )); then
                    logger -p local0.err "[$tag:$LINENO] [$IFACE] station dump fault ($FAULT_CNT >= $FAULT_RESTART_CNT), restarting wpa_supplicant"
                    wifi $IFACE restart
                elif (( FAULT_CNT >= FAULT_REASSOC_CNT )); then
                    logger -p local0.warning "[$tag:$LINENO] [$IFACE] station dump fault ($FAULT_CNT >= $FAULT_REASSOC_CNT), reassociating"
                    wpa_cli -i "$IFACE" reassociate >/dev/null 2>&1
                fi
            else
                FAULT_CNT=0
            fi
        fi
    fi

    sleep 5

    if (( REBOOT_F == 1 )); then
        reason=${CAUSE:-unknown}
        now=$(date +"%Y-%m-%d %H:%M:%S")
        reboot_at=$(date -d "+${REBOOT_COOLDOWN_SEC} seconds" +"%Y-%m-%d %H:%M:%S")
        logger -p local0.emerg "[$tag:$LINENO] [$IFACE] Requesting reboot via policy (cause=$reason, attempts<=${MAX_REBOOT_COUNT}, cooldown=${REBOOT_COOLDOWN_SEC}s, now=$now, reboot_at=$reboot_at)"
        sync
        ERR_CNT=0
        REBOOT_F=0
        if ! MAX_REBOOT_COUNT="$MAX_REBOOT_COUNT" REBOOT_COOLDOWN_SEC="$REBOOT_COOLDOWN_SEC" MIN_UPTIME_SEC="$MIN_UPTIME_SEC" \
          /usr/local/scripts/wlan_reboot_policy.sh \
            --source wifi_checker \
            --iface "$IFACE" \
            --reason "wifi_checker fatal: $reason"; then
          rc=$?
          logger -p local0.warning "[$tag:$LINENO] [$IFACE] Reboot refused by policy (rc=$rc)"
          sleep 60
        fi
    fi
done
