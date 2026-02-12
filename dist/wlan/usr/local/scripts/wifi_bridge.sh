#!/bin/bash
set -euo pipefail

tag=$(basename "$0")
IFACE=$1
# TODO: Read wired interface from config.json instead of hardcoding eth0
WIRED_IF="eth0"

OPT_DIR="/usr/local/wlan-bridge/scripts"

# 최적화 활성화 여부 (1: 활성화, 0: 비활성화)
# 환경 변수 WBRIDGE_OPTIMIZE가 설정되어 있으면 그 값을 따름
USE_OPTIMIZATION=${WBRIDGE_OPTIMIZE:-0}

logger -p local0.info "[$tag:$LINENO] [$IFACE] wbridge startup sequence initiated (opt: $USE_OPTIMIZATION)"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

both_up() {
    ip link show "$WIRED_IF" | grep -q "state UP" || return 1
    ip link show "$IFACE" | grep -q "state UP" || return 1
    cat /sys/class/net/"$WIRED_IF"/carrier 2>/dev/null | grep -q 1 || return 1
    cat /sys/class/net/"$IFACE"/carrier 2>/dev/null | grep -q 1 || return 1
}

# 인터페이스가 준비될 때까지 대기
logger -p local0.info "[$tag:$LINENO] [$IFACE] Waiting for interfaces to be ready..."
for _ in $(seq 1 200); do
    if both_up; then break; fi
    sleep 0.2
done

# --- [ 시스템 최적화 단계 ] ---
if [ "$USE_OPTIMIZATION" -eq 1 ]; then
    # 1. UDP/네트워크 스택 최적화
    if [ -x "$OPT_DIR/optimize-for-udp.sh" ]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Running UDP optimization..."
        "$OPT_DIR/optimize-for-udp.sh" "$WIRED_IF" "$IFACE" > /dev/null 2>&1 || logger -p local0.warn "Optimization script returned error"
    fi

    # 2. IRQ Affinity 최적화
    if [ -x "$OPT_DIR/setup-irq-affinity.sh" ]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Setting up IRQ affinity..."
        "$OPT_DIR/setup-irq-affinity.sh" "$WIRED_IF" "$IFACE" > /dev/null 2>&1 || logger -p local0.warn "IRQ affinity script returned error"
    fi
else
    logger -p local0.info "[$tag:$LINENO] [$IFACE] System optimization skipped by user request"
fi

# --- [ 브리지 실행 ] ---

logger -p local0.info "[$tag:$LINENO] [$IFACE] Starting wbridge binary..."

# Use the new wbridge binary via the wifi-wbridge symlink
# --ip-filter: Skip re-injection for bridge's local IPs
# --no-debug: Reduce log noise in production
exec /usr/local/bin/wifi-wbridge --ip-filter --no-debug "$WIRED_IF" "$IFACE"
