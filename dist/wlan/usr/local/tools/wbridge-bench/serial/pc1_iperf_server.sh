#!/bin/bash
# pc1_iperf_server.sh — PC1 (wired client) 시리얼에서 iperf3 server 띄움
# Plan: wbridge-serial.plan.md FR-S1
# Self-contained: PC1 에 단독 복사해서 실행.
#
# Usage:
#   pc1_iperf_server.sh [PORT]
#       PORT 생략 시 5201 (default)
#
# Ctrl+C 로 종료.
set -euo pipefail

PORT="${1:-5201}"

if ! command -v iperf3 >/dev/null 2>&1; then
    echo "[ERROR] iperf3 가 설치되어 있지 않습니다." >&2
    echo "        sudo apt install -y iperf3" >&2
    exit 1
fi

echo "=== PC1 iperf3 server ==="
echo "host:   $(hostname)"
echo "date:   $(date -Iseconds)"
echo "ip:     $(hostname -I 2>/dev/null | awk '{print $1}')"
echo "port:   $PORT"
echo "보드/PC2 helper 가 시작될 때까지 그대로 두세요. Ctrl+C 로 종료."
echo

exec iperf3 -s -p "$PORT"
