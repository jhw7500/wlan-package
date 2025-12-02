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

:<<"END"
if [[ "$IFACE" == "mlan0" ]]; then
    #sleep 1
    #echo "logger_cap.py" > /dev/kmsg
    #python3 /usr/local/logger/logger_cap.py &
    #systemctl restart logger_cap
    #PID4=$!
    #logger -p syslog.notice "[$tag:$LINENO] logger_cap.py($PID4)"

    #sleep 1
    #echo "logger_scan.py" > /dev/kmsg
    #python3 /usr/local/logger/logger_scan.py &
    #PID3=$!
    #logger -p syslog.notice "[$tag:$LINENO] logger_scan.py($PID3)"
fi
END


:<<'END'
if [[ "$IFACE" == "mlan0" ]]; then
INTERVAL=0.5
PREV_AP=""
PREV_RESTART_TIME=$(date +$s)
RESTART_TIME_LIMIT=$((3600*6))

logger -p local0.notice "[$tag:$LINENO] [$IFACE] interval : $INTERVAL, prev restart time : $PREV_RESTART_TIME, restart time limit : $RESTART_TIME_LIMIT" 
systemctl restart wifi_capture
#while true; do
#    sleep 10
#done

while true; do
    #CURRENT_AP=$(iw dev $IFACE link | grep "Connected to" | awk '{print $3}')
    CURRENT_AP=$(iwconfig $IFACE 2>&1 | grep -oP 'Access Point: \K([0-9A-Fa-f:]{17})')
    #NOW=$(date +%s)
    if [[ "$CURRENT_AP" != "$PREV_AP" ]]; then
        logger -p local0.notice "[$tag:$LINENO] [$IFACE] AP changed: $PREV_AP -> $CURRENT_AP"
        PREV_AP="$CURRENT_AP"

        if [[ -n "$PREV_AP" ]]; then
            logger -p local0.notice "[$tag:$LINENO] [$IFACE] systemctl restart logger_cap (due to AP change)"
            systemctl restart wifi_capture
            #PREV_RESTART_TIME=$NOW
        else
            logger -p local0.notice "[$tag:$LINENO] [$IFACE] systemctl stop logger_cap (due to AP disconnect)"
            systemctl stop wifi_capture
        fi
    fi

    sleep $INTERVAL
done

#else
    #logger -p local0.notice "[$tag:$LINENO] [$IFACE] no capture because interface is not main interface"
    #while true; do
    #    sleep 10
    #done
fi
END
