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

run_on_connect() {
    local bssid="$1"
    local cmds
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

iw event -t 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"$IFACE"*"connected to"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag] [$IFACE] CONNECTED bssid=$bssid"
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
