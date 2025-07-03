import json
import time
import sys
import os
import logging
import signal
from sUTILS import Logger, _EXTRA_
from datetime import datetime

VERSION = "0.0"
MLAN0_JSON = "/var/log/cantops/link/mlan0/link.json"
MLAN1_JSON = "/var/log/cantops/link/mlan1/link.json"
LOG_FILE = "/var/log/cantops/summary/summary.log"

def handle_sigterm(signum, frame):
    logger.message('crit', f"{IFACE} SIGTERM {signum} received! Cleaning up...", _EXTRA_())
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

def extract_info(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            freq = safe_get(data, "info", "freq") or "-"
            addr = safe_get(data, "station_info", "address") or "-"
            sig = safe_get(data, "station_info", "signal") or "-"
            return str(freq), str(addr), str(sig)
    except Exception:
        return "-", "-", "-"

def main():
    ensure_log_directory()
    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        freq0, addr0, sig0 = extract_info(MLAN0_JSON)
        freq1, addr1, sig1 = extract_info(MLAN1_JSON)

        line = (
            f"{now} || "
            f"{format_center(freq0, 6)} | {format_center(addr0, 17)} | {format_center(sig0, 8)} || "
            f"{format_center(freq1, 6)} | {format_center(addr1, 17)} | {format_center(sig1, 8)} \n"
        )

        with open(LOG_FILE, "a") as log:
            log.write(line)
        time.sleep(0.999)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    program_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    logger = Logger(app_name="logger_summary", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    logger.message("info", f"version : {VERSION}, LOG_FILE : {LOG_FILE}", _EXTRA_())

    main()
