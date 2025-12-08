#!/bin/bash
tag=$(basename "$0")
IFACE=$1
CONF_FILE=""

logger -p local0.info "[$tag:$LINENO] [$IFACE] wifi bridge start"

if [[ "$IFACE" != "mlan0" && "$IFACE" != "mlan1" ]]; then
    logger -p local0.emerg "[$tag:$LINENO] [$IFACE] interface is wrong!!"
    exit 0
fi

#for i in {1..3}; do
#    if [[ -d /sys/class/net/$IFACE ]]; then
#        break
#    fi
#    sleep 5
#done

both_up() {
    ip link show eth0 | grep -q "state UP" || return 1
    ip link show "$IFACE" | grep -q "state UP" || return 1
    cat /sys/class/net/eth0/carrier 2>/dev/null | grep -q 1 || return 1
    cat /sys/class/net/$IFACE/carrier 2>/dev/null | grep -q 1 || return 1
}

getMac() {
    if [ -e /sys/class/net/$IFACE/address ]; then
        mac_addr=$(cat /sys/class/net/$IFACE/address)
        logger -p local0.info "[$tag:$LINENO] [$IFACE] MAC Address: $mac_addr"
        echo "$mac_addr"
    else
        logger -p local0.crit "[$tag:$LINENO] [$IFACE] Interface is not found"
        echo ""
    fi

}

for _ in $(seq 1 200); do
    if both_up; then break; fi
    sleep 0.2
done

if [[ "$IFACE" == "mlan0" ]]; then
    CONF_FILE=/etc/systemd/network/20-mlan0.network
fi
if [[ "$IFACE" == "mlan1" ]]; then
    CONF_FILE=/etc/systemd/network/21-mlan1.network
fi

if [ ! -f "$CONF_FILE" ]; then
    echo "Config file not found: $CONF_FILE"
    exit 1
fi

ADDRESS_LINE=$(grep -E '^Address=' "$CONF_FILE" | head -n1 | cut -d= -f2)
IP_ADDR="${ADDRESS_LINE%%/*}"
GATEWAY=$(grep -E '^Gateway=' "$CONF_FILE" | head -n1 | cut -d= -f2)
#logger -p local0.info "[$tag:$LINENO] [$IFACE] relayd -d -I $IFACE -I eth0 ($IFACE address : $ADDRESS_LINE, gateway : $GATEWAY)"

#logger -p local0.notice "[$tag:$LINENO] [$IFACE] ip flush"
#ip addr flush dev $IFACE
#ip route del 192.168.0.0/24 dev eth0
#ip route add 192.168.4.100 dev $IFACE scope link

#for ip in 192.168.4.0 192.168.4.254; do
#    ip route replace $ip dev $IFACE
#done

#ip route replace default via $IFACE

#relayd -d -i $IFACE -i eth0 -R $GATEWAY:$ADDRESS_LINE -L $IP_ADDR -G $GATEWAY -B -t 5 -p 3
#relayd -d -I $IFACE -I eth0 -L $IP_ADDR -G $GATEWAY -B
#relayd -d -I $IFACE -I eth0 -G $GATEWAY
#relayd -d -I $IFACE -I eth0 -L 192.168.4.10

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

sysctl -w net.ipv4.ip_forward=0
#relayd -d -I $IFACE -I eth0 -G $GATEWAY
#dumb eth0 $IFACE
exec /usr/local/bin/wifi_dumb eth0 mlan0

