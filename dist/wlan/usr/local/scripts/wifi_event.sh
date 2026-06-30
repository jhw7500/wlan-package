#!/bin/sh
# wifi_event.sh - iw event(netlink) 기반 AP 연결 변화 감지 + on_connect 커맨드 실행
# Usage: wifi_event.sh [interface]

IFACE="${1:-mlan0}"
tag=$(basename "$0")
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
MCS_REASSOC_MARKER="/tmp/.mcstier_reassoc_${IFACE}"

cleanup() {
    logger -p local0.info "[$tag:$LINENO] [$IFACE] stopped"
    exit 0
}

trap cleanup INT TERM

apply_mcs_tier() {
    # Reapply mcstiercfg on every connection (some roams/resets drop FW tier state).
    # Mirrors wifi_init.sh:apply_mcs_tier(). Keep both in sync.
    local enabled ht vht he args=""

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    enabled=$(jq -r ".${IFACE}.mcs_tier.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$enabled" = "true" ] || return 0

    ht=$(jq -r ".${IFACE}.mcs_tier.ht // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    vht=$(jq -r ".${IFACE}.mcs_tier.vht // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    he=$(jq -r ".${IFACE}.mcs_tier.he // \"\"" "$WIFI_INIT_CONF_JSON" 2>/dev/null)

    [ -n "$ht" ] && args="$args ht $ht"
    [ -n "$vht" ] && args="$args vht $vht"
    [ -n "$he" ] && args="$args he $he"

    [ -z "$args" ] && return 0

    logger -p local0.info "[$tag] [$IFACE] mcstiercfg$args"
    mlanutl "$IFACE" mcstiercfg $args > /dev/null 2>&1 || \
        logger -p local0.err "[$tag:$LINENO] [$IFACE] mcstiercfg failed"

    # First assoc after boot negotiates with hw default HE cap (MCS11); the SET
    # above only restores stored user_he_cap. Force one reassoc so the active
    # link re-advertises the limited cap. Marker in /tmp resets on reboot.
    if [ ! -f "$MCS_REASSOC_MARKER" ]; then
        touch "$MCS_REASSOC_MARKER"
        logger -p local0.info "[$tag:$LINENO] [$IFACE] first mcs_tier apply - forcing reassoc"
        sleep 1
        wpa_cli -i "$IFACE" reassociate > /dev/null 2>&1 || \
            logger -p local0.warning "[$tag:$LINENO] [$IFACE] reassociate failed"
    fi
}

# radio.bw(20/40/80) HE 클램프는 OMI 경로를 폐기함(2026-06-15 실기: NXP FW가
# OMI로 STA 동작 BW를 안 바꿈). BW는 htcapinfo/vhtcfg cap에서 파생되며, cap은
# 호스트 usr_* 필드라 roam/reconnect 시 새 assoc IE에 자동 반영될 것으로 기대
# (실기 확인 항목). cap이 roam 후 풀리는 게 확인되면 여기서 cap 재적용 훅 추가.

run_on_connect() {
    local bssid="$1"
    local enabled cmds

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    enabled=$(jq -r ".${IFACE}.on_connect.enabled // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ "$enabled" = "true" ] || return 0

    cmds=$(jq -r ".${IFACE}.on_connect.commands[]? // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    [ -z "$cmds" ] && return 0

    echo "$cmds" | while IFS= read -r cmd; do
        [ -z "$cmd" ] && continue
        logger -p local0.info "[$tag:$LINENO] [$IFACE] on_connect: $cmd"
        if bash -c "$cmd" > /dev/null 2>&1; then
            logger -p local0.info "[$tag:$LINENO] [$IFACE] on_connect: OK"
        else
            logger -p local0.err "[$tag:$LINENO] [$IFACE] on_connect: FAILED ($?)"
        fi
    done
}

# JSON 퍼미션 체크: group/world-writable이면 임의 명령 삽입 위험
if [ -f "$WIFI_INIT_CONF_JSON" ]; then
    _json_perm=$(stat -c '%a' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "000")
    if [ $(( 0${_json_perm} & 022 )) -ne 0 ]; then
        logger -p local0.crit "[$tag:$LINENO] [$IFACE] CRITICAL: $WIFI_INIT_CONF_JSON is group/world-writable (perm=${_json_perm}). Disabling on_connect to prevent command injection."
        run_on_connect() { :; }
    fi
fi

#logger -p local0.info "[$tag:$LINENO] [$IFACE] started"

# 초기 상태 점검: wifi_event 시작 전에 이미 연결된 경우(첫 부팅 race) 1회 처리.
# iw event는 구독 이후의 이벤트만 전달하므로, 이미 CONNECTED 상태이면 apply_mcs_tier/run_on_connect가 실행되지 않음.
initial_bssid=$(iw dev "$IFACE" link 2>/dev/null | awk '/Connected to/ {print $3; exit}')
if [ -n "$initial_bssid" ]; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] INITIAL CONNECTED bssid=$initial_bssid (catch-up)"
    apply_mcs_tier
    run_on_connect "$initial_bssid"
fi

iw event -t 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"$IFACE"*"connected to"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] CONNECTED bssid=$bssid"
            apply_mcs_tier
            run_on_connect "$bssid"
            /usr/local/scripts/wifi_snmp_trap.sh link up
            ;;
        *"$IFACE"*"roamed to"*)
            # FW 주도 로밍 전용 케이스 — cfg80211_roamed 경로는 "roamed to"로
            # 표면화된다. 주력인 수동 로밍(wpa_cli roam — wifi_roam.py:1242)은
            # nl80211 connect 경로(cfg80211_connect_result)라 "connected to"로
            # 표면화되어 위 케이스가 커버한다.
            # per-association FW 상태(mcstier)를 재적용한다. radio.bw cap은
            # 호스트 usr_* 필드라 새 assoc IE에 자동 반영될 것으로 기대(실기 확인).
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] ROAMED bssid=$bssid"
            apply_mcs_tier
            ;;
        *"$IFACE"*"channel switch"*)
            ch=$(iw dev "$IFACE" info 2>/dev/null | sed -n 's/.*channel \([0-9]*\).*/\1/p' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] CH_SWITCH channel=$ch"
            [ -n "$ch" ] && /usr/local/scripts/wifi_snmp_trap.sh channel "$ch"
            ;;
        *"$IFACE"*"disconnected"*)
            reason=$(echo "$line" | sed -n 's/.*reason: \([0-9]*\).*/\1/p')
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DISCONNECTED reason=$reason"
            /usr/local/scripts/wifi_snmp_trap.sh link down
            ;;
        *"$IFACE"*"deauth"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DEAUTH bssid=$bssid"
            /usr/local/scripts/wifi_snmp_trap.sh link down
            ;;
    esac
done
