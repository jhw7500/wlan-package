
import subprocess
import re
import os
import time
import random
import logging
import sys
import json
import signal
import threading
from datetime import datetime
from sUTILS import Logger, _EXTRA_

LOG_DIR = "/var/log/cantops/scan"
WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
LOG_INTERVAL = 30
STALE_THRESHOLD_SEC = 600  #1hour
#last_log_time = 0
VERSION = "0.0"
IFACE = ""

def handle_sigterm(signum, frame):
    logger.message('crit', f"{IFACE} SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def parse_wpa_supplicant_conf(path):
    ssid = None
    freqs = []
    scan_interval = 30  #         ^r

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ssid=") and not line.startswith("#"):
                ssid = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("scan_freq=") and not line.startswith("#"):
                freqs = line.split("=", 1)[1].strip().split()
            elif line.startswith("bgscan=") and not line.startswith("#"):
                parts = line.split("=", 1)[1].strip().strip('"').split(":")
                if len(parts) == 4:  # bgscan="simple:X:Y:Z"
                    try:
                        scan_interval = int(parts[3])
                    except ValueError:
                        pass

    return ssid, freqs, scan_interval

def periodic_scan(ssid, freqs, interval):
    while True:
        if ssid and freqs:
            cmd = ["iw", IFACE, "scan", "freq"] + freqs + ["ssid", ssid]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(interval)

def main_loop():
    #subprocess.run(["ifconfig", IFACE, "up"])
    #last_log_time = time.time()

    ssid, freqs, interval = parse_wpa_supplicant_conf(WPA_CONF_FILE)
    periodic_scan(ssid, freqs, interval)
    #threading.Thread(target=periodic_scan, args=(ssid, freqs, interval), daemon=True).start()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="logger_scan", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    LOG_DIR = f"/var/log/cantops/link/{IFACE}"
    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    logger.message("info", f"version : {VERSION}, log_file : {LOG_DIR}/ap.log, {LOG_DIR}/freq.log, {LOG_DIR}/beacon.json", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)

    #   ^|     ^t^t  ^i ^f      ^c^} ^d
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    main_loop()
