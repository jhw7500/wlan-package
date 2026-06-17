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
        --netid) [ $# -gt 1 ] || { echo "opc_wlan_apply: --netid requires an argument" >&2; exit 2; }
                 NETID="$2"; shift 2 ;;
        freq)    [ $# -gt 1 ] || { echo "opc_wlan_apply: freq requires an argument" >&2; exit 2; }
                 FREQS="$2"; shift 2 ;;
        ssid)    [ $# -gt 1 ] || { echo "opc_wlan_apply: ssid requires an argument" >&2; exit 2; }
                 SSID="$2"; HAVE_SSID=1; shift 2 ;;
        *)       echo "opc_wlan_apply: unknown arg '$1'" >&2; exit 2 ;;
    esac
done
[ -n "$FREQS" ] || [ "$HAVE_SSID" = 1 ] || { echo "opc_wlan_apply: nothing to apply (need freq and/or ssid)" >&2; exit 2; }

wcli() { wpa_cli -i "$IFACE" "$@"; }
wcli_ok() { [ "$(wcli "$@" 2>/dev/null)" = "OK" ]; }

# ctrl interface 가용 확인 (wpa_supplicant 미동작이면 3).
wcli ping >/dev/null 2>&1 || { echo "opc_wlan_apply: wpa_cli ctrl unavailable for $IFACE" >&2; exit 3; }

# Clear any stale global (outside-network) freq_list that would otherwise cap
# scanning to old frequencies on upgraded devices. Best-effort: harmless if unset.
wcli set freq_list "" >/dev/null 2>&1 || true

if [ -n "$FREQS" ]; then
    wcli_ok set_network "$NETID" freq_list "$FREQS" || { echo "opc_wlan_apply: set freq_list failed" >&2; exit 4; }
    wcli_ok set_network "$NETID" scan_freq "$FREQS" || { echo "opc_wlan_apply: set scan_freq failed" >&2; exit 4; }
fi
if [ "$HAVE_SSID" = 1 ]; then
    wcli_ok set_network "$NETID" ssid "\"$SSID\"" || { echo "opc_wlan_apply: set ssid failed" >&2; exit 4; }
fi

# 비휘발 영속 (update_config=1; conf 재생성 — 주석/포맷 손실 허용).
wcli_ok save_config || { echo "opc_wlan_apply: save_config failed" >&2; exit 5; }

# 적용 트리거 (결과 미확인, 비치명). 끊김 1회.
wcli reassociate >/dev/null 2>&1 || echo "opc_wlan_apply: warn reassociate command failed (non-fatal)" >&2

exit 0
