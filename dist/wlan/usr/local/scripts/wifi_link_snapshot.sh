#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

#logger -p local0.info "[$tag:$LINENO] [$IFACE] start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.crit "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

LOG_DIR="/var/log/cantops/stat/$IFACE/snap.log"

sleep 10

while true; do
    logger -p local0.info "[$tag:$LINENO] [$IFACE] shanpshot -> $LOG_DIR"
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    {
        echo "===== $TS ====="
        # `iw link` 는 moal 에서 연결 중에도 "Not connected." 를 반환할 수 있어 스냅샷이
        # 통째로 무의미해진다. supplicant SME(요약) + station dump(물리 지표)를 함께 남긴다.
        wpa_cli -i "$IFACE" status 2>/dev/null | grep -E '^(bssid|freq|ssid|wpa_state)='
        iw dev "$IFACE" station dump 2>/dev/null
        echo
    } >> $LOG_DIR 2>&1
    sleep 600
done
