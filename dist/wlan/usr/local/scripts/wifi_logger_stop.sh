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

# stat.py가 실제 종료될 때까지 폴링(최대 5초). start.sh가 곧바로 새 인스턴스를 띄워도
# 이전 인스턴스가 사라진 뒤라 flock 경합·미기동을 막는다. 5초 후에도 살아있으면 SIGKILL.
for _ in 1 2 3 4 5; do
    # [/] 정규식 트릭: pgrep -f 가 자기 자신(패턴을 담은 cmdline)을 매칭하지 않도록 앵커
    pgrep -f "[/]usr/local/logger/wifi_logger_stat.py $IFACE" >/dev/null 2>&1 || break
    sleep 1
done
pkill -KILL -f "/usr/local/logger/wifi_logger_stat.py $IFACE" 2>/dev/null

rm /dev/shm/json/$IFACE/*
