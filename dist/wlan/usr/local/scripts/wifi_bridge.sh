#!/bin/bash
tag=$(basename "$0")
key=LOG

logger -p local0.notice "[$tag:$LINENO] wifi bridge start"

getMac() {
    IFACE=$1

    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local1.notice -t bridge "$IFACE MAC Address: $mac_addr"
		echo "$mac_addr"
    else
        logger -p local1.crit -t bridge "Interface $IFACE not found"
		echo ""
    fi

}


mac_eth=$(getMac eth0)
mac_org=$(getMac mlan0)

logger -p local0.notice "mlan0 MAC Address: $mac_org, eth0 MAC Address: $mac_eth"

ip link set mlan0 down
ip link set mlan0 address $mac_new
ip link set mlan0 up

ip addr flush dev mlan0

#mac_new=$(ip link show mlan0 | awk '/ether/ {print $2}')
mac_new=$(getMac mlan0)
logger -p local0.notice "change mlan0 Mac Address : $mac_new"

#ip route del 192.168.0.0/24 dev eth0
#wpa_supplicant -i mlan0 -c /etc/wpa_supplicant.conf -B

echo 1 > /proc/sys/net/ipv4/ip_forward
relayd -I mlan0 -I eth0 &

