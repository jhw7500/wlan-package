#!/bin/bash
tag=$(basename "$0")
#env > /tmp/netdisp_env.txt
#set > /tmp/netdisp_set.txt
#echo "$@" > /tmp/netdisp_args.txt
#IFACE="$1"
#IFACE="${INTERFACE:-$1}"
#echo "IFACE : $IFACE"
logger -p local0.info "[$tag:$LINENO] [$IFACE] routable event on $IFACE"

if [ "$IFACE" == "mlan0" ]; then
    BSSID=$(iw dev "$IFACE" link | awk '/Connected to/ {print $3}')
    case "$BSSID" in
        00:80:4c:e7:1b:ef | 02:80:4c:e7:1b:f0 )
            GW="192.168.4.1"
            ;;
        04:ba:d6:ec:0b:00 | 04:ba:d6:ec:0b:08 )
            GW="192.168.4.50"
            ;;
        *)
            GW="192.168.254.254"
            ;;
    esac
    ip route replace default via "$GW" dev "$IFACE"
    logger -p local0.info "[$tag:$LINENO] [$IFACE] set GW to $GW (BSSID: $BSSID)"
fi
