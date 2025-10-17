#!/bin/bash
systemctl stop wifi_bridge@mlan0
wpa_cli -i mlan0 set_network 0 bgscan "";
wpa_cli -i mlan0 set_network 0 bss_transition_disallow 1;
wpa_cli -i mlan0 reconfigure;
wpa_cli -i mlan0 disconnect;
sleep 1;
wpa_cli -i mlan0 set_network 0 freq_list "5180 5200 5220";
wpa_cli -i mlan0 set_network 0 scan_freq "5180 5200 5220";
wpa_cli -i mlan0 reassociate;
systemctl start wifi_bridge@mlan0
