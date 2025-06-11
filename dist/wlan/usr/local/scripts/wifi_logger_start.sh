#!/bin/bash
tag=$(basename "$0")
key=LOG

IFACE=$1

logger -p local0.notice "[$tag:$LINENO] $IFACE logger start $IFACE"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] $IFACE is wrong!!"
    exit 1
fi

sleep 3
echo "wifi_logger_stat.py" > /dev/kmsg
python3 /usr/local/logger/wifi_logger_stat.py $IFACE &
PID1=$!
logger -p local0.notice "[$tag:$LINENO] wifi_logger_stat.py $IFACE($PID1)"

#sleep 1
echo "wifi_logger_link.py" > /dev/kmsg
python3 /usr/local/logger/wifi_logger_link.py $IFACE &
PID2=$!
logger -p local0.notice "[$tag:$LINENO] wifi_logger_link.py $IFACE($PID2)"

#sleep 1
echo "wifi_logger_scan.py" > /dev/kmsg
python3 /usr/local/logger/wifi_logger_scan.py $IFACE &
PID3=$!
logger -p local0.notice "[$tag:$LINENO] wifi_logger_scan.py $IFACE($PID3)"

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

logger -p local0.notice "[$tag:$LINENO] iface : $IFACE, interval : $INTERVAL, prev restart time : $PREV_RESTART_TIME, restart time limit : $RESTART_TIME_LIMIT" 
systemctl restart wifi_capture
#while true; do
#    sleep 10
#done

while true; do
    #CURRENT_AP=$(iw dev $IFACE link | grep "Connected to" | awk '{print $3}')
    CURRENT_AP=$(iwconfig $IFACE 2>&1 | grep -oP 'Access Point: \K([0-9A-Fa-f:]{17})')
    #NOW=$(date +%s)
    if [[ "$CURRENT_AP" != "$PREV_AP" ]]; then
        logger -p local0.notice "[$tag:$LINENO] AP changed: $PREV_AP -> $CURRENT_AP"
        PREV_AP="$CURRENT_AP"

        if [[ -n "$PREV_AP" ]]; then
            logger -p local0.notice "[$tag:$LINENO] systemctl restart logger_cap (due to AP change)"
            systemctl restart wifi_capture
            #PREV_RESTART_TIME=$NOW
        else
            logger -p local0.notice "[$tag:$LINENO] systemctl stop logger_cap (due to AP disconnect)"
            systemctl stop wifi_capture
        fi
    fi

    sleep $INTERVAL
done

#else
    #logger -p local0.notice "[$tag:$LINENO] no capture because $IFACE is not main interface"
    #while true; do
    #    sleep 10
    #done
fi
END
