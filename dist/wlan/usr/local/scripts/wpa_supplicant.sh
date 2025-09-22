#!/bin/bash
tag=$(basename "$0")
key=LOG
IFACE=$1

if [ "$IFACE" == "mlan0" ]; then
    systemctl restart wifi_bridge@mlan0
fi

/usr/sbin/wpa_supplicant -c/etc/wpa_supplicant/wpa_supplicant-$IFACE.conf -i$IFACE -d

