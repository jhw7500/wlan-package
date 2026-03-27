#!/bin/bash
iface=eth0
target_ip=""

# 1. ARP 테이블에서 IP 알아내기
target_ip=$(ip neigh show dev $iface | grep 'lladdr' | awk '{print $1}')

if [ -z "$target_ip" ]; then
  echo "No IP found on $iface"
  exit 1
fi

echo "Found client IP: $target_ip"

# 2. 주기적으로 ping으로 relayd ARP 유지
while true; do
  #ping -c 1 -I $iface $target_ip > /dev/null
  arping -c 1 -I eth0 $target_ip
  sleep 10
done
