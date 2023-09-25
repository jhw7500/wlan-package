#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

#logger -p local0.notice "[$tag:$LINENO] [$IFACE] start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.crit "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

TS=$(date '+%Y-%m-%d %H:%M:%S')
{
    echo "===== $TS ====="
    iw $IFACE link
    echo
} >> /var/log/cantops/link_stat.log 2>&1
