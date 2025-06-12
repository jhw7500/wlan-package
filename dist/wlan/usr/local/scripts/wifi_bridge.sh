#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1
logger -p local0.notice "[$tag:$LINENO] [$IFACE] wifi bridge start"

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

:<<'END'
mac_eth=$(getMac eth0)
mac_org=$(getMac mlan0)

logger -p local0.notice "[$tag:$LINENO] [$IFACE] MAC Address: $mac_org, eth0 MAC Address: $mac_eth"

ip link set mlan0 down
ip link set mlan0 address $mac_new
ip link set mlan0 up

#mac_new=$(ip link show mlan0 | awk '/ether/ {print $2}')
mac_new=$(getMac mlan0)
logger -p local0.notice "[$tag:$LINENO] [$IFACE] change mlan0 Mac Address : $mac_new"
END

#logger -p local0.notice "[$tag:$LINENO] [$IFACE] ip flush"
#ip addr flush dev $IFACE
#ip route del 192.168.0.0/24 dev eth0
#wpa_supplicant -i mlan0 -c /etc/wpa_supplicant.conf -B
#killall relayd
#logger -p local0.notice "[$tag:$LINENO] [$IFACE] ip forward, relayd"
#relayd -I $IFACE -I eth0 

#ip route add 192.168.4.100 dev $IFACE scope link

#for ip in 192.168.4.0 192.168.4.254; do
#    ip route replace $ip dev $IFACE
#done

#echo 1 > /proc/sys/net/ipv4/ip_forward
#ip route replace default via $IFACE

#systemctl start wifi_ping@$IFACE
#sleep 5
relayd -I $IFACE -I eth0
#systemctl restart wifi_ping@$IFACE

#ip route replace default via $IFACE

