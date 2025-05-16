#!/bin/bash

timedatectl set-timezone Asia/Seoul

#cp ../etc/systemd/network/20-mlan0.network /etc/systemd/network/
#cp ../etc/systemd/network/21-eth0.network /etc/systemd/network/
#cp ../etc/systemd/system/* /etc/systemd/system/
#cp ../etc/rc.local /etc/
#cp ../etc/nanorc /etc/

rm /etc/systemd/system/logger*
cp -r ../etc/* /etc/

cp -r ../lib/firmware/nxp/* /lib/firmware/nxp/

chmod +x ../bin/*
mkdir -p /usr/local/bin
cp -a ../bin/* /usr/local/bin/

#mv /usr/bin/iw /usr/bin/iw_519
#cp ../git/iw/iw /usr/bin/iw_new

chmod +x ../scripts/*

rm /usr/local/logger/logger_ap.py

mkdir -p /usr/local/logger
cp ../sources/* /usr/local/logger/

mkdir -p /usr/local/scripts
cp ../scripts/* /usr/local/scripts/

systemctl disable logger_stat
systemctl disable logger_cap
systemctl disable logger_scan
systemctl disable logger_link

systemctl enable logger@mlan0
systemctl enable logger@mlan1

systemctl enable wifi_check@mlan0
systemctl enable wifi_check@mlan1

sync

