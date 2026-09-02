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
SOC_ID_PATH="${WIFI_SOC_ID_PATH:-/sys/devices/soc0/soc_id}"
SYS_MODULE_ROOT="${WIFI_SYS_MODULE_ROOT:-/sys/module}"

# wifi_logger_mcp.sh가 실제로 읽는 4개 속성을 모두 가진 iio 디바이스를 찾는다.
# 인덱스는 프로브 순서에 밀린다 — iMX93은 SoC 내장 imx93-adc가 device0을 차지하고 외장 ADC(I2C
# 0x68)가 device1로 밀리며, IMU가 붙은 보드에서는 번호가 더 밀린다. 내장 imx93-adc는 채널별
# scale(in_voltage0_scale)이 없고 공용 in_voltage_scale만 있어 이 검사에서 자연히 걸러진다.
# 후보가 정확히 하나일 때만 채택하고, 없거나 여럿이면 보드별 인덱스로 폴백한다.
# 후보가 없으면 1, 둘 이상이면 2를 반환한다 — 폴백 사유를 로그에서 구분하기 위함.
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

    [ "$n" -eq 0 ] && return 1
    [ "$n" -gt 1 ] && return 2
    printf '%s\n' "$found"
}

resolve_iio_dev() {
    local rc _why
    if IIO_DEV=$(detect_iio_dev); then
        IIO_SOURCE="probe($(cat "$IIO_DEV/name" 2>/dev/null))"
    else
        rc=$?
        case "$rc" in
            2) _why="multiple iio devices match in_voltage0/1_raw+scale (ambiguous)" ;;
            *) _why="no iio device matches in_voltage0/1_raw+scale" ;;
        esac
        IIO_DEV="$IIO_DEV_FALLBACK"
        IIO_SOURCE="fallback($BOARD_TYPE default)"
        logger -p local0.warn "[$tag:$LINENO] $_why; falling back to $IIO_DEV" || true
    fi
    IIO_DEV=$(printf '%s' "$IIO_DEV" | tr -cd 'A-Za-z0-9/:._-')
}

detect_board() {
    local family safe_soc_id
    SOC_ID=$(cat "$SOC_ID_PATH" 2>/dev/null) || {
        logger -p local0.emerg "[$tag:$LINENO] cannot read SoC identity: $SOC_ID_PATH" || true
        return 1
    }
    safe_soc_id=$(printf '%s' "$SOC_ID" | tr -cd 'A-Za-z0-9._ :-')
    [ "$SOC_ID" = "$safe_soc_id" ] || {
        logger -p local0.emerg "[$tag:$LINENO] unsafe SoC identity: $SOC_ID_PATH" || true
        return 1
    }
    [ -n "$SOC_ID" ] || {
        logger -p local0.emerg "[$tag:$LINENO] empty SoC identity: $SOC_ID_PATH" || true
        return 1
    }
    family=$(printf '%s' "$SOC_ID" | tr '[:lower:]' '[:upper:]')
    case "$family" in
        I.MX93)
            BOARD_TYPE=imx93; BUS_TYPE=sdio
            IIO_DEV_FALLBACK=/sys/bus/iio/devices/iio:device1
            ;;
        I.MX8MM)
            BOARD_TYPE=imx8mm; BUS_TYPE=pcie
            IIO_DEV_FALLBACK=/sys/bus/iio/devices/iio:device0
            ;;
        *)
            logger -p local0.emerg "[$tag:$LINENO] unsupported SoC identity: $SOC_ID" || true
            return 1
            ;;
    esac
    resolve_iio_dev
}

emit_detection() {
    printf "SOC_ID='%s'\nBOARD_TYPE='%s'\nBUS_TYPE='%s'\nIIO_DEV='%s'\n" \
        "$SOC_ID" "$BOARD_TYPE" "$BUS_TYPE" "$IIO_DEV"
}

module_field() {
    local ko="$1" field="$2" values
    [ -f "$ko" ] && [ ! -L "$ko" ] && [ -s "$ko" ] || return 1
    values=$(tr '\000' '\n' < "$ko" | awk -v field="$field" '
        BEGIN { prefix = field "=" }
        index($0, prefix) == 1 {
            count++
            value = substr($0, length(prefix) + 1)
        }
        END {
            if (count != 1 || value == "") exit 1
            print value
        }
    ') || return 1
    printf '%s\n' "$values"
}

verify_loaded_one() {
    local module="$1" ko="$2" field expected actual
    for field in version srcversion; do
        expected=$(module_field "$ko" "$field") || {
            logger -p local0.emerg "[$tag:$LINENO] module metadata invalid: module=$module ko=$ko field=$field expected=<one-nonempty-value> actual=<missing-or-duplicate>" || true
            return 1
        }
        actual=$(cat "$SYS_MODULE_ROOT/$module/$field" 2>/dev/null) || actual=""
        if [ -z "$actual" ] || [ "$actual" != "$expected" ]; then
            logger -p local0.emerg "[$tag:$LINENO] loaded module metadata mismatch: module=$module ko=$ko field=$field expected=$expected actual=${actual:-<missing>}" || true
            return 1
        fi
    done
}

verify_loaded() {
    local board="$1" mlan_ko="$2" moal_ko="$3" expected_mlan expected_moal actual_mlan actual_moal
    case "$board" in
        imx93) expected_mlan=mlan_imx93.ko; expected_moal=moal_imx93.ko ;;
        imx8mm) expected_mlan=mlan_imx8.ko; expected_moal=moal_imx8.ko ;;
        *)
            logger -p local0.emerg "[$tag:$LINENO] unsupported module board identity: $board" || true
            return 1
            ;;
    esac
    actual_mlan=$(basename "$mlan_ko")
    actual_moal=$(basename "$moal_ko")
    if [ "$actual_mlan" != "$expected_mlan" ] || [ "$actual_moal" != "$expected_moal" ]; then
        logger -p local0.emerg "[$tag:$LINENO] selected KO basename mismatch: board=$board expected=$expected_mlan/$expected_moal actual=$actual_mlan/$actual_moal" || true
        return 1
    fi
    verify_loaded_one mlan "$mlan_ko" &&
        verify_loaded_one moal "$moal_ko"
}

case "${1:-}" in
    --detect)
        [ "$#" -eq 1 ] || exit 64
        detect_board || exit 1
        emit_detection
        exit 0
        ;;
    --verify-loaded)
        [ "$#" -eq 4 ] || exit 64
        verify_loaded "$2" "$3" "$4" || {
            logger -p local0.emerg "[$tag:$LINENO] selected/loaded module identity mismatch" || true
            exit 1
        }
        exit 0
        ;;
    --*) exit 64 ;;
esac

detect_board || exit 1

WIFI_CONF="${1:-$WIFI_CONF_DEFAULT}"

if [ ! -f "$WIFI_CONF" ]; then
    logger -p local0.err "[$tag:$LINENO] missing $WIFI_CONF; skip board config"
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    logger -p local0.err "[$tag:$LINENO] jq not found; skip board config for $WIFI_CONF"
    exit 1
fi

tmp=""
cleanup_tmp() {
    [ -z "$tmp" ] || rm -f -- "$tmp"
}
trap cleanup_tmp EXIT

if ! tmp=$(mktemp "${WIFI_CONF}.tmp.XXXXXX"); then
    logger -p local0.err "[$tag:$LINENO] cannot create temporary file for $WIFI_CONF"
    exit 1
fi

# jq의 stderr는 버리지 않고 캡처해 실패 원인(JSON 구문 오류 등)을 로그로 남긴다.
# `2>&1 > file`은 stderr를 명령치환으로, stdout을 파일로 보낸다 (순서 중요).
jq_err=$(jq --arg b "$BOARD_TYPE" --arg bus "$BUS_TYPE" --arg iio "$IIO_DEV" '
            def is_product_verify($v):
                (($v | type) == "object"
                 and $v.physical_tx == "0x0303"
                 and $v.physical_rx == "0x0303"
                 and $v.user_htstream == "0x2121");
            .global.BOARD_TYPE = $b
            | .global.BUS_TYPE = $bus
            | .global.MOD_PARA = "cts/wifi_mod_para.conf"
            | .mcp.iio_device = $iio
            | if ($b != "imx93"
                  and .mlan0.antcfg.enabled == true
                  and .mlan0.antcfg.tx == "0x0303"
                  and .mlan0.antcfg.rx == "0x0101"
                  and is_product_verify(.mlan0.antcfg.verify))
              then .mlan0.antcfg = ((.mlan0.antcfg + {enabled:false, tx:"", rx:""}) | del(.verify))
              elif ($b != "imx93" and is_product_verify(.mlan0.antcfg.verify))
              then .mlan0.antcfg |= del(.verify)
              else . end
            # 505.p14/imx8 utility에는 antcfgnss(user_htstream) ABI가 없다.
            # factory reset은 postinst migrate를 거치지
            # 않고 template+board stage만 타므로, migrate의 비-imx93 분기와 동일하게
            # 제품 antcfgnss(현행 0x1111, 종전 0x2121)를 여기서도 중화한다
            # (custom 값은 보존, log-only).
            | if ($b != "imx93"
                  and .mlan0.antcfgnss.enabled == true
                  and ((.mlan0.antcfgnss.value // "") == "0x1111"
                       or (.mlan0.antcfgnss.value // "") == "0x2121"))
              then .mlan0.antcfgnss |= (. + {enabled: false})
              else . end
            ' \
            "$WIFI_CONF" 2>&1 > "$tmp")
jq_rc=$?
if [ "$jq_rc" -ne 0 ]; then
    logger -p local0.err "[$tag:$LINENO] jq update failed; keep existing $WIFI_CONF: ${jq_err:-exit status $jq_rc}"
    exit 1
fi
if [ -n "$jq_err" ]; then
    logger -p local0.err "[$tag:$LINENO] jq update failed; keep existing $WIFI_CONF: $jq_err"
    exit 1
fi
if [ ! -s "$tmp" ]; then
    logger -p local0.err "[$tag:$LINENO] jq update failed; keep existing $WIFI_CONF: empty output"
    exit 1
fi
if ! chown --reference="$WIFI_CONF" "$tmp" 2>/dev/null; then
    logger -p local0.err "[$tag:$LINENO] cannot preserve configuration ownership; keep existing $WIFI_CONF"
    exit 1
fi
if ! chmod --reference="$WIFI_CONF" "$tmp" 2>/dev/null; then
    logger -p local0.err "[$tag:$LINENO] cannot preserve configuration mode; keep existing $WIFI_CONF"
    exit 1
fi
if ! mv -- "$tmp" "$WIFI_CONF"; then
    logger -p local0.err "[$tag:$LINENO] cannot install normalized configuration; keep existing $WIFI_CONF"
    exit 1
fi
tmp=""

# factory_reset은 곧바로 reboot하므로, 전원이 끊겨도 보드 설정이 유실되지 않도록 동기화한다
# (postinst의 cpchk도 같은 이유로 cp 직후 sync한다).
sync "$WIFI_CONF" 2>/dev/null || sync
logger -p local0.info "[$tag:$LINENO] $BOARD_TYPE detected (soc_id=$SOC_ID), BUS_TYPE=$BUS_TYPE, iio_device=$IIO_DEV [$IIO_SOURCE] -> $WIFI_CONF"
