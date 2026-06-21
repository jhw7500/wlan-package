#!/bin/bash

tag=$(basename "$0")
key=LOG

IFACE="${1:-}"
if [ -z "$IFACE" ]; then
    echo "usage: $0 <iface>" >&2
    logger -p local0.err "[$tag] usage: $0 <iface>"
    exit 2
fi
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
RECONFIGURE_GRACE_SEC=20

# Load from JSON config
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    BUS_TYPE=$(jq -r '.global.BUS_TYPE // "pcie"' "$WIFI_INIT_CONF_JSON")
    LIMIT_CNT=$(jq -r ".${IFACE}.checker.LIMIT_CNT // 3" "$WIFI_INIT_CONF_JSON")
    MAX_UNSTABLE_DURATION=$(jq -r ".${IFACE}.checker.MAX_UNSTABLE_DURATION // 10" "$WIFI_INIT_CONF_JSON")
    MAX_REBOOT_COUNT=$(jq -r ".${IFACE}.checker.MAX_REBOOT_COUNT // 3" "$WIFI_INIT_CONF_JSON")
    REBOOT_COOLDOWN_SEC=$(jq -r ".${IFACE}.checker.REBOOT_COOLDOWN_SEC // 300" "$WIFI_INIT_CONF_JSON")
    MIN_UPTIME_SEC=$(jq -r ".${IFACE}.checker.MIN_UPTIME_SEC // 120" "$WIFI_INIT_CONF_JSON")
    FAULT_REASSOC_CNT=$(jq -r ".${IFACE}.checker.FAULT_REASSOC_CNT // 2" "$WIFI_INIT_CONF_JSON")
    FAULT_RESTART_CNT=$(jq -r ".${IFACE}.checker.FAULT_RESTART_CNT // 4" "$WIFI_INIT_CONF_JSON")
    FAULT_REBOOT_CNT=$(jq -r ".${IFACE}.checker.FAULT_REBOOT_CNT // 6" "$WIFI_INIT_CONF_JSON")
    RECONFIGURE_GRACE_SEC=$(jq -r ".${IFACE}.checker.RECONFIGURE_GRACE_SEC // 20" "$WIFI_INIT_CONF_JSON")
fi

# Disconnect 복구 사다리: MAX_UNSTABLE_DURATION 후 reassociate(가벼움),
# RESTART_DURATION(=3배) 후에도 무진행이면 wpa_supplicant 재시작. AP 부재로 인한
# 무한 재시작/재부팅을 피하려 disconnect 경로는 reboot까지 가지 않는다.
# jq 실패(손상/빈 JSON 등)로 MAX_UNSTABLE_DURATION이 빈 값/비숫자가 되면 산술이 0이 되어
# 무의미한 즉시 재시작을 유발 → 숫자 검증 후 RESTART_DURATION 계산.
[[ "$MAX_UNSTABLE_DURATION" =~ ^[0-9]+$ ]] || MAX_UNSTABLE_DURATION=10
[[ "$RECONFIGURE_GRACE_SEC" =~ ^[0-9]+$ ]] || RECONFIGURE_GRACE_SEC=20
RESTART_DURATION=$((MAX_UNSTABLE_DURATION * 3))

UNSTABLE_START=0
REASSOC_DONE=0   # 재연결/재시작 시 리셋; 한 unstable 윈도우 내 reassociate 1회만 발화하도록 가드
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

# reconfigure(런타임 conf 재로드)는 재연결(disconnect→reconnect)을 유발한다. 이 정당한
# 과도기를 unstable 사다리가 끊지 않도록, reconfigure 트리거가 직전에 이 flag를 touch하고
# checker는 flag mtime이 TTL(RECONFIGURE_GRACE_SEC) 내면 사다리를 억제한다.
RECONFIGURE_GRACE_FLAG="/run/wifi/${IFACE}.reconfigure-grace"
reconfigure_grace_active() {
    [ -f "$RECONFIGURE_GRACE_FLAG" ] || return 1
    local mt now
    mt=$(stat -c %Y "$RECONFIGURE_GRACE_FLAG" 2>/dev/null) || return 1
    now=$(date +%s)
    (( now - mt >= 0 && now - mt < RECONFIGURE_GRACE_SEC ))
}

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

# 능동 연결 진행 중(auth/assoc/handshake) 여부. 이 상태를 reassoc/restart로 끊으면
# 연결이 무산되므로 개입을 보류한다. SCANNING/DISCONNECTED 등은 진행으로 보지 않음(사다리 적용).
wpa_handshake_in_progress() {
    local s
    s=$(wpa_cli -i "$IFACE" status 2>/dev/null | grep "^wpa_state=" | cut -d= -f2)
    case "$s" in
        AUTHENTICATING|ASSOCIATING|ASSOCIATED|4WAY_HANDSHAKE|GROUP_HANDSHAKE) return 0 ;;
        *) return 1 ;;
    esac
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
            REASSOC_DONE=0
            FAULT_CNT=0
            sleep 3
            continue
        fi

        STATE=$(get_state)
        TIMESTAMP=$(date +%s)

        if [[ "$STATE" == "DISCONNECTED" || "$STATE" == "SCANNING" || "$STATE" == "down" ]]; then
            FAULT_CNT=0
            if reconfigure_grace_active; then
                # reconfigure 재연결 과도기 — 정당한 재연결을 끊지 않도록 타이머/사다리 억제
                UNSTABLE_START=0
                REASSOC_DONE=0
            else
                if [[ $UNSTABLE_START -eq 0 ]]; then
                    UNSTABLE_START=$TIMESTAMP
                    REASSOC_DONE=0
                fi

                DURATION=$((TIMESTAMP - UNSTABLE_START))

                # 능동 연결 진행 중(auth/assoc/handshake)이면 개입 보류 — 진행 중 연결을 끊으면
                # 오히려 연결이 무산된다. SCANNING/DISCONNECTED 등 무진행 상태에서만 사다리 적용.
                # 단, RESTART_DURATION을 넘도록 handshake 상태에 멈춰(stuck) 있으면 wedge로 보고
                # 재시작까지 escalate한다(무한 hold 방지). UNSTABLE_START 타이머는 handshake 동안에도
                # 계속 누적되므로, handshake 실패/정체 시 적절한 단계로 곧바로 escalate된다.
                if wpa_handshake_in_progress && (( DURATION < RESTART_DURATION )); then
                    : # 정상 진행 중 → 타이머 유지, 개입 없음
                elif (( DURATION >= RESTART_DURATION )); then
                    # 2차(무거움): 장시간 미연결·무진행 → wpa_supplicant 재시작
                    logger -p local0.err "[$tag:$LINENO] [$IFACE] restart wpa_supplicant@$IFACE (disconnected ${DURATION}s >= ${RESTART_DURATION}s, no progress)"
                    wifi $IFACE restart
                    UNSTABLE_START=0
                    REASSOC_DONE=0
                elif (( DURATION >= MAX_UNSTABLE_DURATION )) && (( REASSOC_DONE == 0 )); then
                    # 1차(가벼움): reassociate 먼저 (wpa 프로세스/상태 유지)
                    logger -p local0.warning "[$tag:$LINENO] [$IFACE] reassociate (disconnected ${DURATION}s >= ${MAX_UNSTABLE_DURATION}s, no progress)"
                    wpa_cli -i "$IFACE" reassociate >/dev/null 2>&1
                    REASSOC_DONE=1
                fi
            fi
        else
            UNSTABLE_START=0
            REASSOC_DONE=0
            # 연결완료(operstate up) — 단 grace 윈도(TTL) 안에서는 carrier 가 잠깐 up 으로 튀어도
            # flag 를 지우지 않는다. reconfigure 재연결 중 up→down 토글로 grace 가 조기 해제되어
            # 사다리가 false-trigger 되는 것을 막고, TTL 자연 만료에 해제를 위임한다.
            if ! reconfigure_grace_active; then
                rm -f "$RECONFIGURE_GRACE_FLAG" 2>/dev/null || true
            fi

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
