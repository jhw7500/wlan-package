#!/bin/bash
systemctl stop wpa_supplicant@mlan0
#sleep 0.2
#5G
mlanutl mlan0 bandcfg 0x254 
#2G
#mlanutl mlan0 bandcfg 0x10b
systemctl start wpa_supplicant@mlan0
systemctl restart wifi_bridge@mlan0
systemctl restart wifi_capture@mlan0
