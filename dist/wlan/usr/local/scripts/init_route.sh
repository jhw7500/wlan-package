#!/bin/bash
# /usr/local/bin/init_eth0_route.sh
IFACE=eth0
for i in {2..254}; do
    arping -c 1 -I eth0 192.168.4.$i &
done
wait

TARGET=$(ip neigh show dev eth0 | grep REACHABLE | awk '{print $1}')
echo "target : $TARGET"
ip route add $TARGET dev eth0 scope link
