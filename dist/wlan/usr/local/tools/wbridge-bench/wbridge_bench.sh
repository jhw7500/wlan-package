#!/bin/bash
# wbridge_bench.sh — Plan SC: FR-05 (측정), FR-09 (회귀 검증)
# Design Ref: §4.2 (CLI), §3.2 (Bench Result Schema)
#
# End-to-end 측정 (SSH coordinator):
#   [Wired Client PC1] ──ETH── [wbridge board] ──WIFI── [AP] ── [Wireless Client PC2]
#                                    ↑
#                          forwarding path 측정
#
# 보드는 양쪽 PC에 SSH로 iperf3/ping 실행 명령을 보내고, 자체 CPU/conntrack/NIC 통계를 동기 수집.
# trap으로 engine 자동 복원 (Plan Risk 회귀 0%).
#
# Usage:
#   wbridge_bench.sh --phase=<baseline|after> --engine=<pcap|tpacket|moal> \
#                    --wired-host=user@PC1 --wireless-host=user@PC2 \
#                    [--wired-iface=eth0] [--wireless-iface=wlan0] [--duration=N]
set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"

usage() {
    cat <<EOF
Usage: ${TAG} --phase=<baseline|after> --engine=<pcap|tpacket|moal>
              --wired-host=user@HOST --wireless-host=user@HOST
              [--wired-iface=eth0] [--wireless-iface=wlan0]
              [--duration=N] [--output-dir=DIR]

End-to-end forwarding 측정 (SSH coordinator 패턴)

Required:
  --phase=...           baseline | after
  --engine=...          pcap | tpacket | moal
  --wired-host=...      유선 클라이언트 PC (iperf3 server 역할). env WBRIDGE_WIRED_HOST.
  --wireless-host=...   무선 클라이언트 PC (iperf3 client 역할). env WBRIDGE_WIRELESS_HOST.

Options:
  --wired-iface=...     유선 클라이언트의 NIC 이름 (default eth0). IP 자동 추출용.
  --wireless-iface=...  무선 클라이언트의 NIC 이름 (default wlan0).
  --duration=N          iperf3 측정 시간 (default ${WBRIDGE_BENCH_DURATION}s)
  --output-dir=DIR      결과 base 디렉토리 (default ${WBRIDGE_BENCH_OUTPUT})
  -h, --help            도움말

전제조건:
  1. 보드 → 양쪽 PC SSH 키 사전 배포 (BatchMode=yes로 비밀번호 안 묻게)
  2. 양쪽 PC에 iperf3, ping, ip, jq 설치
  3. wbridge가 PC1↔PC2 트래픽을 forwarding하도록 wbridge 운영 중

Exit codes:
  0 = OK, 1 = config-missing, 2 = measurement-failed, 3 = regression-detected
EOF
}

# ============================================================
# 1) 인자 파싱
# ============================================================
PHASE=""
ENGINE=""
WIRED_HOST="${WBRIDGE_WIRED_HOST:-}"
WIRELESS_HOST="${WBRIDGE_WIRELESS_HOST:-}"
WIRED_IFACE="${WBRIDGE_WIRED_IFACE:-eth0}"
WIRELESS_IFACE="${WBRIDGE_WIRELESS_IFACE:-wlan0}"
DURATION="${WBRIDGE_BENCH_DURATION:-60}"
NO_SERVER_SPAWN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --phase=*)           PHASE="${1#*=}" ;;
        --phase)             shift; PHASE="${1:-}" ;;
        --engine=*)          ENGINE="${1#*=}" ;;
        --engine)            shift; ENGINE="${1:-}" ;;
        --wired-host=*)      WIRED_HOST="${1#*=}" ;;
        --wired-host)        shift; WIRED_HOST="${1:-}" ;;
        --wireless-host=*)   WIRELESS_HOST="${1#*=}" ;;
        --wireless-host)     shift; WIRELESS_HOST="${1:-}" ;;
        --wired-iface=*)     WIRED_IFACE="${1#*=}" ;;
        --wired-iface)       shift; WIRED_IFACE="${1:-}" ;;
        --wireless-iface=*)  WIRELESS_IFACE="${1#*=}" ;;
        --wireless-iface)    shift; WIRELESS_IFACE="${1:-}" ;;
        --duration=*)        DURATION="${1#*=}" ;;
        --duration)          shift; DURATION="${1:-}" ;;
        --output-dir=*)      WBRIDGE_BENCH_OUTPUT="${1#*=}" ;;
        --output-dir)        shift; WBRIDGE_BENCH_OUTPUT="${1:-}" ;;
        --no-server-spawn)   NO_SERVER_SPAWN=1 ;;
        -h|--help)           usage; exit 0 ;;
        *)                   die "$EXIT_CONFIG_MISSING" "unknown arg: $1 (--help for usage)" ;;
    esac
    shift
done

# 검증
case "$PHASE" in
    baseline|after) ;;
    "")  die "$EXIT_CONFIG_MISSING" "missing --phase (baseline|after)" ;;
    *)   die "$EXIT_CONFIG_MISSING" "invalid phase: $PHASE" ;;
esac
case "$ENGINE" in
    pcap|tpacket|moal) ;;
    "")  die "$EXIT_CONFIG_MISSING" "missing --engine (pcap|tpacket|moal)" ;;
    *)   die "$EXIT_CONFIG_MISSING" "invalid engine: $ENGINE" ;;
esac
[ -n "$WIRED_HOST" ]    || die "$EXIT_CONFIG_MISSING" "--wired-host or WBRIDGE_WIRED_HOST required (e.g. user\@192.168.1.10)"
[ -n "$WIRELESS_HOST" ] || die "$EXIT_CONFIG_MISSING" "--wireless-host or WBRIDGE_WIRELESS_HOST required"

require_root
require_command jq
require_command bc
require_command mpstat
require_command ssh

# ============================================================
# 2) SSH 옵션 + 헬퍼
# ============================================================
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)

ssh_run() {
    local host="$1"; shift
    ssh "${SSH_OPTS[@]}" "$host" -- "$@"
}

ssh_check() {
    local host="$1"
    local label="$2"
    if ! ssh "${SSH_OPTS[@]}" "$host" 'command -v iperf3 >/dev/null && command -v ip >/dev/null' 2>/dev/null; then
        die "$EXIT_CONFIG_MISSING" "$label SSH/iperf3/ip not reachable: $host"
    fi
    log_info "ssh ok: $label = $host"
}

ssh_check "$WIRED_HOST"    wired
ssh_check "$WIRELESS_HOST" wireless

# 양쪽 PC의 IP 자동 추출 (forwarding 통과 측정에 사용)
WIRED_IP=$(ssh_run "$WIRED_HOST" "ip -4 -o addr show dev $WIRED_IFACE | awk '{print \$4}' | cut -d/ -f1 | head -1") \
    || die "$EXIT_CONFIG_MISSING" "cannot resolve wired IP on $WIRED_HOST/$WIRED_IFACE"
WIRELESS_IP=$(ssh_run "$WIRELESS_HOST" "ip -4 -o addr show dev $WIRELESS_IFACE | awk '{print \$4}' | cut -d/ -f1 | head -1") \
    || die "$EXIT_CONFIG_MISSING" "cannot resolve wireless IP on $WIRELESS_HOST/$WIRELESS_IFACE"
[ -n "$WIRED_IP" ]    || die "$EXIT_CONFIG_MISSING" "empty wired IP (iface=$WIRED_IFACE)"
[ -n "$WIRELESS_IP" ] || die "$EXIT_CONFIG_MISSING" "empty wireless IP (iface=$WIRELESS_IFACE)"
log_info "topology: $WIRELESS_IP ($WIRELESS_HOST) ──wbridge── $WIRED_IP ($WIRED_HOST)"

# ============================================================
# 3) 결과 경로 + cleanup trap
# ============================================================
OUT_FILE=$(_result_path "$PHASE" "$ENGINE")
TMPDIR=$(mktemp -d -t wbridge-bench.XXXXXX)
ORIGINAL_ENGINE=""

cleanup_all() {
    cleanup_pids
    # 원격 server 잔재 정리 — 우리가 spawn한 경우에만
    if [ "$NO_SERVER_SPAWN" -eq 0 ]; then
        ssh_run "$WIRED_HOST" 'pkill -f "iperf3 -s" 2>/dev/null || true' >/dev/null 2>&1 || true
    fi
    _restore_engine_on_exit
    rm -rf "$TMPDIR"
}
trap 'cleanup_all' EXIT INT TERM

# ============================================================
# 4) engine 토글
# ============================================================
ORIGINAL_ENGINE=$(get_engine)
log_info "bench: phase=$PHASE engine=$ENGINE duration=${DURATION}s"
log_info "bench: backup engine=$ORIGINAL_ENGINE"
if [ "$ORIGINAL_ENGINE" != "$ENGINE" ]; then
    set_engine "$ENGINE"
    sleep 2
fi

# ============================================================
# 5) 보드 자체 시작 통계 스냅샷
# ============================================================
CT_BEFORE=$(conntrack_count)
ETH_RX_DROP_BEFORE=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo 0)
ETH_TX_DROP_BEFORE=$(cat /sys/class/net/eth0/statistics/tx_dropped 2>/dev/null || echo 0)
MLAN_RX_DROP_BEFORE=$(cat /sys/class/net/mlan0/statistics/rx_dropped 2>/dev/null || echo 0)
MLAN_TX_DROP_BEFORE=$(cat /sys/class/net/mlan0/statistics/tx_dropped 2>/dev/null || echo 0)

# IRQ/softirq snapshot 시작 — schema v1.2 board_irq 섹션
IRQ_SNAP_PREFIX="${TMPDIR}/wbirq"
IRQ_T_START=$(date +%s)
snapshot_irq "${IRQ_SNAP_PREFIX}-before" || log_warn "IRQ snapshot (before) failed — board_irq will be empty"

# ============================================================
# 6) iperf3 server 기동 (wired-host) — --no-server-spawn 시 skip
# ============================================================
if [ "$NO_SERVER_SPAWN" -eq 0 ]; then
    log_info "bench: starting iperf3 server on $WIRED_HOST"
    ssh_run "$WIRED_HOST" 'pkill -f "iperf3 -s" 2>/dev/null; nohup iperf3 -s -p 5201 >/tmp/iperf3-server.log 2>&1 &' \
        || log_warn "iperf3 server start ssh returned non-zero (continuing)"
    sleep 1
else
    log_info "bench: --no-server-spawn — skip server start (assume already running on $WIRED_HOST:5201)"
fi

# ============================================================
# 7) iperf3 3회 반복 (wireless-host → wired-host, forwarding 통과)
# ============================================================
RUN_COUNT=3
declare -a TPUTS=()
RETR_TOTAL=0

# 보드 자체 mpstat 백그라운드 (전체 측정 시간 동안)
TOTAL_DURATION=$((DURATION * RUN_COUNT))
mpstat_log="${TMPDIR}/mpstat.log"
LC_ALL=C mpstat -P ALL 1 "$TOTAL_DURATION" > "$mpstat_log" &
MPSTAT_PID=$!
register_pid "$MPSTAT_PID"

for i in $(seq 1 "$RUN_COUNT"); do
    log_info "bench: iperf3 run $i/$RUN_COUNT  ($WIRELESS_HOST → $WIRED_IP)"
    iperf_json="${TMPDIR}/iperf-${i}.json"

    if ! ssh_run "$WIRELESS_HOST" "iperf3 -c $WIRED_IP -p 5201 -J -t $DURATION -O 2" > "$iperf_json" 2>"${TMPDIR}/iperf-${i}.err"; then
        log_err "iperf3 run $i failed: $(tail -c 200 "${TMPDIR}/iperf-${i}.err")"
        exit "$EXIT_MEASUREMENT_FAILED"
    fi

    tput_bps=$(jq -r '.end.sum_received.bits_per_second // .end.sum_sent.bits_per_second // 0' "$iperf_json")
    tput_mbps=$(echo "scale=2; $tput_bps / 1000000" | bc -l)
    retr=$(jq -r '.end.sum_sent.retransmits // 0' "$iperf_json")

    TPUTS+=("$tput_mbps")
    RETR_TOTAL=$((RETR_TOTAL + retr))
    printf '  -> %.2f Mbps  retr=%s\n' "$tput_mbps" "$retr"
done

wait "$MPSTAT_PID" 2>/dev/null || true

# 평균/표준편차
TPUT_MEAN=$(printf '%s\n' "${TPUTS[@]}" | awk '{ s += $1; n++ } END { if (n>0) printf "%.2f", s/n; else print "0" }')
TPUT_STDDEV=$(printf '%s\n' "${TPUTS[@]}" | awk -v m="$TPUT_MEAN" '{ d=$1-m; ss+=d*d; n++ } END { if (n>1) printf "%.2f", sqrt(ss/(n-1)); else print "0" }')

# mpstat 평균
CPU_USER=$(awk '/^Average:/ && $2 == "all" { print $3 }' "$mpstat_log")
CPU_SYS=$(awk  '/^Average:/ && $2 == "all" { print $5 }' "$mpstat_log")
CPU_IOW=$(awk  '/^Average:/ && $2 == "all" { print $6 }' "$mpstat_log")
CPU_SOFT=$(awk '/^Average:/ && $2 == "all" { print $8 }' "$mpstat_log")
CPU_IDLE=$(awk '/^Average:/ && $2 == "all" { print $NF }' "$mpstat_log")
: "${CPU_USER:=0}" "${CPU_SYS:=0}" "${CPU_IOW:=0}" "${CPU_SOFT:=0}" "${CPU_IDLE:=0}"

# ============================================================
# 8) ping (wireless → wired, forwarding 통과)
# ============================================================
PING_COUNT=5000
ping_log="${TMPDIR}/ping.log"
log_info "bench: ping wireless→wired -i 0.01 -c $PING_COUNT (~50s)..."
ssh_run "$WIRELESS_HOST" "ping -i 0.01 -c $PING_COUNT -q $WIRED_IP" > "$ping_log" 2>&1 || true

RTT_LINE=$(awk -F'[/= ]+' '/rtt/ { print $6, $7, $8, $9 }' "$ping_log")
read -r RTT_MIN RTT_AVG RTT_MAX RTT_MDEV <<<"$RTT_LINE" || true
: "${RTT_MIN:=0}" "${RTT_AVG:=0}" "${RTT_MAX:=0}" "${RTT_MDEV:=0}"
JITTER_MS=$(echo "scale=3; $RTT_MAX - $RTT_MIN" | bc -l)
PKT_LOSS_LINE=$(grep -oE '[0-9.]+% packet loss' "$ping_log" | head -1 | awk '{print $1}')
PKT_LOSS_PCT="${PKT_LOSS_LINE%\%}"
: "${PKT_LOSS_PCT:=0.0}"

# ============================================================
# 9) 보드 자체 종료 통계 + delta
# ============================================================
CT_AFTER=$(conntrack_count)
ETH_RX_DROP_AFTER=$(cat /sys/class/net/eth0/statistics/rx_dropped 2>/dev/null || echo 0)
ETH_TX_DROP_AFTER=$(cat /sys/class/net/eth0/statistics/tx_dropped 2>/dev/null || echo 0)
MLAN_RX_DROP_AFTER=$(cat /sys/class/net/mlan0/statistics/rx_dropped 2>/dev/null || echo 0)
MLAN_TX_DROP_AFTER=$(cat /sys/class/net/mlan0/statistics/tx_dropped 2>/dev/null || echo 0)

# IRQ/softirq snapshot 종료 + delta JSON 빌드
snapshot_irq "${IRQ_SNAP_PREFIX}-after" || log_warn "IRQ snapshot (after) failed"
IRQ_T_END=$(date +%s)
IRQ_DUR=$((IRQ_T_END - IRQ_T_START))
[ "$IRQ_DUR" -gt 0 ] || IRQ_DUR=1
BOARD_IRQ_JSON=$(irq_diff_to_json "${IRQ_SNAP_PREFIX}-before" "${IRQ_SNAP_PREFIX}-after" "$IRQ_DUR" 2>/dev/null) || \
    BOARD_IRQ_JSON='{"duration_sec":'"$IRQ_DUR"',"hardirq_total":0,"hardirq_per_sec":0,"hardirq_by_iface":{},"hardirq_by_desc":{},"softirq_total":0,"softirq_by_type":{},"softirq_net_rx":0,"softirq_net_tx":0,"softirq_net_per_sec":0}'
HARDIRQ_PER_SEC=$(printf '%s' "$BOARD_IRQ_JSON" | jq -r '.hardirq_per_sec // 0' 2>/dev/null || echo 0)
SIRQ_NET_PER_SEC=$(printf '%s' "$BOARD_IRQ_JSON" | jq -r '.softirq_net_per_sec // 0' 2>/dev/null || echo 0)

CT_DELTA=$((CT_AFTER - CT_BEFORE))
ETH_RX_DROP_DELTA=$((ETH_RX_DROP_AFTER - ETH_RX_DROP_BEFORE))
ETH_TX_DROP_DELTA=$((ETH_TX_DROP_AFTER - ETH_TX_DROP_BEFORE))
MLAN_RX_DROP_DELTA=$((MLAN_RX_DROP_AFTER - MLAN_RX_DROP_BEFORE))
MLAN_TX_DROP_DELTA=$((MLAN_TX_DROP_AFTER - MLAN_TX_DROP_BEFORE))
NIC_DROP_TOTAL=$((ETH_RX_DROP_DELTA + ETH_TX_DROP_DELTA + MLAN_RX_DROP_DELTA + MLAN_TX_DROP_DELTA))

# ============================================================
# 10) regression 판정 (NIC drop 또는 packet loss 기준)
# ============================================================
REGRESSION="false"
if (( $(echo "$PKT_LOSS_PCT > 0.1" | bc -l) )); then
    REGRESSION="true"
fi
if [ "$NIC_DROP_TOTAL" -gt 0 ]; then
    REGRESSION="true"
fi

# ============================================================
# 11) JSON 출력 (Design §3.2 schema v1.1 — end-to-end + board monitoring)
# ============================================================
TPUTS_JSON=$(printf '%s\n' "${TPUTS[@]}" | jq -R '. | tonumber' | jq -s '.')

cat <<EOF | _emit_json_raw "$OUT_FILE"
{
  "schema_version": "1.2",
  "phase": "${PHASE}",
  "engine": "${ENGINE}",
  "timestamp": "$(date -Iseconds)",
  "duration_sec": ${DURATION},
  "topology": {
    "wired_host": "${WIRED_HOST}",
    "wired_iface": "${WIRED_IFACE}",
    "wired_ip": "${WIRED_IP}",
    "wireless_host": "${WIRELESS_HOST}",
    "wireless_iface": "${WIRELESS_IFACE}",
    "wireless_ip": "${WIRELESS_IP}",
    "direction": "wireless_to_wired"
  },
  "iperf3": {
    "tcp_throughput_mbps": ${TPUTS_JSON},
    "tcp_throughput_mbps_mean": ${TPUT_MEAN},
    "tcp_throughput_mbps_stddev": ${TPUT_STDDEV},
    "tcp_retransmits_total": ${RETR_TOTAL}
  },
  "board_cpu": {
    "samples_count": ${TOTAL_DURATION},
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
    "eth0_rx_dropped_delta": ${ETH_RX_DROP_DELTA},
    "eth0_tx_dropped_delta": ${ETH_TX_DROP_DELTA},
    "mlan0_rx_dropped_delta": ${MLAN_RX_DROP_DELTA},
    "mlan0_tx_dropped_delta": ${MLAN_TX_DROP_DELTA},
    "nic_drop_total": ${NIC_DROP_TOTAL}
  },
  "board_irq": ${BOARD_IRQ_JSON},
  "ping": {
    "samples_count": ${PING_COUNT},
    "rtt_min_ms": ${RTT_MIN},
    "rtt_avg_ms": ${RTT_AVG},
    "rtt_max_ms": ${RTT_MAX},
    "rtt_mdev_ms": ${RTT_MDEV},
    "jitter_ms": ${JITTER_MS},
    "packet_loss_pct": ${PKT_LOSS_PCT}
  },
  "regression": {
    "ping_packet_loss_pct": ${PKT_LOSS_PCT},
    "nic_drop_total": ${NIC_DROP_TOTAL},
    "iperf_retransmits_total": ${RETR_TOTAL},
    "regression_detected": ${REGRESSION}
  }
}
EOF

log_info "bench: throughput=${TPUT_MEAN}Mbps softirq=${CPU_SOFT}% jitter=${JITTER_MS}ms loss=${PKT_LOSS_PCT}% nic_drop=${NIC_DROP_TOTAL} hardirq/s=${HARDIRQ_PER_SEC} softirq_net/s=${SIRQ_NET_PER_SEC}"

if [ "$REGRESSION" = "true" ]; then
    log_warn "bench: regression detected (loss > 0.1% or nic_drop > 0)"
    exit "$EXIT_REGRESSION"
fi

exit "$EXIT_OK"
