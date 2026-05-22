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
        iw $IFACE link
        echo
    } >> $LOG_DIR 2>&1
    sleep 600
done
