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
# start.sh가 띄운 iface별 로거를 종료(재시작 시 중복 인스턴스 방지).
# 각 로거를 SIGTERM 후 실제 종료를 폴링(최대 5초)하고, 안 죽으면 SIGKILL로 확실히 제거한다.
# start.sh가 곧바로 새 인스턴스를 띄워도 이전 인스턴스가 사라진 뒤라 flock 경합·미기동을 막는다.
# wifi_logger_link.py 는 systemd 유닛(wifi_logger_link@%i)이 소유·감독한다. PartOf 로
# wifi_logger@%i 정지/재시작이 link 유닛에 전파되어 systemd 가 SIGTERM 하므로 여기선 제외
# (여기서 pkill 하면 systemd 재시작과 경합).
for _ls in wifi_logger_scan.py wifi_logger_stat.py; do
    pkill -f "/usr/local/logger/$_ls $IFACE" 2>/dev/null
    for _ in 1 2 3 4 5; do
        # [/] 트릭: pgrep -f 가 자기 자신(패턴을 담은 cmdline)을 매칭하지 않도록 앵커
        pgrep -f "[/]usr/local/logger/$_ls $IFACE" >/dev/null 2>&1 || break
        sleep 1
    done
    pkill -KILL -f "/usr/local/logger/$_ls $IFACE" 2>/dev/null
done
pkill -f "/usr/local/scripts/wifi_link_snapshot.sh $IFACE" 2>/dev/null

rm /dev/shm/json/$IFACE/*
