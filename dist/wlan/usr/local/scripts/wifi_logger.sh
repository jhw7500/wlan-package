#!/bin/bash
tag=$(basename "$0")
key=LOG

logger -p local0.notice "[$tag:$LINENO] wifi logger start"

#sleep 2
rm /var/log/cantops/scan/mlan0/beacon.json
rm /var/log/cantops/scan/mlan1/beacon.json
rm /var/log/cantops/link/mlan0/link.json
rm /var/log/cantops/link/mlan1/link.json

python3 /usr/local/logger/mac_get.py &
python3 /usr/local/logger/wifi_logger_summary.py 

#sleep 1
#systemctl restart wifi_capture

#sleep 5
#python3 /usr/local/logger/wifi_module_check.py &


