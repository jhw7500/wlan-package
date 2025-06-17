#!/bin/bash

case "$1" in
  start)
        systemctl start wifi_checker@mlan0
        systemctl start wifi_checker@mlan0
        systemctl start wifi_logger@mlan0
        systemctl start wifi_logger@mlan1
        systemctl start wifi_logger
        ;;
  stop)
	systemctl stop wifi_checker@mlan0
	systemctl stop wifi_checker@mlan0
	systemctl stop wifi_logger@mlan0
	systemctl stop wifi_logger@mlan1
	systemctl stop wifi_logger
    ;;
  disable)
	systemctl disable wifi_checker@mlan0
	systemctl disable wifi_checker@mlan1
	systemctl disable wifi_logger@mlan0
	systemctl disable wifi_logger@mlan1
	systemctl disable wifi_logger
	#systemctl stop wifi_logger_summary
	#systemctl stop wifi_capture
    ;;
  enable)
        systemctl enable wifi_checker@mlan0
        systemctl enable wifi_checker@mlan1
        systemctl enable wifi_logger@mlan0
        systemctl enable wifi_logger@mlan1
        systemctl enable wifi_logger
    ;;
  clean)
    rm /var/log/cantops/* -r
    systemctl wifi_logger
    systemctl wifi_logger@mlan0
    systemctl rsyslog
    #cat /dev/null > /var/log/cantops/kerl.log
    #cat /dev/null > /var/log/cantops/sys.log
    #cat /dev/null > /var/log/cantops/local0.log
    #cat /dev/null > /var/log/cantops/summary/stat.log
    #cat /dev/null > /var/log/cantops/scan/mlan0/ap.log
    #cat /dev/null > /var/log/cantops/scan/mlan0/freq.log
    #cat /dev/null > /var/log/cantops/stat/
    ;;
  *)
	echo "Usage: $0 {start|start|enable|disable}"
	exit 1
    ;;
esac


