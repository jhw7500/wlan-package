#!/bin/bash
# 듀얼-STA MU/OFDMA 간접 검증 (9098 STA × 2 + iperf3 서버)
# 같은 AP, 같은 채널에 두 STA가 가입한 상태에서:
#   1) 각 STA 단독 throughput  → SU baseline
#   2) 두 STA 동시 throughput  → MU/OFDMA 효과 추정
# Usage: ./dual-sta-mu-test.sh STA1_IP STA2_IP IPERF_SERVER_IP [duration]
set -u
STA1="${1:?STA1_IP required (예: 192.168.0.100)}"
STA2="${2:?STA2_IP required (예: 192.168.0.101)}"
SRV="${3:?IPERF_SERVER_IP required}"
DUR="${4:-30}"
SSH="ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5"

hr(){ printf '\n========== %s ==========\n' "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }
iperf_run(){ # STA_IP, server, port, output_file, duration
  local sta="$1" srv="$2" port="$3" out="$4" dur="$5"
  $SSH root@$sta "iperf3 -c $srv -p $port -t $dur -P 4 -O 2 -J" > "$out" 2>&1
}
iperf_sum(){ # parse iperf3 -J output → sender Mbps
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(round(d['end']['sum_sent']['bits_per_second']/1e6,1))" "$1" 2>/dev/null || echo "?"
}

hr "[0] 사전 점검 — 양 STA SSH/iperf3/AP 동일 가입 확인"
for s in $STA1 $STA2; do
  printf "  $s : "
  $SSH root@$s 'which iperf3 && iw dev mlan0 link | awk "/Connected to/{print \$3}; /SSID:/{print \$2}; /freq:/{print \$2}"' 2>&1 | tr '\n' ' '
  echo
done
$SSH root@$STA1 "ping -c1 -W2 $SRV >/dev/null 2>&1" && echo "  iperf3 server $SRV reachable from STA1" || die "server $SRV unreachable from STA1"
$SSH root@$STA2 "ping -c1 -W2 $SRV >/dev/null 2>&1" && echo "  iperf3 server $SRV reachable from STA2" || die "server $SRV unreachable from STA2"

hr "[1] 양쪽 STA 진단 verdict (현재 BW/MCS)"
for s in $STA1 $STA2; do
  echo "--- $s ---"
  $SSH root@$s '/tmp/diag-9098-11ax.sh mlan0 2>&1 | sed -n "/\[11\]/,/\[DONE\]/p" | head -25' 2>/dev/null \
    || $SSH root@$s 'iw dev mlan0 info | grep -E "channel|width"; iw dev mlan0 link | grep -E "freq|bitrate|signal"'
done

# iperf3 server는 별도 호스트에서 -s -p 5201 -D 가 실행중이어야 함
# 다중 포트가 필요하면 5201, 5203를 각각 listen
hr "[2] Phase 1 — STA1 단독 부하 (${DUR}s)"
iperf_run $STA1 $SRV 5201 /tmp/s1_alone.json $DUR &
P1=$!; wait $P1
T1_ALONE=$(iperf_sum /tmp/s1_alone.json)
echo "  STA1 단독: $T1_ALONE Mbps"

hr "[3] Phase 2 — STA2 단독 부하 (${DUR}s)"
iperf_run $STA2 $SRV 5203 /tmp/s2_alone.json $DUR &
P2=$!; wait $P2
T2_ALONE=$(iperf_sum /tmp/s2_alone.json)
echo "  STA2 단독: $T2_ALONE Mbps"

hr "[4] Phase 3 — 동시 부하 (${DUR}s)"
iperf_run $STA1 $SRV 5201 /tmp/s1_both.json $DUR &
P1=$!
iperf_run $STA2 $SRV 5203 /tmp/s2_both.json $DUR &
P2=$!
wait $P1 $P2
T1_BOTH=$(iperf_sum /tmp/s1_both.json)
T2_BOTH=$(iperf_sum /tmp/s2_both.json)
SUM_BOTH=$(awk -v a=$T1_BOTH -v b=$T2_BOTH 'BEGIN{print a+b}')
MAX_ALONE=$(awk -v a=$T1_ALONE -v b=$T2_ALONE 'BEGIN{print (a>b)?a:b}')
SUM_ALONE=$(awk -v a=$T1_ALONE -v b=$T2_ALONE 'BEGIN{print a+b}')
echo "  STA1 동시: $T1_BOTH Mbps"
echo "  STA2 동시: $T2_BOTH Mbps"
echo "  동시 합계: $SUM_BOTH Mbps"

hr "[5] 분석 결과"
RATIO=$(awk -v s=$SUM_BOTH -v m=$MAX_ALONE 'BEGIN{print (m>0)?s/m:0}')
EFF=$(awk -v s=$SUM_BOTH -v a=$SUM_ALONE 'BEGIN{print (a>0)?s/a*100:0}')
cat <<EOF
  단독 baseline : STA1=$T1_ALONE  STA2=$T2_ALONE  (max=$MAX_ALONE)
  동시 결과     : STA1=$T1_BOTH   STA2=$T2_BOTH    합=$SUM_BOTH
  ratio (sum/max_alone) = $RATIO   (≥1.0이면 MU/OFDMA 효과 시사)
  efficiency (sum/sum_alone) = ${EFF}%  (≥75%이면 동시 운용 효율 좋음)

해석:
  ratio < 1.0          : AP가 SU 시분할만 — MU 미발현
  ratio ≈ 1.0          : 동시 운용 가능하나 단일 PPDU 동시 분할은 불명확
  ratio > 1.2          : MU/OFDMA 효과 강하게 시사
  efficiency ≥ 90%     : aggregation 매우 효율적 (MU 또는 OFDMA 발현 가능성 ↑)
  efficiency 50~70%    : SU 시분할 (MU 미발현 또는 단순 분배)

※ 이는 *간접 추론*. PPDU type 직접 증명은 외부 sniffer 필요.
EOF

hr "[6] 양쪽 STA histogram delta (BW80 NSS2 buckets)"
for s in $STA1 $STA2; do
  echo "--- $s histogram BW80 NSS2 ---"
  $SSH root@$s 'grep -E "BW:80MHz.*NSS:2" /proc/mwlan/adapter0/mlan0/histogram/wlan-ant0 2>/dev/null | grep -v "= 0"'
done

hr "[DONE]"
