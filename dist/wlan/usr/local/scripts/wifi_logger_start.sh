#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

logger -p local0.notice "[$tag:$LINENO] [$IFACE] logger start"

if [[ "$IFACE" == "eth0" ]]; then
    echo "wifi_logger_link.py $IFACE" > /dev/kmsg
    /bin/python3 /usr/local/logger/wifi_logger_link.py $IFACE &
    PID=$!
    logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_link.py($PID)"
    exit 0
fi

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

#sleep 3

echo "wifi_logger_scan.py $IFACE" > /dev/kmsg
/bin/python3 /usr/local/logger/wifi_logger_scan.py $IFACE &
PID=$!
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_scan.py($PID)"

echo "wifi_logger_link.py $IFACE" > /dev/kmsg
/bin/python3 /usr/local/logger/wifi_logger_link.py $IFACE &
PID=$!
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_link.py($PID)"

echo "wifi_logger_stat.py $IFACE" > /dev/kmsg
/bin/python3 /usr/local/logger/wifi_logger_stat.py $IFACE &
PID=$!
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_logger_stat.py($PID)"

echo "wifi_link_snapshot.sh $IFACE" > /dev/kmsg
/usr/local/scripts/wifi_link_snapshot.sh $IFACE &
PID=$!
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi_link_snapshot.sh($PID)"

