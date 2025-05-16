#!/bin/bash

# 인터페이스 이름 정의
ETH_IF="eth0"
WLAN_IF="mlan0"

# IP 포워딩 활성화
echo 1 > /proc/sys/net/ipv4/ip_forward

# Proxy ARP 설정
for IF in $ETH_IF $WLAN_IF; do
    echo 1 > /proc/sys/net/ipv4/conf/$IF/proxy_arp
    echo 1 > /proc/sys/net/ipv4/conf/$IF/accept_local
    echo 1 > /proc/sys/net/ipv4/conf/$IF/proxy_arp_pvlan
done

# 네트워크 인터페이스 IP 설정
ip addr flush dev $ETH_IF
ip addr flush dev $WLAN_IF
ip addr add 192.168.0.4/24 dev $ETH_IF
ip addr add 192.168.0.10/24 dev $WLAN_IF
ip link set $ETH_IF up
ip link set $WLAN_IF up

echo "3주소 모드 클라이언트 브릿지 설정 완료"

