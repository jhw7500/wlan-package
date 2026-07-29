#!/bin/bash
# wlan_link_lib.sh 동작 검증 — 모의 wpa_cli / iw 로 계단식 폴백까지 확인한다.
#
# 실기 원문(2026-07-29 cts-wlan)을 그대로 픽스처로 쓴다. station dump 의 signal 줄은
# 탭/공백이 혼재하고("\tsignal:  \t-46 dBm") 바로 다음 줄에 "signal avg:" 가 오므로,
# 파서가 avg 를 오매칭하지 않는지가 핵심 검증 지점이다.
set -u

LIB="$(cd "$(dirname "$0")/.." && pwd)/wlan_link_lib.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok()  { echo "  [PASS] $1"; pass=$((pass+1)); }
ng()  { echo "  [FAIL] $1 — got '$2', want '$3'"; fail=$((fail+1)); }
eq()  { [ "$2" = "$3" ] && ok "$1" || ng "$1" "$2" "$3"; }

# ── 모의 명령 ────────────────────────────────────────────────────────────────
mk_wpa_cli() {  # $1: status 본문 ("" 면 실패로 동작)
    if [ -z "$1" ]; then
        printf '#!/bin/sh\nexit 1\n' > "$TMP/wpa_cli"
    else
        { printf '#!/bin/sh\ncat <<%s\n' "EOFX"; printf '%s\n' "$1"; printf '%s\n' "EOFX"; } > "$TMP/wpa_cli"
    fi
    chmod +x "$TMP/wpa_cli"
}
mk_iw() {  # $1: station dump 본문, $2: info 본문
    {
        printf '#!/bin/sh\n'
        printf 'case "$*" in\n'
        printf '  *"station dump"*) cat <<%s\n' "EOFA"; printf '%s\n' "$1"; printf '%s\n;;\n' "EOFA"
        printf '  *info*) cat <<%s\n' "EOFB"; printf '%s\n' "$2"; printf '%s\n;;\n' "EOFB"
        printf 'esac\n'
    } > "$TMP/iw"
    chmod +x "$TMP/iw"
}

STATION_DUMP=$'Station 00:80:4c:c7:7d:dd (on mlan0)\n\trx bytes:\t532086\n\tsignal:  \t-46 dBm\n\tsignal avg:\t-99 dBm\n\ttx bitrate:\t65.0 MBit/s MCS 7'
WPA_OK=$'bssid=00:80:4c:c7:7d:dd\nfreq=5240\nssid=jhw_wlan_\nwpa_state=COMPLETED'
# 실제 supplicant 는 결합 미완료 상태에서도 bssid= 줄을 낸다(구현에 따라 all-zero).
# 이 픽스처가 핵심 — bssid 만 보고 판단하면 미연결인데 BSSID 가 있는 것처럼 보인다.
WPA_SCAN=$'bssid=00:00:00:00:00:00\nfreq=0\nssid=\nwpa_state=SCANNING'
WPA_ASSOC=$'bssid=00:80:4c:c7:7d:dd\nfreq=5240\nssid=jhw_wlan_\nwpa_state=ASSOCIATING'
IW_INFO=$'Interface mlan0\n\tssid jhw_wlan_\n\tchannel 48 (5240 MHz), width: 20 MHz, center1: 5240 MHz'

export PATH="$TMP:$PATH"
# shellcheck source=/dev/null
. "$LIB"

echo "── 1. wpa_cli 정상 (주 경로) ──"
mk_wpa_cli "$WPA_OK"; mk_iw "$STATION_DUMP" "$IW_INFO"
eq "wlan_bssid"        "$(wlan_bssid mlan0)"       "00:80:4c:c7:7d:dd"
eq "wlan_freq_mhz"     "$(wlan_freq_mhz mlan0)"    "5240"
eq "wlan_signal_dbm"   "$(wlan_signal_dbm mlan0)"  "-46"
wlan_is_connected mlan0 && ok "wlan_is_connected(COMPLETED)" || ng "wlan_is_connected(COMPLETED)" "1" "0"

echo "── 2. wpa_cli 사망 → station dump 폴백 ──"
mk_wpa_cli ""
eq "wlan_bssid(폴백)"  "$(wlan_bssid mlan0)"       "00:80:4c:c7:7d:dd"
eq "wlan_freq_mhz(폴백)" "$(wlan_freq_mhz mlan0)"  "5240"
wlan_is_connected mlan0 && ok "wlan_is_connected(폴백: station 존재)" || ng "wlan_is_connected(폴백)" "1" "0"

echo "── 3. 미연결 ──"
mk_wpa_cli "$WPA_SCAN"; mk_iw "" ""
eq "wlan_bssid(미연결)" "$(wlan_bssid mlan0)"      ""
wlan_is_connected mlan0 && ng "wlan_is_connected(미연결)" "0" "1" || ok "wlan_is_connected(미연결)"

echo "── 4. [핵심] signal avg 오매칭 방지 ──"
mk_wpa_cli "$WPA_OK"; mk_iw "$STATION_DUMP" "$IW_INFO"
# avg 는 -99 로 심어 뒀다. -99 가 나오면 파서가 'signal avg:' 를 잡은 것.
eq "signal(-46, avg -99 아님)" "$(wlan_signal_dbm mlan0)" "-46"

echo "── 5. iw info 폴백 시 width(20 MHz) 오매칭 방지 ──"
mk_wpa_cli ""; mk_iw "$STATION_DUMP" "$IW_INFO"
eq "freq(5240, width 20 아님)" "$(wlan_freq_mhz mlan0)" "5240"

echo "── 6. [핵심] 결합 미완료 BSSID 거부 (ASSOCIATING) ──"
# supplicant 가 결합/4-way 진행 중에 낸 bssid 를 수락하면 미연결인데 연결처럼 보여
# catch-up 허위 실행·fallback GW 오설정이 발생한다(roam_notify.py:228 과 같은 규칙).
mk_wpa_cli "$WPA_ASSOC"; mk_iw "" ""
eq "wlan_bssid(ASSOCIATING → 거부)" "$(wlan_bssid mlan0)" ""
wlan_is_connected mlan0 && ng "wlan_is_connected(ASSOCIATING)" "0" "1" || ok "wlan_is_connected(ASSOCIATING)"

echo "── 7. all-zero BSSID 거부 (SCANNING) ──"
mk_wpa_cli "$WPA_SCAN"; mk_iw "" ""
eq "wlan_bssid(SCANNING+all-zero)" "$(wlan_bssid mlan0)" ""

echo "── 8. 미완료여도 station dump 에 peer 가 있으면 그것을 쓴다 ──"
# moal 이 cfg80211/supplicant 와 어긋난 바로 그 상황 — 실제 결합은 살아 있으므로 채택한다.
mk_wpa_cli "$WPA_ASSOC"; mk_iw "$STATION_DUMP" "$IW_INFO"
eq "wlan_bssid(폴백 우선)" "$(wlan_bssid mlan0)" "00:80:4c:c7:7d:dd"

echo
echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
