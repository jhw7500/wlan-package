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
echo 0 > /proc/sys/net/ipv4/conf/eth0/rp_filter
echo 1 > /proc/sys/net/ipv4/conf/eth0/arp_accept
echo 1 > /proc/sys/net/ipv4/conf/$IFACE/proxy_arp
echo 0 > /proc/sys/net/ipv4/conf/$IFACE/rp_filter
echo 1 > /proc/sys/net/ipv4/conf/$IFACE/arp_accept
systemctl stop wifi_bridge@$IFACE
dumb eth0 $IFACE

#ip route replace default via $IFACE

