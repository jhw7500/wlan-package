#!/bin/bash
tag=$(basename "$0")

#logger -p local0.info "[$tag:$LINENO] start"

mkdir -p /var/log/cantops/cpu

/usr/local/scripts/wifi_logger_cpu.sh &
/usr/local/scripts/wifi_logger_mmc.sh &
/usr/local/scripts/wifi_logger_temp.sh &
/usr/local/scripts/wifi_logger_mcp.sh &
/bin/python3 /usr/local/logger/wifi_logger_summary.py &
