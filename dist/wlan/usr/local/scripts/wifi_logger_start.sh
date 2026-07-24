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

#echo "wifi_logger_scan.py $IFACE" > /dev/kmsg
/bin/python3 /usr/local/logger/wifi_logger_scan.py $IFACE &
PID=$!
#logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_scan.py($PID)"

# wifi_logger_link.py 는 systemd 유닛(wifi_logger_link@%i, Restart=always)이 감독 기동한다.
# (여기서 & 로 띄우면 감독 밖 중복 인스턴스가 되어 flock 경합/미감독을 유발한다.)

#echo "wifi_logger_stat.py $IFACE" > /dev/kmsg
/bin/python3 /usr/local/logger/wifi_logger_stat.py $IFACE &
PID=$!
#logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_stat.py($PID)"

#echo "wifi_link_snapshot.sh $IFACE" > /dev/kmsg
/usr/local/scripts/wifi_link_snapshot.sh $IFACE &
PID=$!
#logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_link_snapshot.sh($PID)"

