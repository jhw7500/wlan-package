#!/bin/bash
# SoC를 감지해 보드별 값(BOARD_TYPE/BUS_TYPE/IIO_DEV)을 결정하고 wifi_init_conf.json에 주입한다.
#
# postinst와 factory_reset.sh 양쪽에서 호출한다. 템플릿(/opt/wlan/config/wifi_init_conf.json)에는
# .mcp.iio_device 키가 없고 .global.BOARD_TYPE은 고정값이라, 템플릿을 활성 설정으로 복사한 뒤
# 이 스크립트를 거치지 않으면 보드와 맞지 않는 설정이 남는다. (factory_reset 후 wifi_logger_mcp.sh가
# iio:device0 fallback으로 떨어져 Invalid Voltage를 무한 로깅하던 원인)
#
# 사용법:
#   wifi_board_config.sh [<conf>]   감지 + <conf>에 주입 (기본 /usr/local/etc/wifi_init_conf.json)
#   wifi_board_config.sh --detect   감지 결과만 KEY='VAL' 형태로 출력 (eval 용, 설정 파일 미변경)
set -u

tag=$(basename "$0")
WIFI_CONF_DEFAULT="/usr/local/etc/wifi_init_conf.json"

SOC_ID=$(cat /sys/devices/soc0/soc_id 2>/dev/null || echo "")
if echo "$SOC_ID" | grep -qi "i\.MX93"; then
    BOARD_TYPE="imx93"
    BUS_TYPE="sdio"
    IIO_DEV_FALLBACK="/sys/bus/iio/devices/iio:device1"
else
    BOARD_TYPE="imx8mm"
    BUS_TYPE="pcie"
    IIO_DEV_FALLBACK="/sys/bus/iio/devices/iio:device0"
fi

# wifi_logger_mcp.sh가 실제로 읽는 4개 속성을 모두 가진 iio 디바이스를 찾는다.
# 인덱스는 프로브 순서에 밀린다 — iMX93은 SoC 내장 imx93-adc가 device0을 차지하고 외장 ADC(I2C
# 0x68)가 device1로 밀리며, IMU가 붙은 보드에서는 번호가 더 밀린다. 내장 imx93-adc는 채널별
# scale(in_voltage0_scale)이 없고 공용 in_voltage_scale만 있어 이 검사에서 자연히 걸러진다.
# 후보가 정확히 하나일 때만 채택하고, 없거나 여럿이면 보드별 인덱스로 폴백한다.
detect_iio_dev() {
    local d found="" n=0
    for d in /sys/bus/iio/devices/iio:device*; do
        [ -d "$d" ] || continue
        [ -r "$d/in_voltage0_raw" ]   || continue
        [ -r "$d/in_voltage0_scale" ] || continue
        [ -r "$d/in_voltage1_raw" ]   || continue
        [ -r "$d/in_voltage1_scale" ] || continue
        found="$d"
        n=$((n + 1))
    done

    [ "$n" -eq 1 ] || return 1
    printf '%s\n' "$found"
}

if IIO_DEV=$(detect_iio_dev); then
    IIO_SOURCE="probe($(cat "$IIO_DEV/name" 2>/dev/null))"
else
    IIO_DEV="$IIO_DEV_FALLBACK"
    IIO_SOURCE="fallback($BOARD_TYPE default)"
    logger -p local0.warn "[$tag:$LINENO] no unique iio device with in_voltage0/1_raw+scale; using $IIO_DEV"
fi

if [ "${1:-}" = "--detect" ]; then
    printf "SOC_ID='%s'\nBOARD_TYPE='%s'\nBUS_TYPE='%s'\nIIO_DEV='%s'\n" \
        "$SOC_ID" "$BOARD_TYPE" "$BUS_TYPE" "$IIO_DEV"
    exit 0
fi

WIFI_CONF="${1:-$WIFI_CONF_DEFAULT}"

if [ ! -f "$WIFI_CONF" ]; then
    logger -p local0.err "[$tag:$LINENO] missing $WIFI_CONF; skip board config"
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    logger -p local0.err "[$tag:$LINENO] jq not found; skip board config for $WIFI_CONF"
    exit 1
fi

tmp="${WIFI_CONF}.tmp"
if jq --arg b "$BOARD_TYPE" --arg bus "$BUS_TYPE" --arg iio "$IIO_DEV" \
      '.global.BOARD_TYPE = $b | .global.BUS_TYPE = $bus | .global.MOD_PARA = "cts/wifi_mod_para.conf" | .mcp.iio_device = $iio' \
      "$WIFI_CONF" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    mv "$tmp" "$WIFI_CONF"
    # factory_reset은 곧바로 reboot하므로, 전원이 끊겨도 보드 설정이 유실되지 않도록 동기화한다
    # (postinst의 cpchk도 같은 이유로 cp 직후 sync한다).
    sync "$WIFI_CONF" 2>/dev/null || sync
    logger -p local0.info "[$tag:$LINENO] $BOARD_TYPE detected (soc_id=$SOC_ID), BUS_TYPE=$BUS_TYPE, iio_device=$IIO_DEV [$IIO_SOURCE] -> $WIFI_CONF"
else
    rm -f "$tmp"
    logger -p local0.err "[$tag:$LINENO] jq update failed; keep existing $WIFI_CONF"
    exit 1
fi
