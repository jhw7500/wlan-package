#!/bin/bash
# pc2_measure.sh — PC2 (wireless client) 시리얼에서 측정
# Plan: wbridge-serial.plan.md FR-S2
# Self-contained: PC2 에 단독 복사해서 실행. root 권한 필요 X.
#
# Usage:
#   pc2_measure.sh --start-at=HH:MM:SS \
#                  --target=PC1_IP [--port=5201] \
#                  --duration=30 [--runs=3] \
#                  [--ping-count=5000] \
#                  --label=ENV --engine=ENG \
#                  [--push-to=BOARD_IP:9999] \
#                  [--out=/tmp/pc2-result.json]
set -euo pipefail
TAG=$(basename "$0")

usage() {
    cat <<EOF
${TAG} — PC2 (wireless) 측정 helper

Required:
  --start-at=HH:MM:SS    측정 시작 시각 (보드/PC1 과 동기)
  --target=IP            PC1 IP
  --label=ENV            환경 식별자 (예: wifi6-pure)
  --engine=ENG           브릿지 engine (예: moal). 결과 메타에만 기록.

Options:
  --port=5201            iperf3 port
  --duration=30          1회 iperf3 측정 시간 (sec)
  --runs=3               iperf3 반복 횟수
  --ping-count=5000      ping 패킷 수
  --push-to=IP:PORT      보드의 nc listener (생략 시 push 안 함, 로컬 보관)
  --out=FILE             결과 JSON 경로 (default /tmp/pc2-result.json)
  -h, --help             도움말
EOF
}

START_AT=""
TARGET=""
PORT=5201
DURATION=30
RUNS=3
PING_COUNT=5000
LABEL=""
ENGINE=""
PUSH_TO=""
OUT=/tmp/pc2-result.json

while [ $# -gt 0 ]; do
    case "$1" in
        --start-at=*)   START_AT="${1#*=}" ;;
        --target=*)     TARGET="${1#*=}" ;;
        --port=*)       PORT="${1#*=}" ;;
        --duration=*)   DURATION="${1#*=}" ;;
        --runs=*)       RUNS="${1#*=}" ;;
        --ping-count=*) PING_COUNT="${1#*=}" ;;
        --label=*)      LABEL="${1#*=}" ;;
        --engine=*)     ENGINE="${1#*=}" ;;
        --push-to=*)    PUSH_TO="${1#*=}" ;;
        --out=*)        OUT="${1#*=}" ;;
        -h|--help)      usage; exit 0 ;;
        *) echo "[ERR] unknown arg: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

# 필수 검증
for v in START_AT TARGET LABEL ENGINE; do
    if [ -z "${!v}" ]; then
        echo "[ERR] --${v,,/_/-} 필수" >&2
        exit 1
    fi
done

for c in iperf3 ping jq date sleep; do
    command -v "$c" >/dev/null 2>&1 || { echo "[ERR] $c 부재 — apt install" >&2; exit 1; }
done

# nc는 push 시에만 필요
if [ -n "$PUSH_TO" ]; then
    command -v nc >/dev/null 2>&1 || { echo "[ERR] nc(netcat) 부재 — apt install ncat 또는 netcat-openbsd" >&2; exit 1; }
fi

# ============================================================
# sleep_until
# ============================================================
sleep_until() {
    local target="$1"
    local now target_epoch wait_sec
    now=$(date +%s)
    target_epoch=$(date -d "today $target" +%s 2>/dev/null) \
        || { echo "[ERR] invalid --start-at: $target" >&2; exit 1; }
    if [ "$target_epoch" -le "$now" ]; then
        echo "[WARN] --start-at=$target 가 이미 지났습니다 — 즉시 시작" >&2
        return 0
    fi
    wait_sec=$((target_epoch - now))
    echo "[INFO] $(date -Iseconds): wait ${wait_sec}s → $target 시작" >&2
    sleep "$wait_sec"
}

# ============================================================
# 시작
# ============================================================
TMPDIR=$(mktemp -d -t pc2-measure.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT INT TERM

echo "=== PC2 measure ==="
echo "host:        $(hostname)"
echo "date:        $(date -Iseconds)"
echo "target:      $TARGET:$PORT"
echo "label:       $LABEL"
echo "engine:      $ENGINE"
echo "start-at:    $START_AT"
echo "duration:    ${DURATION}s × ${RUNS} runs"
echo "ping count:  $PING_COUNT"
echo "push-to:     ${PUSH_TO:-(none)}"
echo "out:         $OUT"
echo

sleep_until "$START_AT"

# ============================================================
# iperf3 × runs
# ============================================================
declare -a TPUTS=()
RETR_TOTAL=0
PARTIAL_FAIL=0

for i in $(seq 1 "$RUNS"); do
    echo "[INFO] iperf3 run ${i}/${RUNS}"
    out_json="${TMPDIR}/iperf-${i}.json"
    if ! iperf3 -c "$TARGET" -p "$PORT" -J -t "$DURATION" -O 2 > "$out_json" 2>"${TMPDIR}/iperf-${i}.err"; then
        echo "[WARN] iperf3 run ${i} 실패: $(tail -c 200 "${TMPDIR}/iperf-${i}.err")" >&2
        PARTIAL_FAIL=$((PARTIAL_FAIL+1))
        TPUTS+=("0")
        continue
    fi
    bps=$(jq -r '.end.sum_received.bits_per_second // .end.sum_sent.bits_per_second // 0' "$out_json")
    mbps=$(awk -v b="$bps" 'BEGIN{printf "%.2f", b/1000000}')
    retr=$(jq -r '.end.sum_sent.retransmits // 0' "$out_json")
    TPUTS+=("$mbps")
    RETR_TOTAL=$((RETR_TOTAL + retr))
    echo "       -> ${mbps} Mbps  retr=${retr}"
done

# 평균/표준편차
TPUT_MEAN=$(printf '%s\n' "${TPUTS[@]}" | awk '{ s+=$1; n++ } END { if (n>0) printf "%.2f", s/n; else print "0" }')
TPUT_STDDEV=$(printf '%s\n' "${TPUTS[@]}" | awk -v m="$TPUT_MEAN" '{ d=$1-m; ss+=d*d; n++ } END { if (n>1) printf "%.2f", sqrt(ss/(n-1)); else print "0" }')

# ============================================================
# ping
# ============================================================
echo "[INFO] ping -i 0.01 -c $PING_COUNT"
ping_log="${TMPDIR}/ping.log"
ping -i 0.01 -c "$PING_COUNT" -q "$TARGET" > "$ping_log" 2>&1 || true

RTT_LINE=$(awk -F'[/= ]+' '/rtt/ { print $6, $7, $8, $9 }' "$ping_log")
read -r RTT_MIN RTT_AVG RTT_MAX RTT_MDEV <<<"$RTT_LINE" || true
: "${RTT_MIN:=0}" "${RTT_AVG:=0}" "${RTT_MAX:=0}" "${RTT_MDEV:=0}"
JITTER=$(awk -v mx="$RTT_MAX" -v mn="$RTT_MIN" 'BEGIN{printf "%.3f", mx-mn}')
LOSS=$(grep -oE '[0-9.]+% packet loss' "$ping_log" | head -1 | awk '{print $1}')
LOSS="${LOSS%\%}"
: "${LOSS:=0}"

# ============================================================
# JSON 생성
# ============================================================
TPUTS_JSON=$(printf '%s\n' "${TPUTS[@]}" | jq -R '. | tonumber' | jq -s '.')

cat > "$OUT" <<EOF
{
  "schema_version": "2.0-serial-pc2",
  "phase": "serial",
  "engine": "${ENGINE}",
  "timestamp": "$(date -Iseconds)",
  "duration_sec": ${DURATION},
  "topology": {
    "wired_ip": "${TARGET}",
    "wireless_ip": "$(hostname -I 2>/dev/null | awk '{print $1}')",
    "env_label": "${LABEL}",
    "direction": "wireless_to_wired"
  },
  "iperf3": {
    "tcp_throughput_mbps": ${TPUTS_JSON},
    "tcp_throughput_mbps_mean": ${TPUT_MEAN},
    "tcp_throughput_mbps_stddev": ${TPUT_STDDEV},
    "tcp_retransmits_total": ${RETR_TOTAL},
    "partial_fail_runs": ${PARTIAL_FAIL}
  },
  "ping": {
    "samples_count": ${PING_COUNT},
    "rtt_min_ms": ${RTT_MIN},
    "rtt_avg_ms": ${RTT_AVG},
    "rtt_max_ms": ${RTT_MAX},
    "rtt_mdev_ms": ${RTT_MDEV},
    "jitter_ms": ${JITTER},
    "packet_loss_pct": ${LOSS}
  }
}
EOF

echo
echo "[INFO] 결과 JSON: $OUT"
echo "       throughput=${TPUT_MEAN} Mbps stddev=${TPUT_STDDEV}"
echo "       jitter=${JITTER} ms  loss=${LOSS}%  retr=${RETR_TOTAL}"

# ============================================================
# nc 푸시
# ============================================================
if [ -n "$PUSH_TO" ]; then
    host="${PUSH_TO%%:*}"
    port="${PUSH_TO##*:}"
    [ -z "$port" ] && port=9999
    echo "[INFO] nc push → ${host}:${port}"
    if nc -h 2>&1 | grep -q -- '-q'; then
        if nc -q 1 "$host" "$port" < "$OUT"; then
            echo "[OK] 보드로 결과 전송 완료"
        else
            echo "[WARN] nc 전송 실패 — 로컬 보관: $OUT"
            exit 2
        fi
    else
        # busybox/ncat 호환
        if nc "$host" "$port" < "$OUT"; then
            echo "[OK] 보드로 결과 전송 완료"
        else
            echo "[WARN] nc 전송 실패 — 로컬 보관: $OUT"
            exit 2
        fi
    fi
else
    echo "[INFO] --push-to 없음 — 결과 로컬 보관: $OUT"
fi

[ "$PARTIAL_FAIL" -gt 0 ] && exit 2
exit 0
