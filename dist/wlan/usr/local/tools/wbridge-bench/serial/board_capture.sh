#!/bin/bash
# board_capture.sh — 보드 시리얼에서 측정 통계 수집 + PC2 결과 합치기
# Plan: wbridge-serial.plan.md FR-S3, FR-S4
# Self-contained: 보드에 단독 복사해서 실행. root 권한 필요.
#
# 흐름:
#   1) nc -l <port> > /tmp/pc2-result.json  (백그라운드)
#   2) start-at 까지 sleep
#   3) conntrack/eth0/mlan0 통계 before
#   4) mpstat -P ALL 1 (duration*runs+여유)초 백그라운드
#   5) duration*runs + push-buffer 동안 wait
#   6) mpstat 종료, 통계 after, delta
#   7) PC2 JSON 도착 확인 (max wait 30s)
#   8) board JSON + PC2 JSON 합쳐 통합 JSON 저장
#
# Usage:
#   board_capture.sh --start-at=HH:MM:SS \
#                    --duration=30 [--runs=3] \
#                    --label=ENV --engine=ENG \
#                    [--listen-port=9999] \
#                    [--push-buffer=15] \
#                    [--out-base=/var/log/wbridge-bench/serial]
set -euo pipefail
TAG=$(basename "$0")

usage() {
    cat <<EOF
${TAG} — 보드 측정 helper (시리얼 콘솔용)

Required:
  --start-at=HH:MM:SS    측정 시작 시각
  --label=ENV            환경 식별자
  --engine=ENG           브릿지 engine (메타용 — 실제 토글은 별도)

Options:
  --duration=30          1회 iperf3 측정 시간 (PC2 와 일치)
  --runs=3               iperf3 반복 횟수 (PC2 와 일치)
  --listen-port=9999     PC2 결과 nc 수신 포트
  --push-buffer=15       PC2 push + ping 마무리 여유 시간 (sec)
  --pc2-wait=30          PC2 JSON 도착 max 대기 (sec)
  --out-base=DIR         결과 base (default /var/log/wbridge-bench/serial)
  -h, --help             도움말
EOF
}

START_AT=""
DURATION=30
RUNS=3
LABEL=""
ENGINE=""
LISTEN_PORT=9999
PUSH_BUFFER=15
PC2_WAIT=30
OUT_BASE=/var/log/wbridge-bench/serial

while [ $# -gt 0 ]; do
    case "$1" in
        --start-at=*)    START_AT="${1#*=}" ;;
        --duration=*)    DURATION="${1#*=}" ;;
        --runs=*)        RUNS="${1#*=}" ;;
        --label=*)       LABEL="${1#*=}" ;;
        --engine=*)      ENGINE="${1#*=}" ;;
        --listen-port=*) LISTEN_PORT="${1#*=}" ;;
        --push-buffer=*) PUSH_BUFFER="${1#*=}" ;;
        --pc2-wait=*)    PC2_WAIT="${1#*=}" ;;
        --out-base=*)    OUT_BASE="${1#*=}" ;;
        -h|--help)       usage; exit 0 ;;
        *) echo "[ERR] unknown arg: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

for v in START_AT LABEL ENGINE; do
    [ -n "${!v}" ] || { echo "[ERR] --${v,,/_/-} 필수" >&2; exit 1; }
done

[ "$(id -u)" -eq 0 ] || { echo "[ERR] root 권한 필요" >&2; exit 1; }

for c in jq mpstat nc date sleep awk; do
    command -v "$c" >/dev/null 2>&1 || { echo "[ERR] $c 부재 (apt install)" >&2; exit 1; }
done

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
# 출력 경로 / tmp / trap
# ============================================================
TS=$(date -u +'%Y-%m-%dT%H-%M-%SZ')
ENGINE_DIR="${OUT_BASE}/baseline/${ENGINE}"
mkdir -p "$ENGINE_DIR"
OUT_JSON="${ENGINE_DIR}/${TS}.json"

TMPDIR=$(mktemp -d -t board-capture.XXXXXX)
PC2_JSON="${TMPDIR}/pc2-result.json"

NC_PID=""
MPSTAT_PID=""

cleanup() {
    [ -n "$MPSTAT_PID" ] && kill -TERM "$MPSTAT_PID" 2>/dev/null || true
    [ -n "$NC_PID" ]     && kill -TERM "$NC_PID"     2>/dev/null || true
    rm -rf "$TMPDIR"
}
trap cleanup EXIT INT TERM

echo "=== board capture ==="
echo "host:        $(hostname)"
echo "date:        $(date -Iseconds)"
echo "label:       $LABEL"
echo "engine:      $ENGINE"
echo "start-at:    $START_AT"
echo "duration:    ${DURATION}s × ${RUNS} runs (= $((DURATION*RUNS))s)"
echo "listen:      tcp/${LISTEN_PORT}"
echo "out-base:    $OUT_BASE"
echo "out-json:    $OUT_JSON"
echo

# ============================================================
# nc listener (백그라운드, PC2 결과 받음)
# ============================================================
echo "[INFO] nc -l ${LISTEN_PORT} 시작 (백그라운드)"
if nc -h 2>&1 | grep -q -- '-N'; then
    nc -l -p "$LISTEN_PORT" -N > "$PC2_JSON" 2>/dev/null &
elif nc -h 2>&1 | grep -q -- '\-l'; then
    # netcat-openbsd: nc -l <port>
    nc -l "$LISTEN_PORT" > "$PC2_JSON" 2>/dev/null &
else
    echo "[ERR] nc 옵션 호환성 문제 — 수동으로 nc -l ${LISTEN_PORT} 실행 후 재시도" >&2
    exit 1
fi
NC_PID=$!

# ============================================================
# 시작 시각까지 sleep
# ============================================================
sleep_until "$START_AT"
T_START=$(date -Iseconds)

# ============================================================
# 통계 before
# ============================================================
CT_BEFORE=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)
ETH_RX_DROP_BEFORE=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo 0)
ETH_TX_DROP_BEFORE=$(cat /sys/class/net/eth0/statistics/tx_dropped 2>/dev/null || echo 0)
MLAN_RX_DROP_BEFORE=$(cat /sys/class/net/mlan0/statistics/rx_dropped 2>/dev/null || echo 0)
MLAN_TX_DROP_BEFORE=$(cat /sys/class/net/mlan0/statistics/tx_dropped 2>/dev/null || echo 0)

# ============================================================
# mpstat 백그라운드 (전체 측정 시간 동안)
# ============================================================
TOTAL_DUR=$((DURATION * RUNS))
MPSTAT_LOG="${TMPDIR}/mpstat.log"
LC_ALL=C mpstat -P ALL 1 "$TOTAL_DUR" > "$MPSTAT_LOG" &
MPSTAT_PID=$!

echo "[INFO] mpstat ${TOTAL_DUR}s 캡처 시작 (pid=$MPSTAT_PID)"

# 측정 + push-buffer 만큼 대기
WAIT_TOTAL=$((TOTAL_DUR + PUSH_BUFFER))
echo "[INFO] 측정 진행 중 — ${WAIT_TOTAL}s 대기"
sleep "$WAIT_TOTAL"

# ============================================================
# mpstat 마감
# ============================================================
wait "$MPSTAT_PID" 2>/dev/null || true
MPSTAT_PID=""

CPU_USER=$(awk '/^Average:/ && $2 == "all" { print $3 }' "$MPSTAT_LOG")
CPU_SYS=$(awk  '/^Average:/ && $2 == "all" { print $5 }' "$MPSTAT_LOG")
CPU_IOW=$(awk  '/^Average:/ && $2 == "all" { print $6 }' "$MPSTAT_LOG")
CPU_SOFT=$(awk '/^Average:/ && $2 == "all" { print $8 }' "$MPSTAT_LOG")
CPU_IDLE=$(awk '/^Average:/ && $2 == "all" { print $NF }' "$MPSTAT_LOG")
: "${CPU_USER:=0}" "${CPU_SYS:=0}" "${CPU_IOW:=0}" "${CPU_SOFT:=0}" "${CPU_IDLE:=0}"

# ============================================================
# 통계 after, delta
# ============================================================
CT_AFTER=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)
ETH_RX_DROP_AFTER=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo 0)
ETH_TX_DROP_AFTER=$(cat /sys/class/net/eth0/statistics/tx_dropped 2>/dev/null || echo 0)
MLAN_RX_DROP_AFTER=$(cat /sys/class/net/mlan0/statistics/rx_dropped 2>/dev/null || echo 0)
MLAN_TX_DROP_AFTER=$(cat /sys/class/net/mlan0/statistics/tx_dropped 2>/dev/null || echo 0)

CT_DELTA=$((CT_AFTER - CT_BEFORE))
ETH_RX_DELTA=$((ETH_RX_DROP_AFTER - ETH_RX_DROP_BEFORE))
ETH_TX_DELTA=$((ETH_TX_DROP_AFTER - ETH_TX_DROP_BEFORE))
MLAN_RX_DELTA=$((MLAN_RX_DROP_AFTER - MLAN_RX_DROP_BEFORE))
MLAN_TX_DELTA=$((MLAN_TX_DROP_AFTER - MLAN_TX_DROP_BEFORE))
NIC_DROP_TOTAL=$((ETH_RX_DELTA + ETH_TX_DELTA + MLAN_RX_DELTA + MLAN_TX_DELTA))

# ============================================================
# PC2 JSON 도착 대기
# ============================================================
echo "[INFO] PC2 결과 대기 (max ${PC2_WAIT}s)..."
PC2_RESULT_PRESENT=false
for _ in $(seq 1 "$PC2_WAIT"); do
    if [ -s "$PC2_JSON" ]; then
        # JSON 유효성 확인
        if jq empty "$PC2_JSON" 2>/dev/null; then
            PC2_RESULT_PRESENT=true
            break
        fi
    fi
    sleep 1
done

# nc listener 종료
if [ -n "$NC_PID" ] && kill -0 "$NC_PID" 2>/dev/null; then
    kill -TERM "$NC_PID" 2>/dev/null || true
    NC_PID=""
fi

# ============================================================
# 통합 JSON 빌드
# ============================================================
BOARD_PARTIAL=$(cat <<EOF
{
  "schema_version": "2.0-serial",
  "phase": "serial",
  "engine": "${ENGINE}",
  "timestamp_started": "${T_START}",
  "timestamp_completed": "$(date -Iseconds)",
  "duration_sec": ${DURATION},
  "runs": ${RUNS},
  "topology": {
    "env_label": "${LABEL}",
    "direction": "wireless_to_wired"
  },
  "board_cpu": {
    "samples_count": ${TOTAL_DUR},
    "user_pct_mean": ${CPU_USER},
    "system_pct_mean": ${CPU_SYS},
    "iowait_pct_mean": ${CPU_IOW},
    "softirq_pct_mean": ${CPU_SOFT},
    "idle_pct_mean": ${CPU_IDLE}
  },
  "board_monitoring": {
    "conntrack_count_before": ${CT_BEFORE},
    "conntrack_count_after": ${CT_AFTER},
    "conntrack_delta": ${CT_DELTA},
    "eth0_rx_dropped_delta": ${ETH_RX_DELTA},
    "eth0_tx_dropped_delta": ${ETH_TX_DELTA},
    "mlan0_rx_dropped_delta": ${MLAN_RX_DELTA},
    "mlan0_tx_dropped_delta": ${MLAN_TX_DELTA},
    "nic_drop_total": ${NIC_DROP_TOTAL}
  },
  "pc2_result_present": ${PC2_RESULT_PRESENT}
}
EOF
)

if [ "$PC2_RESULT_PRESENT" = "true" ]; then
    # PC2 JSON 의 iperf3 / ping / topology(wired_ip,wireless_ip) merge
    echo "$BOARD_PARTIAL" | jq --slurpfile pc2 "$PC2_JSON" '
        . as $b
        | $b
        | .iperf3 = ($pc2[0].iperf3 // {})
        | .ping   = ($pc2[0].ping   // {})
        | .topology.wired_ip    = ($pc2[0].topology.wired_ip    // null)
        | .topology.wireless_ip = ($pc2[0].topology.wireless_ip // null)
    ' > "$OUT_JSON"
    echo "[OK] PC2 결과 병합 완료"
else
    echo "[WARN] PC2 JSON 미수신 — board 통계만 저장"
    echo "$BOARD_PARTIAL" > "$OUT_JSON"
fi

mkdir -p "$(dirname "$OUT_JSON")"

echo
echo "=== 결과 요약 ==="
echo "out:        $OUT_JSON"
echo "softirq:    ${CPU_SOFT}%"
echo "system:     ${CPU_SYS}%"
echo "ct_delta:   ${CT_DELTA}"
echo "nic_drop:   ${NIC_DROP_TOTAL}"
if [ "$PC2_RESULT_PRESENT" = "true" ]; then
    THR=$(jq -r '.iperf3.tcp_throughput_mbps_mean // "n/a"' "$OUT_JSON")
    JIT=$(jq -r '.ping.jitter_ms // "n/a"' "$OUT_JSON")
    LOSS=$(jq -r '.ping.packet_loss_pct // "n/a"' "$OUT_JSON")
    RETR=$(jq -r '.iperf3.tcp_retransmits_total // "n/a"' "$OUT_JSON")
    echo "throughput: ${THR} Mbps"
    echo "jitter:     ${JIT} ms"
    echo "loss:       ${LOSS} %"
    echo "retr:       ${RETR}"
fi
echo
echo "다음: wbridge_serial_report.sh --label=${LABEL}"

exit 0
