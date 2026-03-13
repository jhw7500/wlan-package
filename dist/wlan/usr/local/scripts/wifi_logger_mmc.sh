#!/bin/bash
tag=$(basename "$0")
EXT=/sys/kernel/debug/mmc2/mmc2:0001/ext_csd

# Defaults
MMC_CHECK_INTERVAL=300

# Load from JSON config
WIFI_INIT_CONF_JSON="/usr/local/etc/wifi_init_conf.json"
if [ -f "$WIFI_INIT_CONF_JSON" ] && command -v jq >/dev/null 2>&1; then
    MMC_CHECK_INTERVAL=$(jq -r '.mmc.check_interval_sec // 300' "$WIFI_INIT_CONF_JSON")
fi

cleanup() {
    logger -p local0.info "[$tag:$LINENO] stop"
    exit 0
}
trap cleanup INT TERM


logger -p local0.info "[$tag:$LINENO] start"

to_bucket() {
    v=$1
    case "$v" in
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
    v=$1
    case "$v" in
        01) echo "Normal(<80%)" ;;
        02) echo "Error(>=80%)" ;;
        03) echo "Emergency(>=90%)" ;;
        *)  echo "Unknown(0x$1)" ;;
    esac
}

while true; do
    RES=$(tr -d '\n\r ' < "$EXT")

    hex_at() {
        off=$1
        pos=$((off*2))
        echo "${RES:$pos:2}" | tr '[:lower:]' '[:upper:]'
    }

    # JEDEC offsets
    PRE_EOL=$(hex_at 267)   # 0x10B
    LTA=$(hex_at 268)       # 0x10C
    LTB=$(hex_at 269)       # 0x10D

    BKT_A=$(to_bucket "$LTA")
    BKT_B=$(to_bucket "$LTB")
    EOL_TXT=$(to_eol_text "$PRE_EOL")

    sev="info"
    case "$LTA$LTB" in
        *0A*|*0B*) sev="emerg" ;;
        *09*      ) sev="crit"  ;;
        *08*      ) sev="error" ;;
        *07*      ) sev="warning" ;;
    esac

    logger -p local0.$sev "[$tag:$LINENO] PRE_EOL=$EOL_TXT, LifeA=$BKT_A, LifeB=$BKT_B (raw: A=0x$LTA, B=0x$LTB)"

    sleep "$MMC_CHECK_INTERVAL"
done

