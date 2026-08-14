#!/bin/bash

tag=$(basename "$0")

LOGGER_COMMAND_LIB="${WIFI_LOGGER_COMMAND_LIB:-/usr/local/scripts/wifi_logger_command_lib.sh}"
if [ ! -r "$LOGGER_COMMAND_LIB" ]; then
    LOGGER_COMMAND_LIB="$(dirname "$0")/wifi_logger_command_lib.sh"
fi
# shellcheck source=wifi_logger_command_lib.sh
. "$LOGGER_COMMAND_LIB"

MMC_CHECK_INTERVAL=300
BOARD_TYPE=imx8mm
WIFI_INIT_CONF_JSON="${WIFI_INIT_CONF_JSON:-/usr/local/etc/wifi_init_conf.json}"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    MMC_CHECK_INTERVAL=$(jq -r '.mmc.check_interval_sec // 300' "$WIFI_INIT_CONF_JSON")
    BOARD_TYPE=$(jq -r '.global.BOARD_TYPE // "imx8mm"' "$WIFI_INIT_CONF_JSON")
fi

if [ "$BOARD_TYPE" = "imx93" ]; then
    DEFAULT_EXT=/sys/kernel/debug/mmc0/mmc0:0001/ext_csd
else
    DEFAULT_EXT=/sys/kernel/debug/mmc2/mmc2:0001/ext_csd
fi
EXT="${WIFI_MMC_EXT_CSD_PATH:-$DEFAULT_EXT}"
COMMAND_TIMEOUT_SEC="${WIFI_LOGGER_COMMAND_TIMEOUT_SEC:-5}"
ONESHOT="${WIFI_LOGGER_ONESHOT:-0}"

cleanup() {
    exit 0
}
trap cleanup INT TERM

to_bucket() {
    case "$1" in
        00) echo "N/A" ;;
        01) echo "0~10%" ;;
        02) echo "10~20%" ;;
        03) echo "20~30%" ;;
        04) echo "30~40%" ;;
        05) echo "40~50%" ;;
        06) echo "50~60%" ;;
        07) echo "60~70%" ;;
        08) echo "70~80%" ;;
        09) echo "80~90%" ;;
        0A) echo "90~100%" ;;
        0B) echo "EOL+ (over)" ;;
        *)  echo "Unknown(0x$1)" ;;
    esac
}

to_eol_text() {
    case "$1" in
        01) echo "Normal(<80%)" ;;
        02) echo "Error(>=80%)" ;;
        03) echo "Emergency(>=90%)" ;;
        *)  echo "Unknown(0x$1)" ;;
    esac
}

hex_at() {
    local off=$1 pos
    pos=$((off * 2))
    printf '%s\n' "${RES:$pos:2}" | tr '[:lower:]' '[:upper:]'
}

while true; do
    if ! RES=$(logger_read_bounded "$COMMAND_TIMEOUT_SEC" "$EXT" 2>/dev/null); then
        logger -p local3.err "[$tag:$LINENO] MMC read failed: $EXT"
        [ "$ONESHOT" = "1" ] && break
        sleep "$MMC_CHECK_INTERVAL"
        continue
    fi
    RES=$(printf '%s' "$RES" | tr -d '\n\r ')

    PRE_EOL=$(hex_at 267)
    LTA=$(hex_at 268)
    LTB=$(hex_at 269)
    BKT_A=$(to_bucket "$LTA")
    BKT_B=$(to_bucket "$LTB")
    EOL_TXT=$(to_eol_text "$PRE_EOL")

    sev=info
    case "$LTA$LTB" in
        *0A*|*0B*) sev=emerg ;;
        *09*) sev=crit ;;
        *08*) sev=error ;;
        *07*) sev=warning ;;
    esac

    logger -p local3.$sev "[$tag:$LINENO] PRE_EOL=$EOL_TXT, LifeA=$BKT_A, LifeB=$BKT_B (raw: A=0x$LTA, B=0x$LTB)"
    [ "$ONESHOT" = "1" ] && break
    sleep "$MMC_CHECK_INTERVAL"
done
