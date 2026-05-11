#!/bin/bash
# wbridge_serial_report.sh — board_capture.sh 결과를 markdown 리포트로
# Plan: wbridge-serial.plan.md FR-S5
# 보드에서 실행. 기존 wbridge_report.sh 를 그대로 호출 (호환).
#
# Usage:
#   wbridge_serial_report.sh [--base-dir=DIR] [--label=ENV]
#                             [--engines="pcap tpacket moal"] [--out=FILE]
set -euo pipefail
TAG=$(basename "$0")

usage() {
    cat <<EOF
${TAG} — board_capture.sh 결과 → markdown

Options:
  --base-dir=DIR      board_capture.sh 의 --out-base (default /var/log/wbridge-bench/serial)
  --label=ENV         리포트 제목에 표시 (default conf-less = unknown)
  --engines=...       비교할 engine 리스트 (default "pcap tpacket moal")
  --out=FILE          저장 경로 (default stdout)
  -h, --help          도움말

내부적으로 /usr/local/tools/wbridge-bench/wbridge_report.sh 를 호출합니다.
EOF
}

BASE_DIR=/var/log/wbridge-bench/serial
LABEL=unknown
ENGINES="pcap tpacket moal"
OUT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --base-dir=*) BASE_DIR="${1#*=}" ;;
        --label=*)    LABEL="${1#*=}" ;;
        --engines=*)  ENGINES="${1#*=}" ;;
        --out=*)      OUT="${1#*=}" ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "[ERR] unknown arg: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

REPORT="/usr/local/tools/wbridge-bench/wbridge_report.sh"
[ -x "$REPORT" ] || { echo "[ERR] wbridge_report.sh 부재: $REPORT" >&2; exit 1; }

BASELINE_DIR="${BASE_DIR}/baseline"
[ -d "$BASELINE_DIR" ] || {
    echo "[ERR] 결과 디렉토리 부재: $BASELINE_DIR" >&2
    echo "      먼저 board_capture.sh 로 측정하세요." >&2
    exit 1
}

ARGS=(--baseline-dir="$BASELINE_DIR" --engines="$ENGINES" --label="wbridge-serial (${LABEL})")
[ -n "$OUT" ] && ARGS+=(--out="$OUT")

exec "$REPORT" "${ARGS[@]}"
