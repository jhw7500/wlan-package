#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

logger -p local0.notice "[$tag:$LINENO] [$IFACE] logger stop"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" && "$IFACE" != "eth0" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

if [ "$IFACE" = "mlan0" ]; then
    logger -p local0.notice "[$tag:$LINENO] [$IFACE] systemctl stop wifi_capture"
    systemctl stop wifi_capture
fi

# start.sh가 띄운 iface별 로거를 명시적으로 종료 (재시작 시 중복 인스턴스 방지)
pkill -f "/usr/local/logger/wifi_logger_scan.py $IFACE" 2>/dev/null
pkill -f "/usr/local/logger/wifi_logger_link.py $IFACE" 2>/dev/null
pkill -f "/usr/local/logger/wifi_logger_stat.py $IFACE" 2>/dev/null
pkill -f "/usr/local/scripts/wifi_link_snapshot.sh $IFACE" 2>/dev/null

rm /dev/shm/json/$IFACE/*
