#!/bin/sh
tag=$(basename "$0")
key=LOG

IF="$1"
IFINDEX="$2"
CARRIER=$(cat /sys/class/net/$IF/carrier 2>/dev/null || echo -1)
OPER=$(cat /sys/class/net/$IF/operstate 2>/dev/null || echo "unknown")
SPEED=$(cat /sys/class/net/$IF/speed 2>/dev/null || echo "n/a")
DUPLEX=$(cat /sys/class/net/$IF/duplex 2>/dev/null || echo "n/a")
logger -t "eth-udev[$IF]" "ifindex=$IFINDEX carrier=$CARRIER operstate=$OPER speed=$SPEED duplex=$DUPLEX"
