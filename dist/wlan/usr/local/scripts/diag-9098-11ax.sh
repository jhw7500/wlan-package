#!/bin/sh
# 9098 STA 11ax (MU-MIMO / OFDMA) 1-shot 진단 (read-only)
# 사용: ./diag-9098-11ax.sh [iface]   (default: mlan0)
IF="${1:-mlan0}"
MLANUTL="${MLANUTL:-/usr/local/bin/mlanutl}"

hr(){ printf '\n=== %s ===\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

hr "[0] 환경"
uname -a
echo "iface=$IF  mlanutl=$MLANUTL"
[ -x "$MLANUTL" ] || { echo "ERROR: $MLANUTL 없음"; exit 1; }
ip -br link show "$IF" 2>/dev/null

hr "[1] 드라이버/버스/FW"
lsmod | awk '$1~/^(mlan|moal|wlan_sdio|wlan_pcie)$/'
ls /sys/class/net/$IF/device/ 2>/dev/null | head -5
cat /sys/class/net/$IF/device/uevent 2>/dev/null

hr "[2] 현재 연결/실제 PHY rate (NSS/MCS)"
iw dev $IF link
iw dev $IF station dump | awk '/Station|tx bitrate|rx bitrate|signal:|MCS|NSS|connected time/'

hr "[3] HE Capability dump (mlanutl 11axcfg, GET)"
RAW=$($MLANUTL $IF 11axcfg 2>&1 | awk '/^[0-9a-fA-F]{2} / {print}' | tr '\n' ' ' | tr -s ' ' | sed 's/^ //;s/ $//')
N=$(echo "$RAW" | wc -w)
echo "raw($N bytes): $RAW"

# 55B 디코드 — bash 단독 비트연산
DECODE=$(echo "$RAW" | awk '
function hx(s){ return strtonum("0x" s) }
function b(v,i){ return and(rshift(v,i),1) }
function bf(v,lo,hi){ m=0; for(i=lo;i<=hi;i++)m+=lshift(1,i); return rshift(and(v,m),lo) }
{
  n=split($0,a," ");
  if(n<27){print "decode SKIP (output too short, n="n")"; exit}
  band=hx(a[1])
  printf "band     : 0x%02X (2.4G=%d 5G=%d 6G=%d)\n", band, b(band,0), b(band,1), b(band,2)
  printf "TLV id   : 0x%02X%02X  len=%d  ext_id=0x%02X (HE_CAPABILITY=0x23 expected)\n", hx(a[3]),hx(a[2]), hx(a[5])*256+hx(a[4]), hx(a[6])
  printf "MAC Cap  : %s %s %s %s %s %s\n", a[7],a[8],a[9],a[10],a[11],a[12]
  m0=hx(a[7]); m1=hx(a[8]); m2=hx(a[9]); m3=hx(a[10]); m4=hx(a[11]); m5=hx(a[12])
  printf "  mac[0]=0x%02X +HTC=%d TWTReq=%d TWTResp=%d FragSup=%d\n", m0, b(m0,0),b(m0,1),b(m0,2), bf(m0,3,4)
  printf "  mac[1]=0x%02X TrigPadDur=%d MultiTID-Rx=%d\n", m1, bf(m1,2,4), bf(m1,5,7)
  printf "  mac[2]=0x%02X BSR=%d BcastTWT=%d 32bitBA=%d MUCascade=%d\n", m2, b(m2,4),b(m2,5),b(m2,6),b(m2,7)
  printf "  mac[3]=0x%02X **OMCtrl=%d** OFDMA_RA=%d AMPDUExp=%d FlexTWT=%d Rx-MultiBSS=%d\n", m3, b(m3,1),b(m3,2),bf(m3,3,4),b(m3,6),b(m3,7)
  printf "  mac[4]=0x%02X BSRPAgg=%d QTP=%d ABQR=%d SRResp=%d NDPFB=%d OPS=%d\n", m4, b(m4,0),b(m4,1),b(m4,2),b(m4,3),b(m4,4),b(m4,5)
  printf "  mac[5]=0x%02X UL_2x996=%d OM_UL_MU_DataDisRx=%d DynSMPS=%d\n", m5, b(m5,3),b(m5,4),b(m5,5)
  printf "PHY Cap  : %s %s %s %s %s %s %s %s %s %s %s\n", a[13],a[14],a[15],a[16],a[17],a[18],a[19],a[20],a[21],a[22],a[23]
  p0=hx(a[13]); p1=hx(a[14]); p2=hx(a[15]); p3=hx(a[16]); p4=hx(a[17]); p5=hx(a[18])
  p6=hx(a[19]); p7=hx(a[20]); p8=hx(a[21]); p9=hx(a[22]); p10=hx(a[23])
  printf "  phy[0]=0x%02X 40MHz2G=%d 40/80_5G=%d **160=%d** 160_80p80=%d 242RU_2G=%d 242RU_5G=%d\n", p0, b(p0,1),b(p0,2),b(p0,3),b(p0,4),b(p0,5),b(p0,6)
  printf "  phy[1]=0x%02X PuncPreRx=%d DevClass=%d LDPC=%d 1xLTF_0.8GI=%d\n", p1, bf(p1,0,2),b(p1,3),b(p1,4),b(p1,5)
  printf "  phy[2]=0x%02X NDP4xLTF=%d STBC-Tx=%d STBC-Rx=%d Doppler-Tx=%d Doppler-Rx=%d **FullBW_UL_MU_MIMO=%d** PartBW_UL_MU_MIMO=%d\n", p2, b(p2,1),b(p2,2),b(p2,3),b(p2,4),b(p2,5),b(p2,6),b(p2,7)
  printf "  phy[3]=0x%02X DCM-Tx=%d DCM-Rx=%d Rx_HE_MU_PPDU_from_nonAP=%d **SU-BFER=%d**\n", p3, bf(p3,0,2),bf(p3,3,5),b(p3,6),b(p3,7)
  printf "  phy[4]=0x%02X **SU-BFEE=%d** **MU-BFER=%d** BFee-STS<=80=%d BFee-STS>80=%d\n", p4, b(p4,0),b(p4,1),bf(p4,2,4),bf(p4,5,7)
  printf "  phy[5]=0x%02X **#SoundDim<=80=%d (+1=%d streams)** #SoundDim>80=%d NG16-SU=%d NG16-MU=%d\n", p5, bf(p5,0,2), bf(p5,0,2)+1, bf(p5,3,5), b(p5,6),b(p5,7)
  printf "  phy[6]=0x%02X CB42SU=%d CB75MU=%d Trig-SU-BF-FB=%d Trig-MU-BF-FB=%d Trig-CQI-FB=%d PartBW-ER=%d **PartBW-DL-MU-MIMO=%d** PPE=%d\n", p6, b(p6,0),b(p6,1),b(p6,2),b(p6,3),b(p6,4),b(p6,5),b(p6,6),b(p6,7)
  printf "  phy[7]=0x%02X SRP=%d PwrBoost=%d 4xLTF_0.8GI=%d MaxNc=%d STBC-Tx>80=%d STBC-Rx>80=%d\n", p7, b(p7,0),b(p7,1),b(p7,2),bf(p7,3,5),b(p7,6),b(p7,7)
  rx80 = hx(a[25])*256 + hx(a[24])
  tx80 = hx(a[27])*256 + hx(a[26])
  printf "MCS@80  : rx=0x%04X tx=0x%04X\n", rx80, tx80
  printf "  rx NSS1=%d NSS2=%d NSS3=%d NSS4=%d\n", bf(rx80,0,1),bf(rx80,2,3),bf(rx80,4,5),bf(rx80,6,7)
  printf "  tx NSS1=%d NSS2=%d NSS3=%d NSS4=%d  (0=MCS0-7, 1=0-9, 2=0-11, 3=N/A)\n", bf(tx80,0,1),bf(tx80,2,3),bf(tx80,4,5),bf(tx80,6,7)
}')
echo "$DECODE"

hr "[4] Quick verdict for 9098 STA"
echo "$DECODE" | awk '
function pick(line,key,   m,p){ p=key"=[0-9]+"; if(match(line,p)){ m=substr(line,RSTART,RLENGTH); sub(key"=","",m); return int(m) } return -1 }
/mac\[0\]/   { v=pick($0,"\\+HTC");                printf "  [+HTC HE]              =%d  (Trigger 응답 기본: %s)\n", v, (v==1?"OK":"FAIL") }
/mac\[3\]/   { v=pick($0,"OMCtrl");                printf "  [OM Control]           =%d  (UL-MU 동적 토글: %s)\n", v, (v==1?"OK":"FAIL") }
/phy\[0\]/   { v=pick($0,"40/80_5G"); w=pick($0,"160"); printf "  [BW 5G]                40/80=%d 160=%d  (%s)\n", v, w, (v==1 && w==0?"80MHz 정상, 160 OFF":"확인") }
/phy\[2\]/   { v=pick($0,"FullBW_UL_MU_MIMO"); w=pick($0,"PartBW_UL_MU_MIMO"); printf "  [UL-MU-MIMO advert.]   FullBW=%d PartBW=%d  (%s)\n", v, w, (v==1?"OK":"FAIL") }
/phy\[3\]/   { v=pick($0,"SU-BFER");               printf "  [SU-BFER (self)]       =%d  (STA가 BFER 광고: %s)\n", v, (v==1?"YES":"NO") }
/phy\[4\]/   { v=pick($0,"SU-BFEE"); m=pick($0,"MU-BFER"); s=pick($0,"BFee-STS<=80"); printf "  [SU-BFEE / MU-BFER]    SU=%d MU=%d  STS<=80=%d  (DL-MU 수신: %s)\n", v, m, s, (v==1?"OK":"FAIL") }
/phy\[5\]/   { v=pick($0,"#SoundDim<=80");         printf "  [Sounding Dim <=80]    =%d → %d streams  (%s)\n", v, v+1, (v+1==2?"2×2 정확":"※") }
/phy\[6\]/   { p=pick($0,"PartBW-DL-MU-MIMO"); pp=pick($0,"PPE"); printf "  [DL-MU-MIMO PartBW]    =%d  PPE=%d  (PartBW DL-MU: %s)\n", p, pp, (p==1?"OK":"미advertise") }
/^  rx NSS/  { gsub(/.*rx NSS/,"rx NSS"); printf "  [RX MCS@80] %s\n", $0 }
/^  tx NSS/  { gsub(/.*tx NSS/,"tx NSS"); printf "  [TX MCS@80] %s\n", $0 }
'

hr "[5] LLDE 통계 (UL/DL-OFDMA Trigger 카운터)"
LLDE_OUT=$($MLANUTL $IF 11axcmd llde 2>&1)
if [ -z "$LLDE_OUT" ]; then
  echo "  (silent — STA 모드에서 LLDE는 AP-only 기능. 9098 STA에선 의도된 N/A)"
else
  echo "$LLDE_OUT"
fi

hr "[6] HT/HE stream config"
$MLANUTL $IF htstreamcfg 2>&1
echo "  (tx_bf_cfg는 peer MAC + sub-action 인자 필수 — 단순 GET 미지원, skip)"

hr "[7] AP HE Cap (현재 연결된 BSS)"
BSSID=$(iw dev $IF link | awk '/Connected to/ {print $3}')
echo "BSSID=$BSSID"
if [ -n "$BSSID" ]; then
  iw dev $IF scan dump 2>/dev/null | awk -v B="$BSSID" '
    /^BSS / { p=($0 ~ B) }
    p && /HE capabilities|HE Operation|HE PHY Capabilities|HE MAC Capabilities|MU EDCA|BSS Color|Spatial Reuse|Channel width|HE Tx\/Rx|TWT/'
fi

hr "[8] 11ax 부가 설정 상태 (사용자 토글 가능 항목)"
for sub in beam_change set_bsrp enable_htc tx_omi txop_rts obss_pd_offset enable_sr; do
  printf "%-18s: " "$sub"
  $MLANUTL $IF 11axcmd $sub 2>&1 | tail -1
done

hr "[9] /proc/mwlan 통계 (HE/MU/OFDMA 카운터)"
ADAP="/proc/mwlan/adapter0/$IF"
if [ -d "$ADAP" ]; then
  echo "--- info (요약) ---"
  grep -E "bss_mode|media_state|channel|nf|rssi|data_rate|max_rx_data_rate|tx_data_rate|mcs|bss_chan_info|capability|11n_enabled|11ac_enabled|11ax_enabled|num_tx_bytes|num_rx_bytes|num_tx_pkts|num_rx_pkts|tx_pause" "$ADAP/info" 2>/dev/null | head -40
  echo "--- debug (HE/MU/Trigger 카운터만) ---"
  grep -iE "he_|mu_|trig|tbppdu|tb_|ofdma|amsdu|ampdu|mpdu" "$ADAP/debug" 2>/dev/null | head -40
  echo "--- histogram (HE-NSS/MCS 실측 분포, 안테나별) ---"
  for ant in "$ADAP/histogram"/wlan-ant*; do
    [ -f "$ant" ] || continue
    echo ">> $(basename $ant)"
    grep -E "rx_rate|tx_rate|HE|MCS|nss|sig|noise" "$ant" 2>/dev/null | head -25
  done
  echo "--- log (mlanutl getlog equivalent) ---"
  grep -iE "mu|trig|tb_|he_|amsdu|ampdu" "$ADAP/log" 2>/dev/null | head -20
else
  echo "  (/proc/mwlan/adapter0/$IF 없음 — 드라이버 procfs 미활성)"
fi

hr "[10] Bridge 영향 (현재 브랜치 feature/driver-bridge)"
bridge link show 2>/dev/null
ip -br link show type bridge 2>/dev/null
echo "(mlan0 이 bridge slave 면 station 통계는 mlan0 그대로 유효)"

hr "[11] VERDICT (최종 결론)"
# 핵심 비트/값 추출
hb(){ echo "$RAW" | awk -v i="$1" '{print $(i+1)}'; }
bit(){ awk -v h="$1" -v p="$2" 'BEGIN{print and(rshift(strtonum("0x"h),p),1)}'; }
fld(){ awk -v h="$1" -v l="$2" -v u="$3" 'BEGIN{m=0; for(i=l;i<=u;i++)m+=lshift(1,i); print rshift(and(strtonum("0x"h),m),l)}'; }
M0=$(hb 6); M3=$(hb 9); P0=$(hb 12); P2=$(hb 14); P3=$(hb 15); P4=$(hb 16); P5=$(hb 17); P6=$(hb 18)
RX_NSS2=$(fld "$(hb 23)" 2 3); TX_NSS2=$(fld "$(hb 25)" 2 3)
# 4기능 판정
HTC=$(bit "$M0" 0); OM=$(bit "$M3" 1)
SUBFEE=$(bit "$P4" 0); SDIM=$(fld "$P5" 0 2)
FULL_ULMU=$(bit "$P2" 6); RX_HEMU=$(bit "$P3" 6)
DL_MU=$([ "$SUBFEE" = 1 ] && [ $((SDIM+1)) -ge 2 ] && echo OK || echo FAIL)
UL_MU=$([ "$HTC" = 1 ] && [ "$FULL_ULMU" = 1 ] && echo OK || echo FAIL)
DL_OF=$([ "$HTC" = 1 ] && [ "$RX_HEMU" = 1 ] && echo OK || echo FAIL)
UL_OF=$([ "$HTC" = 1 ] && [ "$OM" = 1 ] && echo OK || echo FAIL)
# 환경
BW=$(iw dev $IF info 2>/dev/null | sed -n 's/.*width: \([0-9]*\).*/\1/p')
SIG=$(iw dev $IF link 2>/dev/null | awk '/signal:/{print $2}')
CONN=$(iw dev $IF link 2>/dev/null | awk '/Connected to/{print $3}')
PHYRATE=$(iw dev $IF station dump 2>/dev/null | awk '/tx bitrate:/{for(i=1;i<=NF;i++) if($i~/MBit/) {print $(i-1); break}; exit}')
HTCFG=$($MLANUTL $IF htstreamcfg 2>&1 | grep -oE '[0-9]x[0-9]')
AP_BW=$(iw dev $IF scan dump 2>/dev/null | awk '
  /-- associated/ { p=1; next }
  p && /^BSS / { exit }
  p && /channel width: [0-9]+ \([0-9]+ MHz\)/ {
    if (match($0, /\([0-9]+ MHz\)/)) print substr($0, RSTART+1, RLENGTH-2); exit
  }
  p && /STA channel width: [0-9]+ MHz/ { match($0, /[0-9]+ MHz/); print substr($0, RSTART, RLENGTH); exit }
')
[ -z "$AP_BW" ] && AP_BW="(scan dump 미응답)"

cat <<EOF
─── 9098 STA SDIO ($IF) ─────────────────────────────────────
연결       : $CONN ($SIG dBm), 현재 BW $BW MHz, PHY rate ${PHYRATE} MBit/s
HT stream  : $HTCFG
AP 광고 BW : $AP_BW

광고 능력 (HE Cap byte 디코드):
  [1] DL-MU-MIMO 수신     : $DL_MU  (SU-BFEE=$SUBFEE, SoundDim=$((SDIM+1)) streams, RX MCS NSS2=$RX_NSS2)
  [2] UL-MU-MIMO 송신     : $UL_MU  (+HTC=$HTC, FullBW-UL-MU=$FULL_ULMU, TX MCS NSS2=$TX_NSS2)
  [3] DL-OFDMA 수신       : $DL_OF  (+HTC=$HTC, Rx-HE-MU-PPDU=$RX_HEMU)
  [4] UL-OFDMA 응답       : $UL_OF  (+HTC=$HTC, OM-Ctrl=$OM)

실측 동작 (procfs histogram):
  HE-NSS2 PPDU 수신       : $(grep -c "AX BW:.*NSS:2" $ADAP/histogram/wlan-ant0 2>/dev/null) MCS bucket 도달
  AMSDU TX                : $(grep "dot11TransmittedAMSDUCount" $ADAP/log 2>/dev/null | awk -F= '{print $2}' | tr -d ' ') frames
  AMPDU TX                : $(grep "dot11TransmittedAMPDUCount" $ADAP/log 2>/dev/null | awk -F= '{print $2}' | tr -d ' ') frames

── 종합 판정 ──
EOF

# 종합 판정 로직
if [ "$DL_MU" = OK ] && [ "$UL_MU" = OK ] && [ "$DL_OF" = OK ] && [ "$UL_OF" = OK ]; then
  echo "  ✅ STA(9098) 측 4기능 광고/협상 모두 정상. 2×2 NSS 동작 확인."
  case "$BW" in
    20) echo "  ⚠  실측 BW=20MHz — AP가 80MHz로 광고하지 않음 (AP hostapd 측 이슈, STA 책임 아님).";;
    40) echo "  ⚠  실측 BW=40MHz — AP가 80MHz로 광고하지 않음.";;
    80) echo "  ✅ 실측 BW=80MHz — 광고대로 동작.";;
    160) echo "  ✅ 실측 BW=160MHz — 광고 이상 (9098은 보통 80MHz max).";;
    *)  echo "  ?  실측 BW=$BW MHz — 확인 필요.";;
  esac
  echo "  ℹ  9098 SDIO 칩 의도된 제약: BSR=$(bit "$(hb 8)" 4), Trig-BF-FB=$(bit "$P6" 2)$(bit "$P6" 3)$(bit "$P6" 4) (모두 0이면 정상)"
  echo "  ▶  결론: STA 정상. throughput/MU 효율 cap의 원인은 환경(AP 정책·STA 수)."
else
  echo "  ❌ 광고 결핍 발견:"
  [ "$DL_MU" = FAIL ] && echo "       - DL-MU-MIMO 수신 광고 부족"
  [ "$UL_MU" = FAIL ] && echo "       - UL-MU-MIMO 송신 광고 부족"
  [ "$DL_OF" = FAIL ] && echo "       - DL-OFDMA 광고 부족"
  [ "$UL_OF" = FAIL ] && echo "       - UL-OFDMA 광고 부족"
  echo "  ▶  결론: HE Cap conf 또는 FW build 점검 필요. 11axcfg.conf push 시도 권장."
fi

hr "[12] AP HE Cap (mgmt_dump의 AssocResp에서 추출, byte-level)"
AP_HECAP_LINE=$(grep "IE\[255\] ext=0x23" $(dirname "$ADAP")/mgmt_dump 2>/dev/null | tail -1)
if [ -z "$AP_HECAP_LINE" ]; then
  echo "  mgmt_dump에 AP HE Cap 없음:"
  echo "    - mgmt_hex_dump=1 활성 + net_rx>=2 인지 확인 (wifi_init_conf.json)"
  echo "    - reassociate 트리거 후 다시 시도: wpa_cli -i $IF reassociate"
else
  AP_HEX=$(echo "$AP_HECAP_LINE" | sed 's/.*: //' | sed 's/ $//')
  AP_BYTES=$(echo "$AP_HEX" | wc -w)
  echo "  source: $(echo $AP_HECAP_LINE | awk '{print $1, $2, $3}')"
  echo "  raw($AP_BYTES bytes): $AP_HEX"
  if [ "$AP_BYTES" -lt 21 ]; then
    echo "  ⚠ byte 수 부족 (MAC 6 + PHY 11 + MCS 4 = 21 min). skip."
  else
    AP_M0=$(echo "$AP_HEX" | awk '{print $1}')
    AP_M3=$(echo "$AP_HEX" | awk '{print $4}')
    AP_P0=$(echo "$AP_HEX" | awk '{print $7}')
    AP_P2=$(echo "$AP_HEX" | awk '{print $9}')
    AP_P3=$(echo "$AP_HEX" | awk '{print $10}')
    AP_P4=$(echo "$AP_HEX" | awk '{print $11}')
    AP_P5=$(echo "$AP_HEX" | awk '{print $12}')
    AP_RX80_L=$(echo "$AP_HEX" | awk '{print $18}')
    AP_TX80_L=$(echo "$AP_HEX" | awk '{print $20}')
    AP_HTC=$(bit "$AP_M0" 0)
    AP_OM=$(bit "$AP_M3" 1)
    AP_BW80=$(bit "$AP_P0" 2); AP_BW160=$(bit "$AP_P0" 3)
    AP_FULL_ULMU=$(bit "$AP_P2" 6)
    AP_SU_BFER=$(bit "$AP_P3" 7); AP_RX_HEMU=$(bit "$AP_P3" 6)
    AP_SU_BFEE=$(bit "$AP_P4" 0); AP_MU_BFER=$(bit "$AP_P4" 1)
    AP_SDIM=$(fld "$AP_P5" 0 2)
    AP_RX_NSS2=$(fld "$AP_RX80_L" 2 3)
    AP_TX_NSS2=$(fld "$AP_TX80_L" 2 3)
    cat <<EOF
  ── AP 광고 능력 ──
    mac[0]=0x$AP_M0  +HTC=$AP_HTC
    mac[3]=0x$AP_M3  OM-Ctrl=$AP_OM
    phy[0]=0x$AP_P0  40/80_5G=$AP_BW80  160=$AP_BW160
    phy[2]=0x$AP_P2  FullBW-UL-MU=$AP_FULL_ULMU
    phy[3]=0x$AP_P3  Rx-HE-MU-PPDU=$AP_RX_HEMU  SU-BFER=$AP_SU_BFER
    phy[4]=0x$AP_P4  SU-BFEE=$AP_SU_BFEE  **MU-BFER=$AP_MU_BFER**
    phy[5]=0x$AP_P5  SoundDim<=80=$AP_SDIM ($((AP_SDIM+1)) streams)
    rx_mcs_80 NSS2=$AP_RX_NSS2  tx_mcs_80 NSS2=$AP_TX_NSS2

  ── AP 측 4기능 광고 평가 (AP→STA 방향) ──
EOF
    AP_DL_MU=$([ "$AP_MU_BFER" = 1 ] && echo OK || echo "FAIL (AP MU-BFER=0)")
    AP_UL_MU=$([ "$AP_FULL_ULMU" = 1 ] && echo OK || echo "FAIL (AP FullBW UL-MU=0)")
    AP_DL_OF=$([ "$AP_RX_HEMU" = 1 ] && echo OK || echo "FAIL (AP Rx-HE-MU-PPDU=0)")
    AP_UL_OF=$([ "$AP_HTC" = 1 ] && echo OK || echo "FAIL (AP +HTC=0)")
    printf "    [DL-MU-MIMO 송신] %s\n" "$AP_DL_MU"
    printf "    [UL-MU-MIMO 수신] %s\n" "$AP_UL_MU"
    printf "    [DL-OFDMA 송신  ] %s\n" "$AP_DL_OF"
    printf "    [UL-OFDMA 수신  ] %s\n" "$AP_UL_OF"
    echo
    echo "  ── STA-AP cross-check ──"
    printf "    DL-MU-MIMO : STA-BFEE=%d + AP-MU-BFER=%d → %s\n" "$SUBFEE" "$AP_MU_BFER" "$([ "$SUBFEE" = 1 ] && [ "$AP_MU_BFER" = 1 ] && echo "양쪽 OK" || echo "한쪽 결핍 → MU 그룹 형성 불가")"
    printf "    UL-MU-MIMO : STA-FullBW=%d + AP-FullBW=%d → %s\n" "$FULL_ULMU" "$AP_FULL_ULMU" "$([ "$FULL_ULMU" = 1 ] && [ "$AP_FULL_ULMU" = 1 ] && echo "양쪽 OK" || echo "한쪽 결핍")"
  fi
fi

hr "[DONE]"
