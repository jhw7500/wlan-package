#!/bin/sh
# opc_wlan_apply.sh — OPC 무선설정 런타임 적용 (wpa_cli)
#
# 사용: opc_wlan_apply.sh <iface> [--netid N] [freq "<mhz ...>"] [ssid <name>]
#   --netid N : 대상 network 블록 id (기본 0; 단일 블록 전제, 다중은 후속 확장)
#   freq/ssid : 하나 이상 지정. 둘 다면 한 번의 reassociate 로 묶어 끊김 1회.
#
# 책임: 설정 변경 + 적용(reassociate) 트리거까지. 재연결 성공 확인/롤백은 안 함
#       (결과는 WlanStatusChange indication 이 통지). freq/ssid 는 save_config 로 영속.
#
# exit: 0=ok / 2=usage / 3=ctrl_interface 부재 / 4=set_network 실패 / 5=save_config 실패
set -u

IFACE="${1:-}"
[ -n "$IFACE" ] || { echo "usage: $0 <iface> [--netid N] [freq \"<mhz ...>\"] [ssid <name>]" >&2; exit 2; }
shift

NETID=0
FREQS=""
SSID=""
HAVE_SSID=0
while [ $# -gt 0 ]; do
    case "$1" in
        --netid) NETID="${2:-0}"; shift 2 ;;
        freq)    FREQS="${2:-}"; shift 2 ;;
        ssid)    SSID="${2:-}"; HAVE_SSID=1; shift 2 ;;
        *)       echo "opc_wlan_apply: unknown arg '$1'" >&2; exit 2 ;;
    esac
done
[ -n "$FREQS" ] || [ "$HAVE_SSID" = 1 ] || { echo "opc_wlan_apply: nothing to apply (need freq and/or ssid)" >&2; exit 2; }

wc() { wpa_cli -i "$IFACE" "$@"; }
wc_ok() { [ "$(wc "$@" 2>/dev/null)" = "OK" ]; }

# ctrl interface 가용 확인 (wpa_supplicant 미동작이면 3).
wc ping >/dev/null 2>&1 || { echo "opc_wlan_apply: wpa_cli ctrl unavailable for $IFACE" >&2; exit 3; }

if [ -n "$FREQS" ]; then
    wc_ok set_network "$NETID" freq_list "$FREQS" || { echo "opc_wlan_apply: set freq_list failed" >&2; exit 4; }
    wc_ok set_network "$NETID" scan_freq "$FREQS" || { echo "opc_wlan_apply: set scan_freq failed" >&2; exit 4; }
fi
if [ "$HAVE_SSID" = 1 ]; then
    wc_ok set_network "$NETID" ssid "\"$SSID\"" || { echo "opc_wlan_apply: set ssid failed" >&2; exit 4; }
fi

# 비휘발 영속 (update_config=1; conf 재생성 — 주석/포맷 손실 허용).
wc_ok save_config || { echo "opc_wlan_apply: save_config failed" >&2; exit 5; }

# 적용 트리거 (결과 미확인, 비치명). 끊김 1회.
wc reassociate >/dev/null 2>&1 || echo "opc_wlan_apply: warn reassociate command failed (non-fatal)" >&2

exit 0
