#!/bin/bash
sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A POSTROUTING -o mlan0 -j MASQUERADE
iptables -A FORWARD -i eth0 -o mlan0 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i mlan0 -o eth0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

iptables -C FORWARD -i mlan0 -o eth0 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i mlan0 -o eth0 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT


# 들어오는 트래픽: 192.168.0.200 -> 192.168.1.10
iptables -t nat -A PREROUTING  -i mlan0 -d 192.168.0.200 -j DNAT --to-destination 192.168.1.10

# 나가는 트래픽: 192.168.1.10 -> 192.168.0.200 로 보이게
iptables -t nat -A POSTROUTING -o mlan0 -s 192.168.1.10 -j SNAT --to-source 192.168.0.200

# 포워딩 허용(대칭)
iptables -A FORWARD -i mlan0 -d 192.168.1.10 -j ACCEPT
iptables -A FORWARD -o mlan0 -s 192.168.1.10 -j ACCEPT
