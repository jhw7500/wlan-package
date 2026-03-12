import json
import time
import sys
import os
import logging
import signal
from sUTILS import Logger, _EXTRA_
from datetime import datetime

VERSION = "0.0"
MLAN0_JSON = "/var/log/cantops/json/mlan0/link.json"
MLAN1_JSON = "/var/log/cantops/json/mlan1/link.json"
LOG_FILE = "/var/log/cantops/summary/summary.log"

def handle_sigterm(signum, frame):
    logger.message('crit', f"SIGTERM {signum} received! Cleaning up...", _EXTRA_())
    cleanup()
    sys.exit(0)

def cleanup():
    pass

def safe_get(dct, *keys):
    try:
        for key in keys:
            dct = dct[key]
        return dct
    except (KeyError, TypeError):
        return "---"

def format_field(value, width):
    return f"{value:<{width}}"[:width]

def ensure_log_directory():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def format_center(value, width):
    return f"{value:^{width}}"[:width]

def strip_dbm(value):
    """'-65 dBm' → '-65', 없으면 '-'"""
    if value and value != "---":
        return str(value).replace(" dBm", "").strip()
    return "-"

def extract_info(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            freq = safe_get(data, "info", "freq") or "-"
            addr = safe_get(data, "link", "address") or "-"
            sig = strip_dbm(safe_get(data, "link", "signal"))
            sig_avg = strip_dbm(safe_get(data, "link", "signal_avg"))
            return str(freq), str(addr), sig, sig_avg
    except Exception:
        return None

def main():
    ensure_log_directory()
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if now:
            line = (f"{now}")

        #freq0, addr0, sig0 = extract_info(MLAN0_JSON)
        #freq1, addr1, sig1 = extract_info(MLAN1_JSON)

        mlan0_info = extract_info(MLAN0_JSON)
        if mlan0_info:
            freq0, addr0, sig0, sig_avg0 = mlan0_info
            sig_combined0 = f"{sig0}/{sig_avg0} dBm"
            line += (
                f" || mlan0 | {format_center(freq0, 6)} | {format_center(addr0, 17)}"
                f" | {format_center(sig_combined0, 18)}"
            )

        mlan1_info = extract_info(MLAN1_JSON)
        if mlan1_info:
            freq1, addr1, sig1, sig_avg1 = mlan1_info
            sig_combined1 = f"{sig1}/{sig_avg1} dBm"
            line += (
                f" || mlan1 | {format_center(freq1, 6)} | {format_center(addr1, 17)}"
                f" | {format_center(sig_combined1, 18)}"
            )

        line += "\n"

        with open(LOG_FILE, "a") as log:
            log.write(line)
        time.sleep(0.999)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    program_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    logger = Logger(app_name="SUMM", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    logger.message("info", f"version : {VERSION}, LOG_FILE : {LOG_FILE}", _EXTRA_())

    main()
