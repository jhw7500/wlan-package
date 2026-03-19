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

# JSON에서 주기 설정 로드 (CLI 인자 우선)
if [ -z "$INTERVAL" ] && [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    INTERVAL=$(jq -r ".${IFACE}.periodic_roam.interval // 300" "$WIFI_INIT_CONF_JSON" 2>/dev/null)
fi
INTERVAL="${INTERVAL:-300}"

# 최소 주기 30초
if [ "$INTERVAL" -lt 10 ] 2>/dev/null; then
    logger -p local0.warn "$tag interval ${INTERVAL}s too short, using 10s"
    INTERVAL=10
fi

logger -p local0.info "$tag Starting periodic roam, interval=${INTERVAL}s"

cleanup() {
    logger -p local0.info "$tag Stopping periodic roam"
    exit 0
}
trap cleanup SIGTERM SIGINT

# 초기 대기: wpa_supplicant 연결 안정화
sleep 15

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

    # passive_roam.py로 roam 0 실행 (현재 AP 제외 최고 RSSI로 로밍)
    logger -p local0.info "$tag Executing periodic roam"
    output=$(python3 "$PASSIVE_ROAM" 0 --iface "$IFACE" 2>&1) || true
    logger -p local0.info "$tag Roam result: $(echo "$output" | tail -3 | tr '\n' ' ')"

    sleep "$INTERVAL"
done
