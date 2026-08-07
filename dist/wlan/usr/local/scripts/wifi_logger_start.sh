#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

#logger -p local0.notice "[$tag:$LINENO] [$IFACE] logger start"

if [[ "$IFACE" == "eth0" ]]; then
    # wifi_logger_link.py 는 systemd 유닛(wifi_logger_link@%i, Restart=always)이 감독 기동한다.
    # 여기서 & 로 띄우면 감독 밖 중복 인스턴스가 되므로 띄우지 않는다.
    exit 0
fi

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

#sleep 3

# iface별 로거(wifi_logger_link.py / wifi_logger_scan.py / wifi_logger_stat.py /
# wifi_link_snapshot.sh)는 systemd 유닛(wifi_logger_link@%i, wifi_logger_scan@%i,
# wifi_logger_stat@%i, wifi_link_snapshot@%i — Restart=always)이 감독 기동한다.
# (여기서 & 로 띄우면 감독 밖 중복 인스턴스가 되어 flock 경합/미감독을 유발한다.)

