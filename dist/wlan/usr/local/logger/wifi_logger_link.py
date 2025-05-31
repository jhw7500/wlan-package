import subprocess
import json
import time
import re
import sys
import os
from datetime import datetime
import logging
from sUTILS import Logger, _EXTRA_

VERSION = "0.0"
IFACE = ""
LOG_DIR = "/var/log/cantops/link"
MWLAN_LOG_PATH = "/proc/mwlan/adapter0/mlan0/log"

def save_db(db):
    def compact_lists(text):
        pattern = re.compile(r'\[\s*\n\s*((?:.+,\s*\n)+)\s*(.+?)\s*\]')
        return pattern.sub(lambda m: '[' + re.sub(r'\s+', ' ', m.group(1) + m.group(2)).strip() + ']', text)

    raw_json = json.dumps(db, indent=2)
    compacted_json = compact_lists(raw_json)
    
    with open(f"{LOG_DIR}/link.json", "w") as f:
        f.write(compacted_json)

def run_command(cmd):
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        return ""

def parse_mwlan_log():
    parsed = {}
    try:
        with open(MWLAN_LOG_PATH, "r") as f:
            for line in f:
                if "=" not in line:
                    continue
                key, value = map(str.strip, line.split("=", 1))
                if " " in value:
                    values = value.strip().split()
                    values = [int(v) for v in values if v.isdigit()]
                    parsed[key] = values
                else:
                    parsed[key] = int(value.strip())
    except Exception as e:
        parsed["error"] = str(e)

    return parsed

address = ""

def parse_iw_info(output):
    result = {}
    for line in output.splitlines():
        if "addr" in line:
            result["address"] = line.split()[-1]
        elif "channel" in line:
            match = re.search(r"channel\s+(\d+)\s+\(([\d]+) MHz\),\s+width:\s+([\d]+ MHz)", line)
            if match:
                result["channel"] = int(match.group(1))
                result["frequency"] = int(match.group(2))
                result["width"] = match.group(3)
        elif "txpower" in line:
            result["txpower"] = line.split()[-2]  # Remove dBm
            
    return result

def parse_station_dump(output):
    global address
    result = {}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Station "):
            result["address"] = stripped.split("Station")[1].split("(")[0].strip()
            if result["address"] != address:
                logger.message("notice", f"{IFACE} AP changed: {address} -> {result['address']}", _EXTRA_())
            address = result["address"]
            continue
        key_value = stripped.split(":", 1)
        if len(key_value) == 2:
            key = key_value[0].strip().replace(" ", "_")
            value = key_value[1].strip()
            result[key] = value
    return result


def main():
    while True:
        if os.path.exists(f"/sys/class/net/{IFACE}"):
            info_out = run_command(["iw", IFACE, "info"])
            station_out = run_command(["iw", IFACE, "station", "dump"])

            # 파싱은 출력이 유효할 때만
            info_data = parse_iw_info(info_out) if info_out else {}
            station_data = parse_station_dump(station_out) if station_out else {}

            data = {
                "date" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "info": info_data,
                "station_dump": station_data,
                "mwlan_log": parse_mwlan_log()
            }

            os.makedirs(LOG_DIR, exist_ok=True)
            save_db(data)
            #with open(f"{LOG_DIR}/link.json", "w") as f:
            #    json.dump(data, f, indent=4)
        else:
            #print(f"[WARN] Interface {IFACE} not found")
            logger.message("warn", f"{IFACE} is not found", _EXTRA_())
            time.sleep(5)

        time.sleep(1)

if __name__ == "__main__":
    logger = Logger(app_name="logger_link", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    
    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]
    
    LOG_DIR = f"/var/log/cantops/link/{IFACE}"
    logger.message("notice", f"VERSION : {VERSION}, LOG_FILE : {LOG_DIR}/link.json", _EXTRA_())

    if IFACE == "mlan0" :
        MWLAN_LOG_PATH = "/proc/mwlan/adapter0/mlan0/log"
    elif IFACE == "mlan1" :
        MWLAN_LOG_PATH = "/proc/mwlan/adapter1/mlan1/log"
    else:
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())
        sys.exit(1)
        
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    main()
