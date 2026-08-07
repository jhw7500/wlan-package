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

# iface별 로거(wifi_logger_link.py / wifi_logger_scan.py / wifi_logger_stat.py /
# wifi_link_snapshot.sh)는 systemd 템플릿 유닛(각 *@%i)이 소유·감독한다. PartOf 로
# wifi_logger@%i 정지/재시작이 각 유닛에 전파되어 systemd 가 SIGTERM 하므로 여기서
# pkill 하지 않는다(pkill 하면 Restart=always 재시작과 경합해 stop 이 안 먹는다).

rm /dev/shm/json/$IFACE/*
