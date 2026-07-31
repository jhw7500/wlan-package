#!/bin/bash
set -euo pipefail

tag=$(basename "$0")
STATE_FILE="/run/wbridge.thermal.state"
ENV_FILE="/run/wbridge.thermal.env"
RESTART_TS_FILE="/run/wbridge.thermal.restart.ts"
CONF_JSON="/usr/local/etc/wifi_init_conf.json"

to_int() {
    local v="${1:-}"
    if [[ "$v" =~ ^-?[0-9]+$ ]]; then
        echo "$v"
    else
        echo 0
    fi
}

read_cpu_temp() {
    local raw
    raw=$(to_int "$(cat /sys/devices/virtual/thermal/thermal_zone0/temp 2>/dev/null || echo 0)")
    echo $((raw / 1000))
}

read_wifi_temp() {
    local ifname="$1"
    if ! command -v mlanutl >/dev/null 2>&1; then
        echo 0
        return
    fi
    to_int "$(mlanutl "$ifname" get_sensor_temp 2>/dev/null | awk '{print int($4)}' || true)"
}

state_from_enter_thresholds() {
    if (( CPU_TEMP >= HOT_CPU_ENTER || WIFI_MAX >= HOT_WIFI_ENTER )); then
        echo hot
    elif (( CPU_TEMP >= WARM_CPU_ENTER || WIFI_MAX >= WARM_WIFI_ENTER )); then
        echo warm
    else
        echo ok
    fi
}

# wifi_init_conf.json에서 wbridge thermal 기본값 로드
# 우선순위: wifi_init_conf.json (SSoT) > /etc/default/wbridge (fallback) > 스크립트 기본값
# JSON이 정상 파싱되면 JSON 값을 최종값으로 사용한다. JSON이 없거나 파싱 실패 시
# systemd EnvironmentFile로 이미 로드된 /etc/default/wbridge 값이 그대로 폴백으로 유지된다.
_load_wbridge_thermal_json_defaults() {
    command -v jq >/dev/null 2>&1 && [ -f "$CONF_JSON" ] || return 0
    # JSON 파싱 검증 — 실패 시 env 파일 값을 fallback으로 유지
    if ! jq -e . "$CONF_JSON" >/dev/null 2>&1; then
        logger -p local0.warn "[$tag:$LINENO] JSON parse failed ($CONF_JSON), falling back to /etc/default/wbridge values"
        return 0
    fi
    local key val
    while IFS='=' read -r key val; do
        [ -n "$key" ] || continue
        # JSON이 SSoT — env 파일 값을 덮어씀
        export "$key=$val"
    done < <(jq -r '
        .wbridge |
        "WBRIDGE_MODE_FORCE=\(if .thermal.mode_force then 1 else 0 end)",
        "WBRIDGE_THERMAL_AUTO_RESTART=\(if .thermal.auto_restart then 1 else 0 end)",
        "WBRIDGE_THERMAL_RESTART_COOLDOWN_SEC=\(.thermal.restart_cooldown_sec // 60)",
        "WBRIDGE_THERMAL_BRIDGE_UNITS=\(.thermal.bridge_units // "wifi_bridge@mlan0.service wifi_bridge@mlan1.service")",
        "WBRIDGE_THERMAL_WARM_CPU_ENTER=\(.thermal.thresholds.warm_cpu_enter // 80)",
        "WBRIDGE_THERMAL_HOT_CPU_ENTER=\(.thermal.thresholds.hot_cpu_enter // 90)",
        "WBRIDGE_THERMAL_WARM_CPU_EXIT=\(.thermal.thresholds.warm_cpu_exit // 75)",
        "WBRIDGE_THERMAL_HOT_CPU_EXIT=\(.thermal.thresholds.hot_cpu_exit // 85)",
        "WBRIDGE_THERMAL_WARM_WIFI_ENTER=\(.thermal.thresholds.warm_wifi_enter // 70)",
        "WBRIDGE_THERMAL_HOT_WIFI_ENTER=\(.thermal.thresholds.hot_wifi_enter // 80)",
        "WBRIDGE_THERMAL_WARM_WIFI_EXIT=\(.thermal.thresholds.warm_wifi_exit // 65)",
        "WBRIDGE_THERMAL_HOT_WIFI_EXIT=\(.thermal.thresholds.hot_wifi_exit // 75)"
    ' "$CONF_JSON" 2>/dev/null)
}
_load_wbridge_thermal_json_defaults

MODE_FORCE="${WBRIDGE_MODE_FORCE:-0}"
AUTO_RESTART="${WBRIDGE_THERMAL_AUTO_RESTART:-0}"
RESTART_COOLDOWN_SEC=$(to_int "${WBRIDGE_THERMAL_RESTART_COOLDOWN_SEC:-60}")
if (( RESTART_COOLDOWN_SEC < 0 )); then
    RESTART_COOLDOWN_SEC=60
fi

RESTART_UNITS="${WBRIDGE_THERMAL_BRIDGE_UNITS:-wifi_bridge@mlan0.service wifi_bridge@mlan1.service}"
DEFAULT_STATE="${WBRIDGE_THERMAL_STATE:-ok}"

WARM_CPU_ENTER=$(to_int "${WBRIDGE_THERMAL_WARM_CPU_ENTER:-80}")
HOT_CPU_ENTER=$(to_int "${WBRIDGE_THERMAL_HOT_CPU_ENTER:-90}")
WARM_CPU_EXIT=$(to_int "${WBRIDGE_THERMAL_WARM_CPU_EXIT:-75}")
HOT_CPU_EXIT=$(to_int "${WBRIDGE_THERMAL_HOT_CPU_EXIT:-85}")

WARM_WIFI_ENTER=$(to_int "${WBRIDGE_THERMAL_WARM_WIFI_ENTER:-70}")
HOT_WIFI_ENTER=$(to_int "${WBRIDGE_THERMAL_HOT_WIFI_ENTER:-80}")
WARM_WIFI_EXIT=$(to_int "${WBRIDGE_THERMAL_WARM_WIFI_EXIT:-65}")
HOT_WIFI_EXIT=$(to_int "${WBRIDGE_THERMAL_HOT_WIFI_EXIT:-75}")

CPU_TEMP=$(read_cpu_temp)
MLAN0_TEMP=$(read_wifi_temp mlan0)
MLAN1_TEMP=$(read_wifi_temp mlan1)
if (( MLAN0_TEMP > MLAN1_TEMP )); then
    WIFI_MAX=$MLAN0_TEMP
else
    WIFI_MAX=$MLAN1_TEMP
fi

PREV_STATE="$DEFAULT_STATE"
if [ -s "$STATE_FILE" ]; then
    PREV_STATE=$(tr -d '[:space:]' < "$STATE_FILE")
fi

case "$PREV_STATE" in
    ok|warm|hot) ;;
    *) PREV_STATE="$DEFAULT_STATE" ;;
esac

case "$PREV_STATE" in
    hot)
        if (( CPU_TEMP >= HOT_CPU_EXIT || WIFI_MAX >= HOT_WIFI_EXIT )); then
            NEW_STATE="hot"
        elif (( CPU_TEMP >= WARM_CPU_ENTER || WIFI_MAX >= WARM_WIFI_ENTER )); then
            NEW_STATE="warm"
        else
            NEW_STATE="ok"
        fi
        ;;
    warm)
        if (( CPU_TEMP >= HOT_CPU_ENTER || WIFI_MAX >= HOT_WIFI_ENTER )); then
            NEW_STATE="hot"
        elif (( CPU_TEMP >= WARM_CPU_EXIT || WIFI_MAX >= WARM_WIFI_EXIT )); then
            NEW_STATE="warm"
        else
            NEW_STATE="ok"
        fi
        ;;
    *)
        NEW_STATE=$(state_from_enter_thresholds)
        ;;
esac

TMP_ENV="${ENV_FILE}.tmp"
cat > "$TMP_ENV" <<EOF
WBRIDGE_THERMAL_STATE=$NEW_STATE
WBRIDGE_THERMAL_CPU_TEMP=$CPU_TEMP
WBRIDGE_THERMAL_MLAN0_TEMP=$MLAN0_TEMP
WBRIDGE_THERMAL_MLAN1_TEMP=$MLAN1_TEMP
WBRIDGE_THERMAL_UPDATED_AT=$(date +%s)
EOF
mv "$TMP_ENV" "$ENV_FILE"
printf '%s\n' "$NEW_STATE" > "$STATE_FILE"

if [ "$NEW_STATE" != "$PREV_STATE" ]; then
    logger -p local0.notice "[$tag:$LINENO] thermal state changed: ${PREV_STATE} -> ${NEW_STATE} (cpu=${CPU_TEMP}, mlan0=${MLAN0_TEMP}, mlan1=${MLAN1_TEMP})"

    if [ "$MODE_FORCE" = "1" ]; then
        logger -p local0.warn "[$tag:$LINENO] mode force enabled, skip bridge restart despite thermal state change"
        exit 0
    fi

    if [ "$AUTO_RESTART" != "1" ]; then
        logger -p local0.info "[$tag:$LINENO] thermal auto restart disabled (WBRIDGE_THERMAL_AUTO_RESTART=$AUTO_RESTART)"
        exit 0
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        logger -p local0.warn "[$tag:$LINENO] systemctl not found, cannot restart wifi_bridge services"
        exit 0
    fi

    NOW_TS=$(date +%s)
    LAST_TS=$(to_int "$(cat "$RESTART_TS_FILE" 2>/dev/null || echo 0)")
    if (( NOW_TS - LAST_TS < RESTART_COOLDOWN_SEC )); then
        REMAIN_SEC=$((RESTART_COOLDOWN_SEC - (NOW_TS - LAST_TS)))
        logger -p local0.info "[$tag:$LINENO] restart cooldown active (${REMAIN_SEC}s left), skip restart"
        exit 0
    fi

    if [ -n "$RESTART_UNITS" ]; then
        read -r -a units <<< "$RESTART_UNITS"
        if systemctl --no-block try-restart "${units[@]}" >/dev/null 2>&1; then
            logger -p local0.notice "[$tag:$LINENO] restarted bridge units due to thermal state change: ${RESTART_UNITS}"
            printf '%s\n' "$NOW_TS" > "$RESTART_TS_FILE"
        else
            logger -p local0.warn "[$tag:$LINENO] failed to restart bridge units: ${RESTART_UNITS}"
        fi
    fi
fi
