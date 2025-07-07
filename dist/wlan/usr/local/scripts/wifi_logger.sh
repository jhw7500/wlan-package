#!/bin/bash
tag=$(basename "$0")
key=LOG

logger -p local0.info "[$tag:$LINENO] wifi logger start"

#sleep 2
echo '{}' > /var/log/cantops/scan/mlan0/beacon.json
echo '{}' > /var/log/cantops/scan/mlan1/beacon.json
echo '{}' > /var/log/cantops/link/mlan0/link.json
echo '{}' > /var/log/cantops/link/mlan1/link.json

/bin/python3 /usr/local/logger/wifi_mac_get.py mlan0 &
/bin/python3 /usr/local/logger/wifi_logger_summary.py 

#sleep 1
#systemctl restart wifi_capture

#sleep 5
#python3 /usr/local/logger/wifi_module_check.py &


