#!/bin/bash
killall relayd
wifi 1 restart
#relayd -I mlan0 -I eth0 -G 192.168.4.2 -B -D &
sleep 1
relayd -I eth0 -I mlan0 &

for ip in 192.168.4.20 192.168.4.20; do
    ip route replace $ip dev mlan0
done

