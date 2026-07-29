#!/bin/sh
# 9098 STA HE 4기능 (DL/UL MU-MIMO, DL/UL OFDMA) 5단계 검증 (read-only + 부하 측정)
# Usage: ./verify-he-mu-features.sh [iface] [iperf_server_ip]
#   iface           : mlan0 (default)
#   iperf_server_ip : iperf3 서버 IP (선택 — Step 4 부하 측정에 사용)
IF="${1:-mlan0}"
# 링크 지표 조회 헬퍼(iw link 미사용 — 해당 파일 상단 주석 참조).
# 진단 도구라 결과가 조용히 비는 것보다 즉시 실패가 낫다.
# shellcheck source=./wlan_link_lib.sh
. /usr/local/scripts/wlan_link_lib.sh 2>/dev/null \
    || { echo "ERROR: /usr/local/scripts/wlan_link_lib.sh 로드 실패" >&2; exit 1; }
SRV="${2:-}"
MLANUTL="${MLANUTL:-/usr/local/bin/mlanutl}"
ADAP="/proc/mwlan/adapter0/$IF"

hr(){ printf '\n========== %s ==========\n' "$*"; }
sub(){ printf '\n-- %s --\n' "$*"; }

# ────────────────────────────────────────────────────────────
# HE Cap 55B raw → byte parse 헬퍼
# ────────────────────────────────────────────────────────────
get_he_cap(){
  $MLANUTL $IF 11axcfg 2>&1 | awk '/^[0-9a-fA-F]{2} / {print}' | tr '\n' ' ' | tr -s ' '
}
# 인자: byte_index(0-based), 결과는 hex 값을 stdout으로
hb(){ echo "$1" | awk -v i="$2" '{print $(i+1)}'; }
# bit 추출: byte_hex bit_pos → 0/1
bit(){ awk -v h="$1" -v p="$2" 'BEGIN{print and(rshift(strtonum("0x"h),p),1)}'; }
# field 추출: byte_hex lo hi → integer
fld(){ awk -v h="$1" -v l="$2" -v u="$3" 'BEGIN{m=0; for(i=l;i<=u;i++)m+=lshift(1,i); print rshift(and(strtonum("0x"h),m),l)}'; }

hr "[STEP 1] 4기능별 광고 비트 확인 (advertised cap)"
RAW=$(get_he_cap)
N=$(echo "$RAW" | wc -w)
echo "raw($N bytes): $(echo $RAW | head -c 120)..."
[ "$N" != "55" ] && { echo "ERROR: HE Cap GET 응답 비정상"; exit 1; }

BAND=$(hb "$RAW" 0)
M0=$(hb "$RAW" 6); M1=$(hb "$RAW" 7); M3=$(hb "$RAW" 9)
P0=$(hb "$RAW" 12); P2=$(hb "$RAW" 14); P3=$(hb "$RAW" 15); P4=$(hb "$RAW" 16); P5=$(hb "$RAW" 17); P6=$(hb "$RAW" 18)
RX80_L=$(hb "$RAW" 23); RX80_H=$(hb "$RAW" 24); TX80_L=$(hb "$RAW" 25); TX80_H=$(hb "$RAW" 26)
RX80=$(awk -v l="$RX80_L" -v h="$RX80_H" 'BEGIN{printf "%X", strtonum("0x"h)*256+strtonum("0x"l)}')
TX80=$(awk -v l="$TX80_L" -v h="$TX80_H" 'BEGIN{printf "%X", strtonum("0x"h)*256+strtonum("0x"l)}')

# 4기능 광고 비트 평가
sub "DL-MU-MIMO (2×2)"
SU_BFEE=$(bit "$P4" 0); MU_BFER=$(bit "$P4" 1); STS_LE80=$(fld "$P4" 2 4)
SOUND_DIM=$(fld "$P5" 0 2); PARTBW_DLMU=$(bit "$P6" 6)
RX_NSS2=$(fld "$RX80_L" 2 3)
printf "  SU-BFEE         = %d   (필수: 1)\n" $SU_BFEE
printf "  MU-BFER         = %d   (STA: 0이 정상)\n" $MU_BFER
printf "  BFee-STS<=80    = %d   (= %d STS)\n" $STS_LE80 $((STS_LE80+1))
printf "  SoundDim<=80    = %d   (= %d streams)\n" $SOUND_DIM $((SOUND_DIM+1))
printf "  PartBW-DL-MU    = %d   (선택: 0이어도 FullBW만 가능)\n" $PARTBW_DLMU
printf "  rx_mcs_80 NSS2  = %d   (2=MCS0-11)\n" $RX_NSS2
if [ $SU_BFEE -eq 1 ] && [ $((SOUND_DIM+1)) -ge 2 ] && [ $RX_NSS2 -le 2 ]; then
  echo "  ▶ 광고: 정상 (DL-MU-MIMO 수신 가능)"
else
  echo "  ▶ 광고: 결핍 — 위 조건 미충족"
fi

sub "UL-MU-MIMO (2×2)"
FULL_ULMU=$(bit "$P2" 6); PART_ULMU=$(bit "$P2" 7); HTC=$(bit "$M0" 0)
TX_NSS2=$(fld "$TX80_L" 2 3)
printf "  +HTC HE         = %d   (필수: 1)\n" $HTC
printf "  FullBW UL-MU    = %d   (필수: 1)\n" $FULL_ULMU
printf "  PartBW UL-MU    = %d   (선택)\n" $PART_ULMU
printf "  tx_mcs_80 NSS2  = %d   (2=MCS0-11)\n" $TX_NSS2
if [ $HTC -eq 1 ] && [ $FULL_ULMU -eq 1 ] && [ $TX_NSS2 -le 2 ]; then
  echo "  ▶ 광고: 정상 (UL-MU-MIMO 송신 가능)"
else
  echo "  ▶ 광고: 결핍"
fi

sub "DL-OFDMA"
RX_HE_MU=$(bit "$P3" 6); BW80=$(bit "$P0" 2)
printf "  +HTC HE         = %d   (필수: 1)\n" $HTC
printf "  Rx HE-MU-PPDU   = %d   (필수: 1)\n" $RX_HE_MU
printf "  40/80MHz 5G     = %d   (RU 분할 위한 BW)\n" $BW80
printf "  rx_mcs_80 NSS2  = %d\n" $RX_NSS2
if [ $HTC -eq 1 ] && [ $RX_HE_MU -eq 1 ]; then
  echo "  ▶ 광고: 정상 (DL-OFDMA RU 수신 가능)"
else
  echo "  ▶ 광고: 결핍"
fi

sub "UL-OFDMA"
OM_CTRL=$(bit "$M3" 1); TRIG_PAD=$(fld "$M1" 2 4)
printf "  +HTC HE         = %d   (필수: 1)\n" $HTC
printf "  OM Control      = %d   (UL MU 토글: 1)\n" $OM_CTRL
printf "  Trig Pad Dur    = %d   (0~3, 단위 8μs)\n" $TRIG_PAD
if [ $HTC -eq 1 ] && [ $OM_CTRL -eq 1 ]; then
  echo "  ▶ 광고: 정상 (UL-OFDMA Trigger 응답 가능)"
else
  echo "  ▶ 광고: 결핍"
fi

# ────────────────────────────────────────────────────────────
hr "[STEP 2] 설정 변경 가능 항목 + 현재 값"
sub "동적 토글 (즉시 적용, 재접속 불필요)"
for cmd in enable_htc tx_omi txop_rts beam_change set_bsrp obss_pd_offset enable_sr; do
  printf "  %-18s : " "$cmd"
  $MLANUTL $IF 11axcmd $cmd 2>&1 | tail -1
done
$MLANUTL $IF htstreamcfg 2>&1 | sed 's/^/  htstreamcfg        : /'

sub "정적 설정 (재접속/driver reload 필요)"
echo "  /etc/11axcfg.conf 또는 working conf 위치:"
ls -la /etc/11axcfg.conf /opt/wlan/config/11axcfg.conf 2>/dev/null | sed 's/^/    /'
echo "  적용: mlanutl $IF 11axcfg /path/to/11axcfg.conf"
echo "  (PHY Cap의 일부 비트는 FW가 hw 한계로 강제 클리어 — 예: STBC, 160MHz)"

# ────────────────────────────────────────────────────────────
hr "[STEP 3] 광고 비트 재확인 (Step 1 결과를 한 줄로 요약)"
printf "  DL-MU-MIMO 수신: %s\n" "$([ $SU_BFEE -eq 1 ] && echo OK || echo FAIL)"
printf "  UL-MU-MIMO 송신: %s\n" "$([ $FULL_ULMU -eq 1 ] && [ $HTC -eq 1 ] && echo OK || echo FAIL)"
printf "  DL-OFDMA 수신   : %s\n" "$([ $RX_HE_MU -eq 1 ] && [ $HTC -eq 1 ] && echo OK || echo FAIL)"
printf "  UL-OFDMA 응답   : %s\n" "$([ $OM_CTRL -eq 1 ] && [ $HTC -eq 1 ] && echo OK || echo FAIL)"

# ────────────────────────────────────────────────────────────
hr "[STEP 4] 실측 동작 검증"
sub "현재 PHY rate (assoc 후 협상값)"
iw dev $IF info | grep -E "channel|width|center"
iw dev $IF station dump 2>/dev/null | awk '/tx bitrate|rx bitrate|signal:/'

sub "AP BW 광고 (IE 디코드)"
iw dev $IF scan dump 2>/dev/null | awk '
  /^BSS / { p=($0 ~ /associated/) }
  p && /HT operation:|VHT operation:|HE Operation:|channel width:|center freq seg|primary channel:|STA channel/ { print "  "$0 }
'

if [ -n "$SRV" ]; then
  sub "부하 측정 시작 (iperf3 → $SRV, 10s) — histogram delta 측정"
  BEFORE=$(cat $ADAP/histogram/wlan-ant0 2>/dev/null | grep -oE 'rx_rate\[[0-9]+\] = [0-9]+' | sort)
  iperf3 -c $SRV -t 10 -P 4 -O 1 2>&1 | tail -10
  AFTER=$(cat $ADAP/histogram/wlan-ant0 2>/dev/null | grep -oE 'rx_rate\[[0-9]+\] = [0-9]+' | sort)
  sub "histogram delta (rate별 PPDU 증가량)"
  diff <(echo "$BEFORE") <(echo "$AFTER") | grep '^>' | head -10
else
  sub "histogram 누적 (rate별 카운터, iperf 서버 없으면 기존 트래픽만)"
  cat $ADAP/histogram/wlan-ant0 2>/dev/null | grep -E "^(rx|tx)_rate" | grep "AX" | head -20
fi

sub "log: AMSDU/AMPDU 통계 (간접 지표)"
grep -E "AMSDU|AMPDU|RetryCount" $ADAP/log 2>/dev/null | head -10

# ────────────────────────────────────────────────────────────
hr "[STEP 5] 원인 분석 (광고 OK이지만 동작 미확인 시)"
cat <<'EOF'
※ 9098 SDIO mlan은 MU/Trigger PPDU 카운터를 별도 노출하지 않음.
  STA-only 진단으로는 "MU 동작 여부" 직접 증명 불가 → 간접 지표 + 외부 sniffer 필요.

[원인 분석 매트릭스]
  현상                                          | 가능 원인                          | 확인 방법
  --------------------------------------------+-------------------------------------+--------------------
  광고는 OK인데 PHY rate가 BW20 cap            | AP가 80MHz로 광고 안 함              | iw scan dump > VHT op channel width
                                              |                                     | AP hostapd vht_oper_chwidth 확인
  광고 OK, 단일 STA만 접속                     | DL/UL-MU는 ≥2 STA 그룹 필요         | 2번째 STA 추가 후 throughput 변화
  광고 OK, AP가 SU-BFER만 지원                 | AP MU-BFER OFF                      | iw scan > AP HE PHY Cap byte4 bit1
  광고 OK, Trigger Frame 안 옴                 | AP UL-OFDMA 비활성                  | hostapd: he_ul_mu_data_disable, LLDE off
  광고 OK, Sounding 실패                       | STA의 Trig-BF-FB=0 (9098 칩 한계)  | phy[6] bit2/3/4 = 0 — 정상 (제약)
  광고 OK, BSR=0 (mac[2].4)                   | UL 스케줄 효율 저하                 | 칩 한계 — AP가 BSRP Trigger 송신 시도
  rate가 NSS=1로 cap                          | RSSI 약함, BFEE 협상 실패           | signal: -65dBm 이상 권장
  HE 자체 미협상 (HT/VHT만)                    | AP가 ieee80211ax=0                  | iw scan > HE Cap 존재 여부
EOF
echo "  현재 보드: signal=$(wlan_signal_dbm "$IF") dBm, BW=$(iw dev $IF info | awk '/width:/{print $5,$6}')"

hr "[FINAL VERDICT]"
BW=$(iw dev $IF info 2>/dev/null | sed -n 's/.*width: \([0-9]*\).*/\1/p')
SIG=$(wlan_signal_dbm "$IF")
RATE=$(iw dev $IF station dump 2>/dev/null | awk '/tx bitrate:/{print $3,$4; exit}')
P4_BIT1=$(bit "$P4" 1)
DL_MU_V=$([ $SU_BFEE -eq 1 ] && [ $((SOUND_DIM+1)) -ge 2 ] && [ $RX_NSS2 -le 2 ] && echo OK || echo FAIL)
UL_MU_V=$([ $HTC -eq 1 ] && [ $FULL_ULMU -eq 1 ] && [ $TX_NSS2 -le 2 ] && echo OK || echo FAIL)
DL_OF_V=$([ $HTC -eq 1 ] && [ $RX_HE_MU -eq 1 ] && echo OK || echo FAIL)
UL_OF_V=$([ $HTC -eq 1 ] && [ $OM_CTRL -eq 1 ] && echo OK || echo FAIL)
cat <<EOF
연결: $(wlan_bssid "$IF") ($SIG dBm), BW $BW MHz, $RATE
광고: DL-MU-MIMO=$DL_MU_V  UL-MU-MIMO=$UL_MU_V  DL-OFDMA=$DL_OF_V  UL-OFDMA=$UL_OF_V

── 종합 ──
EOF
if [ "$DL_MU_V" = OK ] && [ "$UL_MU_V" = OK ] && [ "$DL_OF_V" = OK ] && [ "$UL_OF_V" = OK ]; then
  echo "  ✅ STA 4기능 광고/협상 정상, 2×2 동작 (NSS=$([ $RX_NSS2 -le 2 ] && echo 2 || echo "?"))"
  [ "$BW" = "20" ] && echo "  ⚠  BW=20MHz cap — AP 측 hostapd 광고 이슈" \
                  || echo "  ✅ BW=$BW MHz — AP/STA 협상 정상"
  echo "  ℹ  MU PPDU 실제 발현 여부는 외부 sniffer/AP 통계 없이 STA-only 직접 증명 불가"
  echo "  ▶  STA(9098 SDIO) 검증 PASS."
else
  echo "  ❌ 광고 결핍 발견 — Step 1 결과 재확인 후 conf 또는 FW build 점검"
fi

hr "[DONE]"
