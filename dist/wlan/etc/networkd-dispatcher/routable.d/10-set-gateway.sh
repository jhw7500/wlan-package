#!/bin/bash
tag=$(basename "$0")
#env > /tmp/netdisp_env.txt
#set > /tmp/netdisp_set.txt
#echo "$@" > /tmp/netdisp_args.txt
#IFACE="$1"
#IFACE="${INTERFACE:-$1}"
#echo "IFACE : $IFACE"
logger -p local0.info "[$tag:$LINENO] [$IFACE] routable event on $IFACE"

if [ "$IFACE" == "mlan0" ]; then
    # BSSID 조회에 `iw dev link` 를 쓰지 않는다 — moal 은 연결 중에도 "Not connected." 를
    # 반환할 수 있고(2026-07-29 실측), 그러면 BSSID 가 빈 값이 되어 아래 *) 분기로 떨어져
    # **default route 가 fallback GW 로 조용히 오설정**된다. wlan_link_lib 가 wpa_cli status →
    # station dump 순으로 조회한다.
    # shellcheck source=/dev/null
    if . /usr/local/scripts/wlan_link_lib.sh 2>/dev/null; then
        BSSID=$(wlan_bssid "$IFACE")
    else
        logger -p local0.err "[$tag:$LINENO] [$IFACE] wlan_link_lib.sh load failed; BSSID unavailable"
        BSSID=""
    fi
    case "$BSSID" in
        00:80:4c:e7:1b:ef | 02:80:4c:e7:1b:f0 )
            GW="192.168.4.1"
            ;;
        04:ba:d6:ec:0b:00 | 04:ba:d6:ec:0b:08 )
            GW="192.168.4.50"
            ;;
        *)
            GW="192.168.254.254"
            ;;
    esac
    ip route replace default via "$GW" dev "$IFACE"
    logger -p local0.info "[$tag:$LINENO] [$IFACE] set GW to $GW (BSSID: $BSSID)"
fi
