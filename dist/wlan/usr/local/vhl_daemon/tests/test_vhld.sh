#!/bin/bash
# test_vhld.sh - VHL Protocol Daemon integration test
# Requirements: socat, xxd, vhld must be running

set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-50000}"
PASS=0
FAIL=0

log_ok()   { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
log_fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

make_packet() {
    local type="$1"
    local req_id="$2"
    local seq="$3"
    local payload_hex="${4:-}"
    local payload_len

    if [ -z "$payload_hex" ]; then
        payload_len="0000"
    else
        local byte_count=$(( ${#payload_hex} / 2 ))
        payload_len=$(printf "%04x" "$byte_count")
    fi

    echo "01${type}${req_id}${seq}${payload_len}${payload_hex}"
}

send_recv() {
    local hex="$1"
    echo -n "$hex" | xxd -r -p | \
        socat -T1 - UDP:${HOST}:${PORT} 2>/dev/null | xxd -p | tr -d '\n'
}

echo "=== VHL Protocol Daemon Test ==="
echo "Target: ${HOST}:${PORT}"
echo ""

# Test 1: Get Device Info
echo "[Test 1] Get Device Info (0x0001)"
pkt=$(make_packet "01" "0001" "0001" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:0:2}" = "01" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "응답 수신됨"
else
    log_fail "응답 없음 또는 잘못된 형식"
fi

# Test 2: Get Device Status
echo "[Test 2] Get Device Status (0x0002)"
pkt=$(make_packet "01" "0002" "0002" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "상태 응답 수신"
else
    log_fail "응답 없음"
fi

# Test 3: Get WLAN Config
echo "[Test 3] Get WLAN Config (0x0003)"
pkt=$(make_packet "01" "0003" "0003" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ] && [ "${resp:2:2}" = "02" ]; then
    log_ok "무선 설정 응답 수신"
else
    log_fail "응답 없음"
fi

# Test 4: Sequence echo
echo "[Test 4] Sequence Number Echo"
pkt=$(make_packet "01" "0001" "ABCD" "")
resp=$(send_recv "$pkt")
if [ -n "$resp" ]; then
    echo_seq="${resp:8:4}"
    if [ "$echo_seq" = "abcd" ]; then
        log_ok "시퀀스 번호 정확히 반환 (0xABCD)"
    else
        log_fail "시퀀스 번호 불일치: expected abcd, got $echo_seq"
    fi
else
    log_fail "응답 없음"
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
exit $FAIL
