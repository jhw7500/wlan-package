#!/bin/bash
# wifi_periodic_roam.sh - 주기적으로 passive roam (roam 0)을 실행하는 서비스 스크립트
# Usage: wifi_periodic_roam.sh <iface> [interval_sec]
#
# wifi_init_conf.json의 roaming 설정을 참조하거나 CLI 인자로 주기를 지정
# roam 0 = 현재 AP 제외 최고 RSSI AP로 로밍 시도

set -uo pipefail

IFACE="${1:-mlan0}"
INTERVAL="${2:-}"
PASSIVE_ROAM="/usr/local/logger/passive_roam.py"
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"

tag="[$(basename "$0")] [${IFACE}]"

# JSON에서 설정 로드
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    [ -z "$INTERVAL" ] && INTERVAL=$(jq -r ".${IFACE}.periodic_roam.interval // 300" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
    SCAN_BEFORE_ROAM=$(jq -r ".${IFACE}.periodic_roam.scan_before_roam // false" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
else
    SCAN_BEFORE_ROAM="false"
fi
INTERVAL="${INTERVAL:-300}"

# 최소 주기 10초
if [ "$INTERVAL" -lt 10 ] 2>/dev/null; then
    logger -p local0.warn "$tag interval ${INTERVAL}s too short, using 10s"
    INTERVAL=10
fi

logger -p local0.info "$tag Starting periodic roam, interval=${INTERVAL}s, scan_before_roam=${SCAN_BEFORE_ROAM}"

cleanup() {
    logger -p local0.info "$tag Stopping periodic roam"
    exit 0
}
trap cleanup SIGTERM SIGINT

# 초기 대기: wpa_supplicant 연결 안정화
sleep 15

# 현재 연결된 SSID의 주파수 목록 취득
get_freq_list() {
    local ssid
    ssid=$(wpa_cli -i "$IFACE" status 2>/dev/null | grep '^ssid=' | cut -d= -f2)
    if [ -n "$ssid" ]; then
        iw "$IFACE" scan dump 2>/dev/null | grep -B5 "SSID: $ssid" | grep 'freq:' | awk '{print $2}' | sort -u | tr '\n' ' '
    fi
}

while true; do
    # 인터페이스 존재 확인
    if [ ! -d "/sys/class/net/$IFACE" ]; then
        logger -p local0.warn "$tag Interface $IFACE not found, waiting..."
        sleep "$INTERVAL"
        continue
    fi

    # wpa_supplicant 동작 확인
    if ! wpa_cli -i "$IFACE" status >/dev/null 2>&1; then
        logger -p local0.warn "$tag wpa_supplicant not running on $IFACE, waiting..."
        sleep "$INTERVAL"
        continue
    fi

    # 연결 상태 확인 (COMPLETED 상태일 때만 로밍)
    wpa_state=$(wpa_cli -i "$IFACE" status 2>/dev/null | grep '^wpa_state=' | cut -d= -f2)
    if [ "$wpa_state" != "COMPLETED" ]; then
        logger -p local0.info "$tag Not connected (state=$wpa_state), skip roam"
        sleep "$INTERVAL"
        continue
    fi

    # scan_before_roam: 로밍 전 스캔 수행
    if [ "$SCAN_BEFORE_ROAM" = "true" ]; then
        freq_list=$(get_freq_list)
        if [ -n "$freq_list" ]; then
            ssid=$(wpa_cli -i "$IFACE" status 2>/dev/null | grep '^ssid=' | cut -d= -f2)
            iw "$IFACE" scan freq $freq_list ssid "$ssid" >/dev/null 2>&1
            sleep 1
        fi
    fi

    # passive_roam.py로 roam 0 실행 (현재 AP 제외 최고 RSSI로 로밍)
    output=$(python3 "$PASSIVE_ROAM" 0 --iface "$IFACE" 2>&1) || true

    # 결과에서 의미 있는 내용만 로깅
    if echo "$output" | grep -q "No other APs"; then
        logger -p local0.debug "$tag No other APs available, skip"
    elif echo "$output" | grep -q "No scan block"; then
        logger -p local0.debug "$tag No scan data available, skip"
    else
        # 로밍 실행된 경우만 info로 로깅
        result=$(echo "$output" | grep "ROAM_RESULT:" | head -1)
        logger -p local0.info "$tag ${result:-Roam: no result}"
    fi

    sleep "$INTERVAL"
done
