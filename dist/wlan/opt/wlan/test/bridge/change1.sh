#!/bin/bash
wifi 0 restart
killall relayd
#relayd -I mlan1 -I eth0 -G 192.168.4.2 -B -D &
relayd -I eth0 -I mlan1 &

for ip in 192.168.4.21 192.168.4.21; do
    ip route replace $ip dev mlan1
done
