#!/bin/bash

case "$1" in
  start)
        systemctl start wifi_checker@mlan0
        systemctl start wifi_checker@mlan0
        systemctl start wifi_logger@mlan0
        systemctl start wifi_logger@mlan1
        systemctl start wifi_logger
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
  *)
	echo "Usage: $0 {start|start|enable|disable}"
	exit 1
    ;;
esac


