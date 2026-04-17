#!/bin/sh
# wifi_event.sh - iw event(netlink) 기반 AP 연결 변화 감지 + on_connect 커맨드 실행
# Usage: wifi_event.sh [interface]

IFACE="${1:-mlan0}"
tag="wifi_event"
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

cleanup() {
    logger -p local0.info "[$tag] [$IFACE] stopped"
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
        logger -p local0.err "[$tag] [$IFACE] mcstiercfg failed"
}

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
        logger -p local0.info "[$tag] [$IFACE] on_connect: $cmd"
        if bash -c "$cmd" > /dev/null 2>&1; then
            logger -p local0.info "[$tag] [$IFACE] on_connect: OK"
        else
            logger -p local0.err "[$tag] [$IFACE] on_connect: FAILED ($?)"
        fi
    done
}

# JSON 퍼미션 체크: group/world-writable이면 임의 명령 삽입 위험
if [ -f "$WIFI_INIT_CONF_JSON" ]; then
    _json_perm=$(stat -c '%a' "$WIFI_INIT_CONF_JSON" 2>/dev/null || echo "000")
    if [ $(( 0${_json_perm} & 022 )) -ne 0 ]; then
        logger -p local0.crit "[$tag] [$IFACE] CRITICAL: $WIFI_INIT_CONF_JSON is group/world-writable (perm=${_json_perm}). Disabling on_connect to prevent command injection."
        run_on_connect() { :; }
    fi
fi

logger -p local0.info "[$tag] [$IFACE] started"

# 초기 상태 점검: wifi_event 시작 전에 이미 연결된 경우(첫 부팅 race) 1회 처리.
# iw event는 구독 이후의 이벤트만 전달하므로, 이미 CONNECTED 상태이면 apply_mcs_tier/run_on_connect가 실행되지 않음.
initial_bssid=$(iw dev "$IFACE" link 2>/dev/null | awk '/Connected to/ {print $3; exit}')
if [ -n "$initial_bssid" ]; then
    logger -p local0.info "[$tag] [$IFACE] INITIAL CONNECTED bssid=$initial_bssid (catch-up)"
    apply_mcs_tier
    run_on_connect "$initial_bssid"
fi

iw event -t 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"$IFACE"*"connected to"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag] [$IFACE] CONNECTED bssid=$bssid"
            apply_mcs_tier
            run_on_connect "$bssid"
            ;;
        *"$IFACE"*"disconnected"*)
            reason=$(echo "$line" | sed -n 's/.*reason: \([0-9]*\).*/\1/p')
            logger -p local0.info "[$tag] [$IFACE] DISCONNECTED reason=$reason"
            ;;
        *"$IFACE"*"deauth"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag] [$IFACE] DEAUTH bssid=$bssid"
            ;;
    esac
done
