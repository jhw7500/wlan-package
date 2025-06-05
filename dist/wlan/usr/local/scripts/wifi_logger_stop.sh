#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

logger -p local0.notice "[$tag:$LINENO] $IFACE logger stop"

:<<'END'
if [ "$IFACE" = "mlan0" ]; then
    logger -p local0.notice "[$tag:$LINENO] $IFACE systemctl stop wifi_capture"
    systemctl stop wifi_capture
else
    logger -p local0.notice "[$tag:$LINENO] $IFACE is not capture interface"
fi
END
