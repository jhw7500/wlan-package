#!/bin/bash
# wifi_snmp_trap.sh - CanTops CTS-WLAN SNMP 트랩 송신 헬퍼
# Usage: wifi_snmp_trap.sh link up|down
#        wifi_snmp_trap.sh channel <ch>
# wifi_init_conf.json 의 snmp.trap.{enabled,link,channel,dest,community,version} 를 읽어
# 활성 시 snmptrap 으로 vendor 트랩을 송신한다. 비활성/미설정/실패는 조용히 exit 0.

CONF="${WIFI_INIT_CONF:-/usr/local/etc/wifi_init_conf.json}"
# 트랩은 CanTops 정본 루트(.66620.1 = PEN 66620 · product 1 CTS-WLAN)로만 보낸다.
# 폴링(pass_persist)은 CONTEC .672.65 와 병행하지만 트랩까지 병행하면 두 루트를
# 모두 등록한 NMS 가 같은 이벤트를 중복 수신하므로 트랩은 정본 단독으로 둔다.
CTS=".1.3.6.1.4.1.66620.1"
tag=$(basename "$0")

# 설정·jq 부재 → no-op
[ -f "$CONF" ] && command -v jq >/dev/null 2>&1 || exit 0

# CONF 가 group/world-writable 이면 dest/community 변조로 트랩이 임의 호스트로 송신될 수
# 있어 차단(wifi_event.sh 의 on_connect 퍼미션 가드와 동일 방어 — Claude 리뷰 [MEDIUM]).
_perm=$(stat -c '%a' "$CONF" 2>/dev/null || echo "000")
[ $(( 0${_perm} & 022 )) -eq 0 ] || { logger -p local0.crit "[$tag:$LINENO] $CONF group/world-writable - trap disabled (prevents destination tampering)"; exit 0; }

enabled=$(jq -r '.snmp.trap.enabled // false' "$CONF" 2>/dev/null)
[ "$enabled" = "true" ] || exit 0

# 종류별 스위치 snmp.trap.link·channel(#464 SNMP_TRAP_WLAN·SNMP_TRAP_CHAN). 키가 없으면 켜짐.
# jq 의 `//` 는 false 도 기본값으로 덮으므로 null 만 기본값으로 바꾼다(wifi_apply_enabled.sh get_bool 과 같음).
case "$1" in
    link|channel)
        want=$(jq -r ".snmp.trap.$1 | if . == null then true else . end" "$CONF" 2>/dev/null)
        [ "$want" = "true" ] || exit 0
        ;;
esac

dest=$(jq -r '.snmp.trap.dest // ""' "$CONF" 2>/dev/null)
[ -n "$dest" ] || { logger -p local0.err "[$tag:$LINENO] snmp.trap.dest is unset - trap skipped"; exit 0; }

comm=$(jq -r '.snmp.trap.community // "public"' "$CONF" 2>/dev/null)
ver=$(jq -r '.snmp.trap.version // "2c"' "$CONF" 2>/dev/null)

# snmptrap 송신(dry-run 시 echo). 첫 인자 '' = uptime(자동).
send() {
    if [ "${WIFI_SNMP_TRAP_DRYRUN:-0}" = "1" ]; then
        echo "snmptrap -v$ver -c $comm $dest $*"
        return 0
    fi
    snmptrap -v"$ver" -c "$comm" "$dest" "$@" 2>/dev/null \
        || logger -p local0.err "[$tag:$LINENO] snmptrap send failed (dest=$dest)"
}

case "$1" in
    link)
        case "$2" in
            up)   st=1 ;;
            down) st=2 ;;
            *)    exit 0 ;;
        esac
        send '' "$CTS.1.1.1" \
            "$CTS.3.2.1.1.2" i 2 \
            "$CTS.3.2.1.7.2" i "$st"
        ;;
    channel)
        ch="$2"
        [ -n "$ch" ] || exit 0
        send '' "$CTS.1.1.2" \
            "$CTS.3.3.1.10.2.0" i "$ch"
        ;;
    *) exit 0 ;;
esac
