#!/bin/bash
ebtables --concurrent -t nat -A PREROUTING -p arp --arp-op Request -i mlan0 -j dnat --to-dst 90:2c:fb:00:f0:89
#ebtables --concurrent -t nat -A PREROUTING -p arp --arp-op Request -i mlan1 -j dnat --to-dst 90:2c:fb:00:f0:89

ebtables --concurrent -t nat -A POSTROUTING -p arp -o mlan0 -j snat --to-src 90:2c:fb:00:f0:89
#ebtables --concurrent -t nat -A POSTROUTING -p arp -o mlan1 -j snat --to-src 90:2c:fb:00:f0:89
