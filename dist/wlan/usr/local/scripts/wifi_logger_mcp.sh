#!/bin/bash
set -u

tag=$(basename "$0")
DEV="/sys/bus/iio/devices/iio:device0"
FACILITY="local3"
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"

# Defaults
gain0="0.5203"
gain1="15.6552"
MCP_CHECK_INTERVAL=5
# ADC를 못 읽거나 전압이 5V/24V 어느 쪽에도 안 맞는 상태가 이 횟수만큼 이어지면 감시를 포기한다.
# 설정/하드웨어 문제는 재시도로 풀리지 않으므로, 무한 반복 로그로 콘솔을 덮는 대신 중단한다.
MCP_MAX_PROBE_FAIL=12

# 5V system thresholds
EMERG_A_5V=2.5; CRIT_A_5V=2.0; ERR_A_5V=1.5; WARN_A_5V=1.0
# 24V system thresholds
EMERG_A_24V=0.5; CRIT_A_24V=0.4; ERR_A_24V=0.3; WARN_A_24V=0.2

# Load from JSON config
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    DEV=$(jq -r '.mcp.iio_device // "/sys/bus/iio/devices/iio:device0"' "$WIFI_INIT_CONF_JSON")
    gain0=$(jq -r '.mcp.gain_current // 0.5203' "$WIFI_INIT_CONF_JSON")
    gain1=$(jq -r '.mcp.gain_voltage // 15.6552' "$WIFI_INIT_CONF_JSON")
    MCP_CHECK_INTERVAL=$(jq -r '.mcp.check_interval_sec // 5' "$WIFI_INIT_CONF_JSON")
    MCP_MAX_PROBE_FAIL=$(jq -r '.mcp.max_probe_fail // 12' "$WIFI_INIT_CONF_JSON")
    EMERG_A_5V=$(jq -r '.mcp.system_5v.emerg_a // 2.5' "$WIFI_INIT_CONF_JSON")
    CRIT_A_5V=$(jq -r '.mcp.system_5v.crit_a // 2.0' "$WIFI_INIT_CONF_JSON")
    ERR_A_5V=$(jq -r '.mcp.system_5v.error_a // 1.5' "$WIFI_INIT_CONF_JSON")
    WARN_A_5V=$(jq -r '.mcp.system_5v.warn_a // 1.0' "$WIFI_INIT_CONF_JSON")
    EMERG_A_24V=$(jq -r '.mcp.system_24v.emerg_a // 0.5' "$WIFI_INIT_CONF_JSON")
    CRIT_A_24V=$(jq -r '.mcp.system_24v.crit_a // 0.4' "$WIFI_INIT_CONF_JSON")
    ERR_A_24V=$(jq -r '.mcp.system_24v.error_a // 0.3' "$WIFI_INIT_CONF_JSON")
    WARN_A_24V=$(jq -r '.mcp.system_24v.warn_a // 0.2' "$WIFI_INIT_CONF_JSON")
fi

# 설정값 방어. 비숫자/0 이하를 그대로 흘려보내면 sleep이 실패해 busy loop가 되거나
# (check_interval_sec), 정수 비교가 에러나서 종료 조건이 영영 성립하지 않는다(max_probe_fail).
case "$MCP_CHECK_INTERVAL" in
    ''|*[!0-9.]*) MCP_CHECK_INTERVAL=5 ;;
esac
if [ "$(echo "$MCP_CHECK_INTERVAL > 0" | bc -l 2>/dev/null)" != "1" ]; then
    logger -p ${FACILITY}.warn "[$tag:$LINENO] invalid mcp.check_interval_sec; using 5"
    MCP_CHECK_INTERVAL=5
fi
case "$MCP_MAX_PROBE_FAIL" in
    ''|*[!0-9]*) MCP_MAX_PROBE_FAIL=12 ;;
esac
[ "$MCP_MAX_PROBE_FAIL" -ge 1 ] || MCP_MAX_PROBE_FAIL=12

cleanup() {
    #logger -p ${FACILITY}.info "[$tag:$LINENO] wifi_logger_mcp stop"
    exit 0
}
trap cleanup INT TERM

#logger -p ${FACILITY}.info "[$tag:$LINENO] wifi_logger_mcp start"

# ADC 두 채널을 읽어 전역 a(전류)/v(전압)에 채운다. sysfs 읽기나 bc 계산이 실패하면 1을 반환한다.
# 실패를 반환하지 않고 빈 값을 흘려보내면 printf가 0.000으로 찍어, 센서 부재를 "0V 이상전압"으로
# 오인하게 된다.
read_adc() {
    local raw0 scale0 raw1 scale1
    raw0=$(cat "$DEV/in_voltage0_raw" 2>/dev/null)     || return 1
    scale0=$(cat "$DEV/in_voltage0_scale" 2>/dev/null) || return 1
    raw1=$(cat "$DEV/in_voltage1_raw" 2>/dev/null)     || return 1
    scale1=$(cat "$DEV/in_voltage1_scale" 2>/dev/null) || return 1

    a=$(echo "$raw0 * $scale0 * $gain0" | bc -l 2>/dev/null)
    v=$(echo "$raw1 * $scale1 * $gain1" | bc -l 2>/dev/null)
    [ -n "$a" ] && [ -n "$v" ] || return 1
}

# 실패 반복 시 로그 간격을 늘린다 (상한 60s). MCP_CHECK_INTERVAL이 소수여도 되도록 bc 사용.
backoff_sleep() {
    local n="$1" sec
    sec=$(echo "$MCP_CHECK_INTERVAL * $n" | bc -l)
    if (( $(echo "$sec > 60" | bc -l) )); then sec=60; fi
    sleep "$sec"
}

# 전원 종류(5V/24V) 판별. 판별될 때까지는 전류 임계값을 정할 수 없다.
probe_fail=0
while true; do
    if read_adc; then
        if   (( $(echo "$v >= 4.0"  | bc -l) )) && (( $(echo "$v <= 6.0"  | bc -l) )); then
            EMERG_A=$EMERG_A_5V; CRIT_A=$CRIT_A_5V; ERR_A=$ERR_A_5V; WARN_A=$WARN_A_5V
            break
        elif (( $(echo "$v >= 20.0" | bc -l) )) && (( $(echo "$v <= 30.0" | bc -l) )); then
            EMERG_A=$EMERG_A_24V; CRIT_A=$CRIT_A_24V; ERR_A=$ERR_A_24V; WARN_A=$WARN_A_24V
            break
        fi
        # 읽기는 됐지만 어느 전원 규격에도 안 맞음 — 전원 이상이거나 gain 설정 오류.
        # emerg는 journald가 모든 콘솔로 wall broadcast하므로 err로 남긴다.
        logger -p ${FACILITY}.err "[$tag:$LINENO] Invalid Voltage!! CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"
    else
        logger -p ${FACILITY}.err "[$tag:$LINENO] ADC read failed: $DEV (check .mcp.iio_device in $WIFI_INIT_CONF_JSON)"
    fi

    probe_fail=$((probe_fail + 1))
    if [ "$probe_fail" -ge "$MCP_MAX_PROBE_FAIL" ]; then
        logger -p ${FACILITY}.err "[$tag:$LINENO] no valid supply after $probe_fail probes on $DEV; current/voltage monitoring disabled"
        exit 0
    fi
    backoff_sleep "$probe_fail"
done

logger -p ${FACILITY}.info "[$tag:$LINENO] EMERG_A=$EMERG_A, CRIT_A=$CRIT_A, ERR_A=$ERR_A, WARN_A=$WARN_A"

read_fail=0
while true; do
    if ! read_adc; then
        read_fail=$((read_fail + 1))
        # 여기서는 절대 종료하지 않는다. wifi_logger.service는 Type=oneshot(Restart 없음)이고
        # wifi_logger.sh는 이 스크립트를 백그라운드로 띄우고 빠지므로, 한번 exit하면 재부팅 전까지
        # 아무도 되살려주지 않는다 — 과전류 emerg 감시를 영구히 잃는다. 대신 로그만 억제하고
        # 계속 재시도해서 ADC가 돌아오면 감시가 자동 재개되게 한다.
        # (첫 루프의 exit은 유효하다: 그쪽은 전원 규격 자체를 못 정한 상태라 감시 시작조차 못 한다.)
        if [ "$read_fail" -le "$MCP_MAX_PROBE_FAIL" ]; then
            logger -p ${FACILITY}.err "[$tag:$LINENO] ADC read failed: $DEV ($read_fail)"
        elif [ $((read_fail % 60)) -eq 0 ]; then
            logger -p ${FACILITY}.err "[$tag:$LINENO] ADC still failing: $DEV ($read_fail consecutive)"
        fi
        backoff_sleep "$read_fail"
        continue
    fi
    if [ "$read_fail" -gt 0 ]; then
        logger -p ${FACILITY}.info "[$tag:$LINENO] ADC recovered after $read_fail failed reads; monitoring resumed"
    fi
    read_fail=0

    if   (( $(echo "$a >= $EMERG_A" | bc -l) )); then LOG_LEVEL=emerg
    elif (( $(echo "$a >= $CRIT_A"  | bc -l) )); then LOG_LEVEL=crit
    elif (( $(echo "$a >= $ERR_A"   | bc -l) )); then LOG_LEVEL=err
    elif (( $(echo "$a >= $WARN_A"  | bc -l) )); then LOG_LEVEL=warn
    else LOG_LEVEL=debug
    fi

    logger -p ${FACILITY}.${LOG_LEVEL} "[$tag:$LINENO] CH0(Current): $(printf '%.3f' "$a")A, CH1(Voltage): $(printf '%.3f' "$v")V"

    sleep "$MCP_CHECK_INTERVAL"
done
