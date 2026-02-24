#!/bin/bash
set -euo pipefail

tag=$(basename "$0")
IFACE=$1
# TODO: Read wired interface from config.json instead of hardcoding eth0
WIRED_IF="eth0"

OPT_DIR="/usr/local/wlan-bridge/scripts"

if [[ "$IFACE" != "mlan0" ]]; then
    exit 0
fi
# 최적화 활성화 여부 (1: 활성화, 0: 비활성화)
# 환경 변수 WBRIDGE_OPTIMIZE가 설정되어 있으면 그 값을 따름
USE_OPTIMIZATION=${WBRIDGE_OPTIMIZE:-0}
WBRIDGE_ENGINE=${WBRIDGE_ENGINE:-pcap}

REQUESTED_MODE=${WBRIDGE_MODE:-normal}
THERMAL_STATE=${WBRIDGE_THERMAL_STATE:-ok}
PROFILE_VERSION=${WBRIDGE_PROFILE_VERSION:-1}
MODE_FORCE=${WBRIDGE_MODE_FORCE:-0}
LINK_GUARD=${WBRIDGE_LINK_GUARD:-1}
LINK_DOWN_DEBOUNCE_SEC=${WBRIDGE_LINK_DOWN_DEBOUNCE_SEC:-3}
LINK_UP_STABLE_SEC=${WBRIDGE_LINK_UP_STABLE_SEC:-8}
LINK_IDLE_POLL_SEC=${WBRIDGE_LINK_IDLE_POLL_SEC:-10}
WAIT_READY_TIMEOUT_SEC=${WBRIDGE_WAIT_READY_TIMEOUT_SEC:-40}

sanitize_positive_int() {
    local value=${1:-}
    local fallback=${2:-1}
    if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -gt 0 ]; then
        echo "$value"
        return
    fi
    echo "$fallback"
}

case "$USE_OPTIMIZATION" in
    0|1) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_OPTIMIZE='$USE_OPTIMIZATION', fallback to 0"
        USE_OPTIMIZATION=0
        ;;
esac

case "$REQUESTED_MODE" in
    latency|normal|thermal) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_MODE='$REQUESTED_MODE', fallback to normal"
        REQUESTED_MODE="normal"
        ;;
esac

case "$THERMAL_STATE" in
    ok|warm|hot) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_THERMAL_STATE='$THERMAL_STATE', fallback to ok"
        THERMAL_STATE="ok"
        ;;
esac

case "$MODE_FORCE" in
    0|1) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_MODE_FORCE='$MODE_FORCE', fallback to 0"
        MODE_FORCE=0
        ;;
esac

case "$LINK_GUARD" in
    0|1) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_LINK_GUARD='$LINK_GUARD', fallback to 1"
        LINK_GUARD=1
        ;;
esac

LINK_DOWN_DEBOUNCE_SEC=$(sanitize_positive_int "$LINK_DOWN_DEBOUNCE_SEC" 3)
LINK_UP_STABLE_SEC=$(sanitize_positive_int "$LINK_UP_STABLE_SEC" 8)
LINK_IDLE_POLL_SEC=$(sanitize_positive_int "$LINK_IDLE_POLL_SEC" 10)
WAIT_READY_TIMEOUT_SEC=$(sanitize_positive_int "$WAIT_READY_TIMEOUT_SEC" 40)

case "$WBRIDGE_ENGINE" in
    pcap|tpacket) ;;
    *)
        logger -p local0.warn "[$tag:$LINENO] [$IFACE] Invalid WBRIDGE_ENGINE='$WBRIDGE_ENGINE', fallback to pcap"
        WBRIDGE_ENGINE="pcap"
        ;;
esac

EFFECTIVE_MODE="$REQUESTED_MODE"
if [ "$MODE_FORCE" -ne 1 ]; then
    if [ "$THERMAL_STATE" = "hot" ]; then
        EFFECTIVE_MODE="thermal"
    elif [ "$THERMAL_STATE" = "warm" ] && [ "$REQUESTED_MODE" = "latency" ]; then
        EFFECTIVE_MODE="normal"
    fi
fi

WBRIDGE_MODE_REQUESTED="$REQUESTED_MODE"
WBRIDGE_PROFILE_EFFECTIVE="$EFFECTIVE_MODE"
WBRIDGE_THERMAL_STATE="$THERMAL_STATE"
WBRIDGE_PROFILE_VERSION="$PROFILE_VERSION"
WBRIDGE_MODE_FORCE="$MODE_FORCE"
WBRIDGE_LINK_GUARD="$LINK_GUARD"

if [ "$MODE_FORCE" -eq 1 ]; then
    logger -p local0.warn "[$tag:$LINENO] [$IFACE] WBRIDGE_MODE_FORCE=1 bypasses thermal clamp (requested=$REQUESTED_MODE, thermal=$THERMAL_STATE, effective=$EFFECTIVE_MODE)"
fi

logger -p local0.info "[$tag:$LINENO] [$IFACE] wbridge startup sequence initiated (opt=$USE_OPTIMIZATION, requested=$REQUESTED_MODE, effective=$EFFECTIVE_MODE, thermal=$THERMAL_STATE, force=$MODE_FORCE, profile_ver=$PROFILE_VERSION, link_guard=$LINK_GUARD, down_debounce=${LINK_DOWN_DEBOUNCE_SEC}s, up_stable=${LINK_UP_STABLE_SEC}s)"

UDP_OPT_RESULT="not_requested"
IRQ_OPT_RESULT="not_requested"

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

wait_for_link_ready() {
    local down_since=0
    local up_since=0
    local next_log_ts=0
    local now down_elapsed up_elapsed

    while true; do
        now=$(date +%s)

        if both_up; then
            down_since=0
            if [ "$up_since" -eq 0 ]; then
                up_since=$now
            fi
            up_elapsed=$((now - up_since))
            if [ "$up_elapsed" -ge "$LINK_UP_STABLE_SEC" ]; then
                return 0
            fi
            sleep 1
            continue
        fi

        up_since=0
        if [ "$down_since" -eq 0 ]; then
            down_since=$now
            next_log_ts=$now
        fi
        down_elapsed=$((now - down_since))

        if [ "$LINK_GUARD" -eq 0 ] && [ "$down_elapsed" -ge "$WAIT_READY_TIMEOUT_SEC" ]; then
            return 1
        fi

        if [ "$down_elapsed" -ge "$LINK_DOWN_DEBOUNCE_SEC" ]; then
            if [ "$now" -ge "$next_log_ts" ]; then
                logger -p local0.info "[$tag:$LINENO] [$IFACE] Link incomplete for ${down_elapsed}s, waiting (${LINK_IDLE_POLL_SEC}s poll)"
                next_log_ts=$((now + LINK_IDLE_POLL_SEC))
            fi
            sleep "$LINK_IDLE_POLL_SEC"
        else
            sleep 1
        fi
    done
}

# 인터페이스가 준비될 때까지 대기
logger -p local0.info "[$tag:$LINENO] [$IFACE] Waiting for interfaces to be ready..."
if wait_for_link_ready; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Links are stable for ${LINK_UP_STABLE_SEC}s"
else
    logger -p local0.warn "[$tag:$LINENO] [$IFACE] Link ready timeout (${WAIT_READY_TIMEOUT_SEC}s), continuing without guard"
fi

# --- [ 시스템 최적화 단계 ] ---
if [ "$USE_OPTIMIZATION" -eq 1 ]; then
    if [ "$EFFECTIVE_MODE" = "thermal" ] && [ "$MODE_FORCE" -ne 1 ]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Skip UDP optimization due to thermal effective profile"
        UDP_OPT_RESULT="skipped_thermal"
    elif [ -x "$OPT_DIR/optimize-for-udp.sh" ]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Running UDP optimization..."
        if "$OPT_DIR/optimize-for-udp.sh" "$WIRED_IF" "$IFACE" > /dev/null 2>&1; then
            UDP_OPT_RESULT="applied"
        else
            UDP_OPT_RESULT="failed"
            logger -p local0.warn "Optimization script returned error"
        fi
    else
        UDP_OPT_RESULT="script_missing"
    fi

    # 2. IRQ Affinity 최적화 (모드별)
    if [ -x "$OPT_DIR/setup-irq-affinity.sh" ]; then
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Setting up IRQ affinity (effective mode: $EFFECTIVE_MODE)..."
        if "$OPT_DIR/setup-irq-affinity.sh" --mode "$EFFECTIVE_MODE" "$WIRED_IF" "$IFACE" > /dev/null 2>&1; then
            IRQ_OPT_RESULT="applied"
        else
            IRQ_OPT_RESULT="failed"
            logger -p local0.warn "IRQ affinity script returned error"
        fi
    else
        IRQ_OPT_RESULT="script_missing"
    fi
else
    logger -p local0.info "[$tag:$LINENO] [$IFACE] System optimization skipped by user request"
    UDP_OPT_RESULT="skipped_by_user"
    IRQ_OPT_RESULT="skipped_by_user"
fi

# --- [ 브리지 실행 ] ---

# setup-irq-affinity.sh가 생성한 환경변수 파일 로드
# 모드별(latency/normal/thermal) wbridge 설정이 포함됨
WBRIDGE_ENV="/run/wbridge.env"
if [ "$USE_OPTIMIZATION" -eq 1 ] && [ "$IRQ_OPT_RESULT" = "applied" ] && [ -f "$WBRIDGE_ENV" ]; then
    # shellcheck source=/dev/null
    . "$WBRIDGE_ENV"
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Loaded wbridge env from $WBRIDGE_ENV (req=${WBRIDGE_MODE_REQUESTED:-$REQUESTED_MODE}, eff=${WBRIDGE_PROFILE_EFFECTIVE:-$EFFECTIVE_MODE}, thermal=${WBRIDGE_THERMAL_STATE:-$THERMAL_STATE}, force=${WBRIDGE_MODE_FORCE:-$MODE_FORCE}, budget=$WBRIDGE_DISPATCH_BUDGET, immediate=$WBRIDGE_IMMEDIATE, timeout=$WBRIDGE_TIMEOUT_MS, rt=$WBRIDGE_RT_PRIORITY)"
elif [ -f "$WBRIDGE_ENV" ]; then
    if [ "$USE_OPTIMIZATION" -ne 1 ]; then
        rm -f "$WBRIDGE_ENV" 2>/dev/null || true
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Ignored and cleared stale $WBRIDGE_ENV because optimization is disabled"
    else
        logger -p local0.info "[$tag:$LINENO] [$IFACE] Ignored $WBRIDGE_ENV because setup-irq-affinity was not applied in this run (result=$IRQ_OPT_RESULT)"
    fi
else
    logger -p local0.info "[$tag:$LINENO] [$IFACE] No $WBRIDGE_ENV found, using wbridge defaults"
fi

WBRIDGE_MODE_REQUESTED=${WBRIDGE_MODE_REQUESTED:-$REQUESTED_MODE}
WBRIDGE_PROFILE_EFFECTIVE=${WBRIDGE_PROFILE_EFFECTIVE:-$EFFECTIVE_MODE}
WBRIDGE_THERMAL_STATE=${WBRIDGE_THERMAL_STATE:-$THERMAL_STATE}
WBRIDGE_PROFILE_VERSION=${WBRIDGE_PROFILE_VERSION:-$PROFILE_VERSION}
WBRIDGE_MODE_FORCE=${WBRIDGE_MODE_FORCE:-$MODE_FORCE}

export WBRIDGE_PROFILE_VERSION WBRIDGE_MODE_REQUESTED WBRIDGE_PROFILE_EFFECTIVE WBRIDGE_THERMAL_STATE WBRIDGE_MODE_FORCE WBRIDGE_LINK_GUARD 2>/dev/null || true
export WBRIDGE_DISPATCH_BUDGET WBRIDGE_IMMEDIATE WBRIDGE_TIMEOUT_MS WBRIDGE_RT_PRIORITY WBRIDGE_PCAP_BUFFER WBRIDGE_TPACKET_RETIRE_TOV 2>/dev/null || true

EFFECTIVE_SNAPSHOT="/run/wbridge.effective.json"
cat > "$EFFECTIVE_SNAPSHOT" <<EOF
{
  "profile_version": "${WBRIDGE_PROFILE_VERSION}",
  "mode_requested": "${WBRIDGE_MODE_REQUESTED}",
  "thermal_state": "${WBRIDGE_THERMAL_STATE}",
  "mode_force": "${WBRIDGE_MODE_FORCE}",
  "link_guard": "${LINK_GUARD}",
  "link_down_debounce_sec": "${LINK_DOWN_DEBOUNCE_SEC}",
  "link_up_stable_sec": "${LINK_UP_STABLE_SEC}",
  "link_idle_poll_sec": "${LINK_IDLE_POLL_SEC}",
  "wait_ready_timeout_sec": "${WAIT_READY_TIMEOUT_SEC}",
  "profile_effective": "${WBRIDGE_PROFILE_EFFECTIVE}",
  "dispatch_budget": "${WBRIDGE_DISPATCH_BUDGET:-}",
  "immediate": "${WBRIDGE_IMMEDIATE:-}",
  "timeout_ms": "${WBRIDGE_TIMEOUT_MS:-}",
  "rt_priority": "${WBRIDGE_RT_PRIORITY:-}",
  "pcap_buffer": "${WBRIDGE_PCAP_BUFFER:-}",
  "tpacket_retire_tov": "${WBRIDGE_TPACKET_RETIRE_TOV:-}"
}
EOF
logger -p local0.info "[$tag:$LINENO] [$IFACE] Wrote effective profile snapshot to $EFFECTIVE_SNAPSHOT"

APPLY_SNAPSHOT="/run/wbridge.apply.json"
cat > "$APPLY_SNAPSHOT" <<EOF
{
  "engine": "${WBRIDGE_ENGINE}",
  "optimize_enabled": "${USE_OPTIMIZATION}",
  "udp_optimization": "${UDP_OPT_RESULT}",
  "irq_optimization": "${IRQ_OPT_RESULT}",
  "mode_requested": "${WBRIDGE_MODE_REQUESTED}",
  "mode_effective": "${WBRIDGE_PROFILE_EFFECTIVE}",
  "thermal_state": "${WBRIDGE_THERMAL_STATE}",
  "mode_force": "${WBRIDGE_MODE_FORCE}",
  "link_guard": "${LINK_GUARD}",
  "updated_at": "$(date +%s)"
}
EOF
logger -p local0.info "[$tag:$LINENO] [$IFACE] Wrote optimization apply snapshot to $APPLY_SNAPSHOT"

if [ "$WBRIDGE_ENGINE" = "tpacket" ]; then
    logger -p local0.info "[$tag:$LINENO] [$IFACE] Starting wbridge-tpacket binary..."
    exec /usr/local/bin/wifi-wbridge-tpacket "$WIRED_IF" "$IFACE"
fi

logger -p local0.info "[$tag:$LINENO] [$IFACE] Starting wbridge binary..."

# Use the new wbridge binary via the wifi-wbridge symlink
# --ip-filter: Skip re-injection for bridge's local IPs
# --no-debug: Reduce log noise in production
exec /usr/local/bin/wifi-wbridge --ip-filter --no-debug "$WIRED_IF" "$IFACE"
