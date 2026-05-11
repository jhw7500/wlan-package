#!/bin/bash
# wbridge_irq_diff.sh — /proc/interrupts + /proc/softirqs 차분 측정 (standalone)
#
# Plan SC: FR-05 보강 (NIC IRQ 부하 정량화)
# Design Ref: §3.2 board_irq schema (v1.2)
#
# 모드 (택일):
#   1) one-shot:  --duration=N      → snapshot → sleep N → snapshot → diff
#   2) snap:      --snap=PREFIX     → 시작점 저장 (${PREFIX}.interrupts/.softirqs)
#   3) diff:      --diff=PREFIX     → 저장된 snapshot과 현재 비교 → diff
#
# Usage:
#   wbridge_irq_diff.sh --duration=30 [--out=FILE] [--text|--json]
#   wbridge_irq_diff.sh --snap=/tmp/wbirq-A
#   wbridge_irq_diff.sh --diff=/tmp/wbirq-A [--out=FILE] [--text|--json]
#
# 활용 예:
#   # 30초간 측정 후 표 출력
#   wbridge_irq_diff.sh --duration=30 --text
#
#   # iperf3 측정과 합쳐서
#   wbridge_irq_diff.sh --snap=/tmp/wbirq
#   iperf3 -c 192.168.0.10 -t 30
#   wbridge_irq_diff.sh --diff=/tmp/wbirq --text
#
set -euo pipefail
TAG=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "${SCRIPT_DIR}/lib/_common.sh"

usage() {
    cat <<EOF
Usage: ${TAG} (--duration=N | --snap=PREFIX | --diff=PREFIX) [options]

Modes (택일):
  --duration=N    N초간 측정 (snap → sleep → snap → diff)
  --snap=PREFIX   현재 snapshot 저장 (\${PREFIX}.interrupts, .softirqs)
  --diff=PREFIX   기존 snapshot과 현재 비교 → diff 출력

Options:
  --out=FILE      JSON/text 결과 파일 (디렉토리 자동 생성)
  --json          JSON 출력 (default)
  --text          사람용 표 출력
  -h, --help

Exit codes:
  0=OK, 1=config-missing, 2=measurement-failed
EOF
}

DURATION=""
SNAP=""
DIFF_FROM=""
OUT_FILE=""
FORMAT="json"

while [ $# -gt 0 ]; do
    case "$1" in
        --duration=*) DURATION="${1#*=}" ;;
        --duration)   shift; DURATION="${1:-}" ;;
        --snap=*)     SNAP="${1#*=}" ;;
        --snap)       shift; SNAP="${1:-}" ;;
        --diff=*)     DIFF_FROM="${1#*=}" ;;
        --diff)       shift; DIFF_FROM="${1:-}" ;;
        --out=*)      OUT_FILE="${1#*=}" ;;
        --out)        shift; OUT_FILE="${1:-}" ;;
        --json)       FORMAT="json" ;;
        --text)       FORMAT="text" ;;
        -h|--help)    usage; exit 0 ;;
        *) die "$EXIT_CONFIG_MISSING" "unknown arg: $1 (--help for usage)" ;;
    esac
    shift
done

# 모드 검증
modes=0
[ -n "$DURATION" ]  && modes=$((modes + 1))
[ -n "$SNAP" ]      && modes=$((modes + 1))
[ -n "$DIFF_FROM" ] && modes=$((modes + 1))
[ "$modes" -eq 1 ] || die "$EXIT_CONFIG_MISSING" "exactly one of --duration / --snap / --diff required"

# snap 모드: 단순 저장
if [ -n "$SNAP" ]; then
    snapshot_irq "$SNAP" || die "$EXIT_MEASUREMENT_FAILED" "snapshot_irq failed"
    log_info "snap saved: ${SNAP}.interrupts, ${SNAP}.softirqs"
    exit "$EXIT_OK"
fi

# diff/duration 공통: temp 결과 파일 cleanup
TMP_BEFORE=""
TMP_AFTER=""
TMP_AFTER_OWNED=0
cleanup_temps() {
    [ -n "${TMP_BEFORE:-}" ] && [ "${TMP_BEFORE_OWNED:-0}" -eq 1 ] \
        && rm -f "${TMP_BEFORE}.interrupts" "${TMP_BEFORE}.softirqs" 2>/dev/null || true
    [ -n "${TMP_AFTER:-}" ] && [ "$TMP_AFTER_OWNED" -eq 1 ] \
        && rm -f "${TMP_AFTER}.interrupts" "${TMP_AFTER}.softirqs" 2>/dev/null || true
}
trap 'cleanup_temps' EXIT INT TERM

if [ -n "$DURATION" ]; then
    case "$DURATION" in
        ''|*[!0-9]*) die "$EXIT_CONFIG_MISSING" "--duration must be positive integer" ;;
    esac
    [ "$DURATION" -gt 0 ] || die "$EXIT_CONFIG_MISSING" "--duration must be > 0"

    TMP_BEFORE=$(mktemp -u -t wbirq-before.XXXXXX)
    TMP_BEFORE_OWNED=1
    snapshot_irq "$TMP_BEFORE" || die "$EXIT_MEASUREMENT_FAILED" "snapshot before failed"
    log_info "duration mode: snap → sleep ${DURATION}s → snap"
    sleep "$DURATION"

    TMP_AFTER=$(mktemp -u -t wbirq-after.XXXXXX)
    TMP_AFTER_OWNED=1
    snapshot_irq "$TMP_AFTER" || die "$EXIT_MEASUREMENT_FAILED" "snapshot after failed"
    DUR_USED="$DURATION"
else
    # diff 모드
    [ -f "${DIFF_FROM}.interrupts" ] || die "$EXIT_MEASUREMENT_FAILED" "snapshot missing: ${DIFF_FROM}.interrupts"
    [ -f "${DIFF_FROM}.softirqs" ]   || die "$EXIT_MEASUREMENT_FAILED" "snapshot missing: ${DIFF_FROM}.softirqs"
    TMP_BEFORE="$DIFF_FROM"
    TMP_BEFORE_OWNED=0

    TMP_AFTER=$(mktemp -u -t wbirq-after.XXXXXX)
    TMP_AFTER_OWNED=1
    snapshot_irq "$TMP_AFTER" || die "$EXIT_MEASUREMENT_FAILED" "snapshot now failed"

    # duration 추정: snapshot file mtime 차분
    b_mtime=$(stat -c %Y "${TMP_BEFORE}.interrupts" 2>/dev/null || echo 0)
    a_mtime=$(stat -c %Y "${TMP_AFTER}.interrupts"  2>/dev/null || echo 0)
    DUR_USED=$((a_mtime - b_mtime))
    [ "$DUR_USED" -gt 0 ] || DUR_USED=1
fi

# JSON 빌드
JSON=$(irq_diff_to_json "$TMP_BEFORE" "$TMP_AFTER" "$DUR_USED")

# 출력 분기
emit_payload() {
    local payload="$1"
    if [ -n "$OUT_FILE" ]; then
        mkdir -p "$(dirname "$OUT_FILE")"
        printf '%s\n' "$payload" > "$OUT_FILE"
        log_info "result: $OUT_FILE"
    else
        printf '%s\n' "$payload"
    fi
}

if [ "$FORMAT" = "json" ]; then
    emit_payload "$JSON"
else
    require_command jq
    text=$(printf '%s' "$JSON" | jq -r '
        "duration_sec: \(.duration_sec)",
        "",
        "## hardirq",
        "  total      : \(.hardirq_total)",
        "  per_sec    : \(.hardirq_per_sec)",
        "  by_iface   :",
        ( (.hardirq_by_iface // {}) | to_entries
          | sort_by(-.value)
          | map("    - \(.key): \(.value)") | .[] ),
        "",
        "## softirq",
        "  total      : \(.softirq_total)",
        "  net_per_sec: \(.softirq_net_per_sec)",
        "  by_type    :",
        ( (.softirq_by_type // {}) | to_entries
          | sort_by(-.value)
          | map("    - \(.key): \(.value)") | .[] )
    ')
    emit_payload "$text"
fi

exit "$EXIT_OK"
