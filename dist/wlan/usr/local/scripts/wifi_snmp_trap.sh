#!/bin/sh
# wifi_snmp_trap.sh - CONTEC FXE3000 SNMP 트랩 송신 헬퍼
# Usage: wifi_snmp_trap.sh link up|down
#        wifi_snmp_trap.sh channel <ch>
# wifi_init_conf.json 의 snmp.trap.{enabled,dest,community,version} 를 읽어
# 활성 시 snmptrap 으로 vendor 트랩을 송신한다. 비활성/미설정/실패는 조용히 exit 0.

CONF="${WIFI_INIT_CONF:-/usr/local/etc/wifi_init_conf.json}"
FXE=".1.3.6.1.4.1.672.65"
tag=$(basename "$0")

# 설정·jq 부재 → no-op
[ -f "$CONF" ] && command -v jq >/dev/null 2>&1 || exit 0

enabled=$(jq -r '.snmp.trap.enabled // false' "$CONF" 2>/dev/null)
[ "$enabled" = "true" ] || exit 0

dest=$(jq -r '.snmp.trap.dest // ""' "$CONF" 2>/dev/null)
[ -n "$dest" ] || { logger -p local0.err "[$tag] snmp.trap.dest 미설정 — 트랩 생략"; exit 0; }

comm=$(jq -r '.snmp.trap.community // "public"' "$CONF" 2>/dev/null)
ver=$(jq -r '.snmp.trap.version // "2c"' "$CONF" 2>/dev/null)

# snmptrap 송신(dry-run 시 echo). 첫 인자 '' = uptime(자동).
send() {
    if [ "${WIFI_SNMP_TRAP_DRYRUN:-0}" = "1" ]; then
        echo "snmptrap -v$ver -c $comm $dest $*"
        return 0
    fi
    snmptrap -v"$ver" -c "$comm" "$dest" "$@" 2>/dev/null \
        || logger -p local0.err "[$tag] snmptrap 송신 실패 (dest=$dest)"
}

# (Task 2/3 에서 case 추가)
case "$1" in
    *) exit 0 ;;
esac
