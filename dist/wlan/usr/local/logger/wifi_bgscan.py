
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
DEFAULT_INTERVAL = 30
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

def set_flag(on: bool, path="/tmp/roam_condition"):
    with open(path, "w") as f:
        if on:
            f.write("1")
        else:
            f.write("")  # 빈 파일은 OFF 상태

def get_flag(path="/tmp/roam_condition") -> bool:
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return content == "1"
    except FileNotFoundError:
        return False  # 파일이 없으면 OFF 취급

def is_wpa_running(interface="mlan0"):
    result = subprocess.run(
        ["systemctl", "is-active", f"wpa_supplicant@{interface}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"

def parse_wpa_supplicant_conf(path):
    ssid = None
    freqs = []
    interval = 30  #         ^r

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ssid=") and not line.startswith("#"):
                ssid = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("scan_freq=") and not line.startswith("#"):
                freqs = line.split("=", 1)[1].strip().split()
            elif line.startswith("#!INTERVAL="):
                try:
                    interval = int(line.split("=")[1])
                except ValueError:
                    logger.message('err', f"[{IFACE}] INTERVAL : {interval} is invalid in {path}", _EXTRA_())
                    pass
            '''
            elif line.startswith("bgscan=") and not line.startswith("#"):
                parts = line.split("=", 1)[1].strip().strip('"').split(":")
                if len(parts) == 4:  # bgscan="simple:X:Y:Z"
                    try:
                        scan_interval = int(parts[3])
                    except ValueError:
                        pass
            '''

    return ssid, freqs, interval

def construct_iw_scan_cmd(ssid, scan_freqs):
    cmd = ["iw", IFACE, "scan"]

    if scan_freqs:
        cmd += ["freq"] + scan_freqs

    if ssid:
        cmd += ["ssid", ssid]

    return cmd

def periodic_scan(ssid, freqs, interval):

    cmd = construct_iw_scan_cmd(ssid, freqs)

    if not interval:
        interval=DEFAULT_INTERVAL

    last_time = time.time()
    
    while True:
        if not os.path.exists(f"/sys/class/net/{IFACE}"):
            logger.message("err", f"[{IFACE}] /sys/class/net/{IFACE} is not exist", _EXTRA_())
            time.sleep(5)
            continue

        if not is_wpa_running(IFACE):
            time.sleep(5)
            continue

        if get_flag():
            #logger.message("info", f"[{IFACE}] roam condition on", _EXTRA_())
            time.sleep(5)
            continue

        if time.time() - last_time >= interval:
            try:
                logger.message("info", f"{cmd}", _EXTRA_())
                #subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(cmd, check=True)
                last_time = time.time()
            except subprocess.CalledProcessError as e:
                logger.message("err", f"iw scan failed: {e}", _EXTRA_())

        time.sleep(0.5)

def main_loop():
    #subprocess.run(["ifconfig", IFACE, "up"])
    #last_log_time = time.time()

    ssid, freqs, interval = parse_wpa_supplicant_conf(WPA_CONF_FILE)
    logger.message("info", f"ssid : {ssid}, freq: {freqs}", _EXTRA_())
    periodic_scan(ssid, freqs, interval)
    #threading.Thread(target=periodic_scan, args=(ssid, freqs, interval), daemon=True).start()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    logger = Logger(app_name="wifi_bgscan", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    WPA_CONF_FILE = f"/etc/wpa_supplicant/wpa_supplicant-{IFACE}.conf"
    logger.message("info", f"version : {VERSION}", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)

    main_loop()
