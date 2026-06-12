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

apply_he_bw_omi() {
    # HE(11ax) assoc의 동작 BW 클램프 — OMI는 per-association 상태라 매 연결/
    # 로밍마다 재전송해야 유지된다. 조건식/OMI 인코딩은 wifi.sh radio-apply의
    # HE OMI 블록과 동기 유지. (htcapinfo/vhtcfg는 HE 연결의 BW를 못 줄임)
    local bw mode std nss omi cw out

    [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1 || return 0

    bw=$(jq -r ".${IFACE}.radio.bw // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    case "$bw" in
        20) cw=0 ;;
        40) cw=1 ;;
        *)  return 0 ;;
    esac

    # HE 가능 조건: mode=ax 또는 (미설정 && mlan1 아님 && STANDARD∉{n,ac})
    mode=$(jq -r ".${IFACE}.radio.mode // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    if [ "$mode" != "ax" ]; then
        [ -n "$mode" ] && return 0
        [ "$IFACE" = "mlan1" ] && return 0
        std=$(jq -r ".${IFACE}.STANDARD // .global.STANDARD // empty" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
        case "$std" in
            n|ac) return 0 ;;
        esac
    fi

    # NSS: htstreamcfg GET 1순위(양 칩 동일 포맷), BOARD_TYPE fallback
    # (imx93/IW612=1x1, 그 외/9098=2x2). 잘못된 NSS는 스트림까지 클램프함.
    nss=2
    out=$(mlanutl "$IFACE" htstreamcfg 2>/dev/null)
    case "$out" in
        *1x1*) nss=1 ;;
        *2x2*) nss=2 ;;
        *)
            case "$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON" 2>/dev/null)" in
                imx93*) nss=1 ;;
            esac
            ;;
    esac

    # OM Control: B0-2 RxNSS(NSS-1), B3-4 ChWidth(0=20,1=40), B6 TxNSTS(NSS-1).
    # tx_option=0: FW가 QoS NULL을 자체 전송하므로 별도 트래픽 불필요.
    omi=$(printf '0x%02X' $(( (nss - 1) | (cw << 3) | ((nss - 1) << 6) )))
    logger -p local0.info "[$tag] [$IFACE] he bw clamp: tx_omi $omi (bw=$bw nss=$nss)"
    mlanutl "$IFACE" 11axcmd tx_omi "$omi" 0 0 > /dev/null 2>&1 || \
        logger -p local0.err "[$tag:$LINENO] [$IFACE] tx_omi $omi failed (non-HE assoc or FW reject)"
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
    apply_he_bw_omi
    run_on_connect "$initial_bssid"
fi

iw event -t 2>&1 | while IFS= read -r line; do
    case "$line" in
        *"$IFACE"*"connected to"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] CONNECTED bssid=$bssid"
            apply_mcs_tier
            apply_he_bw_omi
            run_on_connect "$bssid"
            ;;
        *"$IFACE"*"roamed to"*)
            # FW 주도 로밍은 "connected to"가 아닌 "roamed to"로 표면화됨 —
            # per-association 상태(mcstier FW 상태, OMI 클램프)를 재적용한다.
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] ROAMED bssid=$bssid"
            apply_mcs_tier
            apply_he_bw_omi
            ;;
        *"$IFACE"*"disconnected"*)
            reason=$(echo "$line" | sed -n 's/.*reason: \([0-9]*\).*/\1/p')
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DISCONNECTED reason=$reason"
            ;;
        *"$IFACE"*"deauth"*)
            bssid=$(echo "$line" | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
            logger -p local0.info "[$tag:$LINENO] [$IFACE] DEAUTH bssid=$bssid"
            ;;
    esac
done
