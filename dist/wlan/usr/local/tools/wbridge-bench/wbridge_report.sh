#!/bin/bash
# wbridge_report.sh — Plan SC: FR-07 (baseline ↔ after 비교 리포트)
# Design Ref: §3.3 (Report Summary Schema), §11.2
#
# baseline/after JSON 결과 디렉토리를 읽어서 engine × metric 비교 markdown 리포트 생성.
# baseline-only 모드(after 디렉토리 비어있음)도 지원.
#
# Usage:
#   wbridge_report.sh [--baseline-dir=DIR] [--after-dir=DIR] [--out=FILE]
#                     [--engines="pcap tpacket moal"] [--label=LABEL]
set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"

usage() {
    cat <<EOF
Usage: ${TAG} [--baseline-dir=DIR] [--after-dir=DIR] [--out=FILE]
              [--engines="pcap tpacket moal"] [--label=LABEL]

Options:
  --baseline-dir=DIR  baseline JSON 디렉토리 (default ${WBRIDGE_BENCH_OUTPUT}/baseline)
  --after-dir=DIR     after JSON 디렉토리 (default ${WBRIDGE_BENCH_OUTPUT}/after)
  --out=FILE          markdown 출력 (default stdout)
  --engines=...       비교할 engine 리스트 (default "${WBRIDGE_BENCH_ENGINES}")
  --label=LABEL       리포트 제목 라벨 (예: "WiFi5", "WiFi6"). 기본 "wbridge-fastpath"
  -h, --help          도움말

동작:
  - baseline-dir만 있으면: 3 engine baseline 비교 표 (engine 간 delta)
  - 둘 다 있으면: baseline ↔ after delta 표 + Plan SUCCESS 판정
  - JSON schema v1.1 (topology, board_monitoring 포함) 가정
EOF
}

# --- 인자 파싱 ---
BASELINE_DIR="${WBRIDGE_BENCH_OUTPUT}/baseline"
AFTER_DIR="${WBRIDGE_BENCH_OUTPUT}/after"
OUT_FILE=""
ENGINES_STR="$WBRIDGE_BENCH_ENGINES"
LABEL="wbridge-fastpath"

while [ $# -gt 0 ]; do
    case "$1" in
        --baseline-dir=*) BASELINE_DIR="${1#*=}" ;;
        --baseline-dir)   shift; BASELINE_DIR="${1:-}" ;;
        --after-dir=*)    AFTER_DIR="${1#*=}" ;;
        --after-dir)      shift; AFTER_DIR="${1:-}" ;;
        --out=*)          OUT_FILE="${1#*=}" ;;
        --out)            shift; OUT_FILE="${1:-}" ;;
        --engines=*)      ENGINES_STR="${1#*=}" ;;
        --engines)        shift; ENGINES_STR="${1:-}" ;;
        --label=*)        LABEL="${1#*=}" ;;
        --label)          shift; LABEL="${1:-}" ;;
        -h|--help)        usage; exit 0 ;;
        *) die "$EXIT_CONFIG_MISSING" "unknown arg: $1 (--help for usage)" ;;
    esac
    shift
done

require_command jq
read -ra ENGINES <<<"$ENGINES_STR"

# --- 가장 최신 JSON 1개 picking ---
latest_json() {
    local dir="$1"
    [ -d "$dir" ] || return 1
    ls -1t "$dir"/*.json 2>/dev/null | head -1
}

# --- 평균값 추출 (안전한 jq) ---
extract_metrics() {
    local json="$1"
    [ -f "$json" ] || return 1
    jq -r '
        [
          .iperf3.tcp_throughput_mbps_mean,
          .iperf3.tcp_throughput_mbps_stddev,
          .iperf3.tcp_retransmits_total,
          .board_cpu.softirq_pct_mean,
          .board_cpu.system_pct_mean,
          .board_cpu.idle_pct_mean,
          .ping.rtt_avg_ms,
          .ping.rtt_max_ms,
          .ping.jitter_ms,
          .ping.packet_loss_pct,
          .board_monitoring.conntrack_delta,
          .board_monitoring.nic_drop_total,
          (.board_irq.hardirq_per_sec // 0),
          (.board_irq.softirq_net_per_sec // 0),
          (.board_irq.hardirq_total // 0)
        ] | @tsv
    ' "$json"
}

# --- 결과 모음 ---
declare -A B_THR B_THR_SD B_RETR B_SOFT B_SYS B_IDLE B_PAVG B_PMAX B_JIT B_LOSS B_CT B_DROP B_HIPS B_SNPS B_HITOT
declare -A A_THR A_THR_SD A_RETR A_SOFT A_SYS A_IDLE A_PAVG A_PMAX A_JIT A_LOSS A_CT A_DROP A_HIPS A_SNPS A_HITOT
HAVE_BASELINE=0
HAVE_AFTER=0

for eng in "${ENGINES[@]}"; do
    bjson=$(latest_json "$BASELINE_DIR/$eng" 2>/dev/null || echo "")
    if [ -n "$bjson" ] && [ -f "$bjson" ]; then
        HAVE_BASELINE=1
        IFS=$'\t' read -r v1 v2 v3 v4 v5 v6 v7 v8 v9 v10 v11 v12 v13 v14 v15 < <(extract_metrics "$bjson")
        B_THR[$eng]="$v1"; B_THR_SD[$eng]="$v2"; B_RETR[$eng]="$v3"
        B_SOFT[$eng]="$v4"; B_SYS[$eng]="$v5"; B_IDLE[$eng]="$v6"
        B_PAVG[$eng]="$v7"; B_PMAX[$eng]="$v8"; B_JIT[$eng]="$v9"; B_LOSS[$eng]="$v10"
        B_CT[$eng]="$v11"; B_DROP[$eng]="$v12"
        B_HIPS[$eng]="${v13:-0}"; B_SNPS[$eng]="${v14:-0}"; B_HITOT[$eng]="${v15:-0}"
    fi

    ajson=$(latest_json "$AFTER_DIR/$eng" 2>/dev/null || echo "")
    if [ -n "$ajson" ] && [ -f "$ajson" ]; then
        HAVE_AFTER=1
        IFS=$'\t' read -r v1 v2 v3 v4 v5 v6 v7 v8 v9 v10 v11 v12 v13 v14 v15 < <(extract_metrics "$ajson")
        A_THR[$eng]="$v1"; A_THR_SD[$eng]="$v2"; A_RETR[$eng]="$v3"
        A_SOFT[$eng]="$v4"; A_SYS[$eng]="$v5"; A_IDLE[$eng]="$v6"
        A_PAVG[$eng]="$v7"; A_PMAX[$eng]="$v8"; A_JIT[$eng]="$v9"; A_LOSS[$eng]="$v10"
        A_CT[$eng]="$v11"; A_DROP[$eng]="$v12"
        A_HIPS[$eng]="${v13:-0}"; A_SNPS[$eng]="${v14:-0}"; A_HITOT[$eng]="${v15:-0}"
    fi
done

[ "$HAVE_BASELINE" -eq 1 ] || die "$EXIT_CONFIG_MISSING" "no baseline data in $BASELINE_DIR"

# --- delta% 계산 (after vs baseline) ---
delta_pct() {
    local b="$1" a="$2"
    if [ -z "$b" ] || [ -z "$a" ] || [ "$b" = "null" ] || [ "$a" = "null" ]; then
        echo "—"
        return
    fi
    if (( $(echo "$b == 0" | bc -l 2>/dev/null || echo 0) )); then
        echo "—"
        return
    fi
    echo "scale=2; ($a - $b) * 100 / $b" | bc -l 2>/dev/null | awk '{printf "%+.1f%%", $1}'
}

# --- markdown 빌드 ---
TS=$(date -Iseconds)
TMP=$(mktemp)

{
    printf '# %s — Performance Comparison Report\n\n' "$LABEL"
    printf '> Generated: %s\n' "$TS"
    printf '> Baseline dir: `%s`\n' "$BASELINE_DIR"
    if [ "$HAVE_AFTER" -eq 1 ]; then
        printf '> After dir:    `%s`\n' "$AFTER_DIR"
    fi
    printf '> Engines: %s\n\n' "${ENGINES[*]}"

    if [ "$HAVE_AFTER" -eq 0 ]; then
        printf '## Baseline Comparison (3 engines, baseline only)\n\n'
        printf '| Metric | %s |\n' "$(IFS=' | '; echo "${ENGINES[*]}")"
        printf '|---|'; for _ in "${ENGINES[@]}"; do printf '%s' '---:|'; done; printf '\n'
        for label_metric in \
            "Throughput (Mbps)::B_THR" \
            "Throughput stddev::B_THR_SD" \
            "TCP retransmits::B_RETR" \
            "Softirq %::B_SOFT" \
            "System %::B_SYS" \
            "Idle %::B_IDLE" \
            "Ping avg (ms)::B_PAVG" \
            "Ping max (ms)::B_PMAX" \
            "Jitter (ms)::B_JIT" \
            "Packet loss %::B_LOSS" \
            "Conntrack delta::B_CT" \
            "NIC drop total::B_DROP" \
            "Hardirq /sec::B_HIPS" \
            "Softirq NET/sec::B_SNPS" \
            "Hardirq total::B_HITOT"
        do
            metric="${label_metric%%::*}"
            arr="${label_metric##*::}"
            printf '| %s |' "$metric"
            for eng in "${ENGINES[@]}"; do
                # bash indirection
                v=$(eval "echo \"\${${arr}[$eng]:-—}\"")
                printf ' %s |' "${v:-—}"
            done
            printf '\n'
        done
        printf '\n'

        # baseline 비교 verdict
        printf '### Engine Comparison Verdict\n\n'
        if [ -n "${B_THR[moal]:-}" ] && [ -n "${B_THR[pcap]:-}" ]; then
            d_moal_pcap_thr=$(delta_pct "${B_THR[pcap]}" "${B_THR[moal]}")
            echo "- **moal vs pcap throughput**: $d_moal_pcap_thr (moal 우위)"
        fi
        if [ -n "${B_SOFT[moal]:-}" ] && [ -n "${B_SOFT[pcap]:-}" ]; then
            d_moal_pcap_cpu=$(delta_pct "${B_SOFT[pcap]}" "${B_SOFT[moal]}")
            echo "- **moal vs pcap softirq**: $d_moal_pcap_cpu (negative=moal 우위)"
        fi
        if [ -n "${B_THR[tpacket]:-}" ] && [ -n "${B_THR[pcap]:-}" ]; then
            d_tpacket_pcap_thr=$(delta_pct "${B_THR[pcap]}" "${B_THR[tpacket]}")
            echo "- **tpacket vs pcap throughput**: $d_tpacket_pcap_thr (tpacket=mmap zerocopy 효과)"
        fi
        # IRQ 비교 — schema v1.2 board_irq 기준
        if [ -n "${B_HIPS[moal]:-}" ] && [ -n "${B_HIPS[pcap]:-}" ]; then
            d_hi_moal_pcap=$(delta_pct "${B_HIPS[pcap]}" "${B_HIPS[moal]}")
            echo "- **moal vs pcap hardirq/sec**: $d_hi_moal_pcap (작을수록 mode 무관 가설 일치 — 트래픽 동일하면 hardirq도 비슷해야 함)"
        fi
        if [ -n "${B_SNPS[moal]:-}" ] && [ -n "${B_SNPS[pcap]:-}" ]; then
            d_sn_moal_pcap=$(delta_pct "${B_SNPS[pcap]}" "${B_SNPS[moal]}")
            echo "- **moal vs pcap softirq NET/sec**: $d_sn_moal_pcap (negative=moal NET softirq 적음 = mode 효과)"
        fi
        printf '\n'

        printf '### Notes\n\n'
        echo "- baseline 데이터만 존재. after 측정 후 다시 실행하면 baseline ↔ after delta 표시"
        echo "- conntrack_delta 모두 0이면 bridge_netfilter OFF — NF Flowtable 효과 없음"
        echo "- hardirq/sec이 모드별로 거의 같다면 = 트래픽 양 동일 (NIC 인터럽트 발생량은 모드 무관 가설 검증)"
        echo "- softirq NET/sec 차이 = 모드별 packet 처리 비용 차이 (driver-level forwarding 효과)"
    else
        # baseline + after 비교
        printf '## Baseline ↔ After Comparison\n\n'
        for eng in "${ENGINES[@]}"; do
            [ -n "${B_THR[$eng]:-}" ] || continue
            [ -n "${A_THR[$eng]:-}" ] || { printf '> Engine **%s**: after data missing — skip\n\n' "$eng"; continue; }
            printf '### Engine: %s\n\n' "$eng"
            printf '| Metric | Baseline | After | Delta |\n'
            printf '|---|---:|---:|---:|\n'
            for triple in \
                "Throughput (Mbps)|B_THR|A_THR" \
                "Softirq %|B_SOFT|A_SOFT" \
                "System %|B_SYS|A_SYS" \
                "Ping avg (ms)|B_PAVG|A_PAVG" \
                "Ping max (ms)|B_PMAX|A_PMAX" \
                "Jitter (ms)|B_JIT|A_JIT" \
                "Packet loss %|B_LOSS|A_LOSS" \
                "TCP retransmits|B_RETR|A_RETR" \
                "Conntrack delta|B_CT|A_CT" \
                "NIC drop total|B_DROP|A_DROP" \
                "Hardirq /sec|B_HIPS|A_HIPS" \
                "Softirq NET/sec|B_SNPS|A_SNPS" \
                "Hardirq total|B_HITOT|A_HITOT"
            do
                IFS='|' read -r m b_arr a_arr <<<"$triple"
                bval=$(eval "echo \${${b_arr}[$eng]:-—}")
                aval=$(eval "echo \${${a_arr}[$eng]:-—}")
                d=$(delta_pct "$bval" "$aval")
                printf '| %s | %s | %s | %s |\n' "$m" "$bval" "$aval" "$d"
            done
            printf '\n'
        done

        # Plan SUCCESS verdict
        printf '## Plan SUCCESS Verdict\n\n'
        printf '> 기준: throughput +15%% OR CPU(softirq) -20%% OR jitter -10%% (셋 중 하나) + 회귀 0%%\n\n'
        VERDICT="UNKNOWN"
        for eng in "${ENGINES[@]}"; do
            [ -n "${B_THR[$eng]:-}" ] && [ -n "${A_THR[$eng]:-}" ] || continue
            d_thr=$(echo "scale=4; (${A_THR[$eng]} - ${B_THR[$eng]}) * 100 / ${B_THR[$eng]}" | bc -l 2>/dev/null || echo 0)
            d_cpu=$(echo "scale=4; (${A_SOFT[$eng]} - ${B_SOFT[$eng]}) * 100 / ${B_SOFT[$eng]}" | bc -l 2>/dev/null || echo 0)
            d_jit=$(echo "scale=4; (${A_JIT[$eng]} - ${B_JIT[$eng]}) * 100 / ${B_JIT[$eng]}" | bc -l 2>/dev/null || echo 0)
            verdict="—"
            if (( $(echo "$d_thr > 15" | bc -l 2>/dev/null || echo 0) )); then verdict="✅ PASS (throughput +${d_thr}%)"
            elif (( $(echo "$d_cpu < -20" | bc -l 2>/dev/null || echo 0) )); then verdict="✅ PASS (softirq ${d_cpu}%)"
            elif (( $(echo "$d_jit < -10" | bc -l 2>/dev/null || echo 0) )); then verdict="✅ PASS (jitter ${d_jit}%)"
            else verdict="❌ FAIL (improvement < target)"
            fi
            # 회귀 검증
            regression=""
            [ "${A_LOSS[$eng]:-0}" != "0" ] && (( $(echo "${A_LOSS[$eng]} > 0.1" | bc -l 2>/dev/null || echo 0) )) && regression=" REGRESSION:loss"
            (( "${A_DROP[$eng]:-0}" > 0 )) && regression="${regression} REGRESSION:nic_drop"
            echo "- **$eng**: ${verdict}${regression}"
        done
        printf '\n'
    fi

    printf '%s\n' '---'
    printf '_Generated by wbridge_report.sh_\n'
} > "$TMP"

if [ -n "$OUT_FILE" ]; then
    mkdir -p "$(dirname "$OUT_FILE")"
    mv "$TMP" "$OUT_FILE"
    log_info "report written: $OUT_FILE"
    echo "$OUT_FILE"
else
    cat "$TMP"
    rm -f "$TMP"
fi

exit "$EXIT_OK"
