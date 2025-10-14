#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1
CONF_FILE=""

for i in {1..3}; do
    if [[ -d /sys/class/net/$IFACE ]]; then
        break
    fi
    sleep 5
done

if [[ "$IFACE" == "mlan0" ]]; then
    CONF_FILE=/etc/systemd/network/20-mlan0.network
fi
if [[ "$IFACE" == "mlan1" ]]; then
    CONF_FILE=/etc/systemd/network/21-mlan1.network
fi

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi bridge start ($CONF_FILE)"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 1
fi

getMac() {
    IFACE=$1

    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local1.info "[$tag:$LINENO] [$IFACE] MAC Address: $mac_addr"
		echo "$mac_addr"
    else
        logger -p local1.crit "[$tag:$LINENO] [$IFACE] Interface is not found"
		echo ""
    fi

}

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

ADDRESS_LINE=$(grep -E '^Address=' "$CONF_FILE" | head -n1 | cut -d= -f2)
IP_ADDR="${ADDRESS_LINE%%/*}"
GATEWAY=$(grep -E '^Gateway=' "$CONF_FILE" | head -n1 | cut -d= -f2)
#logger -p local0.info "[$tag:$LINENO] [$IFACE] relayd -d -I $IFACE -I eth0 ($IFACE address : $ADDRESS_LINE, gateway : $GATEWAY)"

:<<'END'
mac_eth=$(getMac eth0)
mac_org=$(getMac mlan0)

logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC Address: $mac_org, eth0 MAC Address: $mac_eth"

ip link set mlan0 down
ip link set mlan0 address $mac_new
ip link set mlan0 up

#mac_new=$(ip link show mlan0 | awk '/ether/ {print $2}')
mac_new=$(getMac mlan0)
logger -p local0.info "[$tag:$LINENO] [$IFACE] change mlan0 Mac Address : $mac_new"
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

#ip route replace default via $IFACE

#relayd -d -i $IFACE -i eth0 -R $GATEWAY:$ADDRESS_LINE -L $IP_ADDR -G $GATEWAY -B -t 5 -p 3
#relayd -d -I $IFACE -I eth0 -L $IP_ADDR -G $GATEWAY -B

#relayd -d -I $IFACE -I eth0 -G $GATEWAY
#relayd -d -I $IFACE -I eth0 -L 192.168.4.10
#systemctl restart wifi_arping@$IFACE
#systemctl restart wifi_ping@$IFACE
#ip route replace default via $IFACE

:<<'END'
#echo 1 > /proc/sys/net/ipv4/conf/eth0/proxy_arp
#echo 0 > /proc/sys/net/ipv4/conf/eth0/rp_filter
#echo 1 > /proc/sys/net/ipv4/conf/eth0/arp_accept
#echo 1 > /proc/sys/net/ipv4/conf/$IFACE/proxy_arp
#echo 0 > /proc/sys/net/ipv4/conf/$IFACE/rp_filter
#echo 1 > /proc/sys/net/ipv4/conf/$IFACE/arp_accept
#echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter
#echo 0 > /proc/sys/net/ipv4/conf/eth0/rp_filter
#echo 0 > /proc/sys/net/ipv4/conf/$IFACE/rp_filter
#echo 1 > /proc/sys/net/ipv4/conf/all/arp_ignore
#echo 1 > /proc/sys/net/ipv4/conf/eth0/arp_ignore
#echo 1 > /proc/sys/net/ipv4/conf/$IFACE/
sysctl -w net.ipv4.conf.all.rp_filter=0
sysctl -w net.ipv4.conf.eth0.rp_filter=0
sysctl -w net.ipv4.conf.$IFACE.rp_filter=0
sysctl -w net.ipv4.conf.all.arp_ignore=1
sysctl -w net.ipv4.conf.eth0.arp_ignore=1
sysctl -w net.ipv4.conf.$IFACE.arp_ignore=1
sysctl -w net.ipv4.conf.all.arp_announce=2
sysctl -w net.ipv4.conf.eth0.arp_announce=2
sysctl -w net.ipv4.conf.$IFACE.arp_announce=2
#sysctl -w net.ipv4.conf.all.proxy_arp_pvlan=1
#sysctl -w net.ipv4.conf.eth0.proxy_arp=1
#sysctl -w net.ipv4.conf.$IFACE.proxy_arp=1
END


sysctl -w net.ipv4.ip_forward=1
relayd -d -I $IFACE -I eth0 -G $GATEWAY
#dumb eth0 $IFACE
