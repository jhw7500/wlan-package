#!/bin/bash
tag=$(basename "$0")
key=LOG

logger -p local0.info "[$tag:$LINENO] wifi logger start"

#/bin/python3 /usr/local/logger/wifi_module_check.py

#/usr/local/scripts/eth_mac_get.sh
#/bin/python3 /usr/local/logger/wired_mac_ip_get.py
/usr/local/scripts/wifi_logger_mmc.sh &
/usr/local/scripts/wifi_logger_temp.sh &
/usr/local/scripts/wifi_logger_mcp.sh &
/bin/python3 /usr/local/logger/wifi_logger_summary.py &

#sleep 1
#systemctl restart wifi_capture

#sleep 5
#python3 /usr/local/logger/wifi_module_check.py &
