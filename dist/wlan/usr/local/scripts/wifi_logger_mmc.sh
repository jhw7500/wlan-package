#!/bin/sh
tag=$(basename "$0")
EXT=/sys/kernel/debug/mmc2/mmc2:0001/ext_csd

cleanup() {
    logger -p $local3.info "[$tag:$LINENO] wifi_logger_mmc stop"
    exit 0
}
trap cleanup INT TERM


logger -p local3.info "[$tag:$LINENO] wifi_logger_mmc start"

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

    sev="notice"
    case "$LTA$LTB" in
        *0A*|*0B*) sev="emerg" ;;
        *09*      ) sev="crit"  ;;
        *08*      ) sev="error" ;;
        *07*      ) sev="warning" ;;
    esac

    logger -p local3.$sev "[$tag:$LINENO] PRE_EOL=$EOL_TXT, LifeA=$BKT_A, LifeB=$BKT_B (raw: A=0x$LTA, B=0x$LTB)"

    sleep 300
done

logger -p local3.info "[$tag:$LINENO] wifi_logger_mmc stop"

