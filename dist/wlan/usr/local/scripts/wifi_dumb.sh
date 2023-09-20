#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1
logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi bridge(dumb) start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.err "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

getMac() {
    IFACE=$1

    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local1.notice "[$tag:$LINENO] [$IFACE] MAC Address: $mac_addr"
		echo "$mac_addr"
    else
        logger -p local1.crit "[$tag:$LINENO] [$IFACE] Interface is not found"
		echo ""
    fi

}


echo 0 > /proc/sys/net/ipv4/ip_forward
systemctl stop wifi_bridge@$IFACE
#systemctl start wifi_arping@$IFACE
dumb eth0 $IFACE

#ip route replace default via $IFACE

