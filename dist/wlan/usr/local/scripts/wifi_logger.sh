#!/bin/bash
tag=$(basename "$0")
key=LOG

logger -p local0.notice "[$tag:$LINENO] wifi logger"

python3 /usr/local/logger/wifi_logger_summary.py &

systemctl restart wifi_capture

sleep 5
python3 /usr/local/logger/wifi_module_check.py &


