import os
import re
import json
import subprocess
from datetime import datetime

SCAN_JSON_PATH = "/var/log/cantops/scan/mlan0/iw_scan_db.json"

def run_scan_dump():
    try:
        result = subprocess.check_output(["iw", "mlan0", "scan", "dump"], text=True)
        return result
    except subprocess.CalledProcessError as e:
        return ""

def parse_scan(text):
    bss_blocks = re.split(r"^BSS ([0-9a-f:]{17})\(on mlan0\)", text, flags=re.MULTILINE)
    entries = {}
    for i in range(1, len(bss_blocks), 2):
        bssid = bss_blocks[i].strip()
        block = bss_blocks[i + 1]

        def extract(pattern):
            match = re.search(pattern, block)
            return match.group(1).strip() if match else None

        ssid = extract(r"SSID:\s*(.+)")
        signal = extract(r"signal:\s*(-?\d+\.\d+)")
        freq = extract(r"freq:\s*(\d+)")
        channel = extract(r"DS Parameter set: channel (\d+)")
        capabilities = re.findall(r"\*\s+(.*?)\n", block)

        entries[bssid] = {
            "bssid": bssid,
            "ssid": ssid,
            "signal": float(signal) if signal else None,
            "freq": int(freq) if freq else None,
            "channel": int(channel) if channel else None,
            "capabilities": capabilities,
            "last_seen": datetime.now().isoformat()
        }

    return entries

def load_existing():
    if os.path.exists(SCAN_JSON_PATH):
        with open(SCAN_JSON_PATH, "r") as f:
            return json.load(f)
    return {}

def merge_db(old_db, new_db):
    updated = old_db.copy()
    for bssid, data in new_db.items():
        updated[bssid] = data
    return updated

def save_db(db):
    with open(SCAN_JSON_PATH, "w") as f:
        json.dump(db, f, indent=2)

def main():
    scan_output = run_scan_dump()
    if not scan_output:
        print("No scan result.")
        return

    new_entries = parse_scan(scan_output)
    existing_db = load_existing()
    merged = merge_db(existing_db, new_entries)
    save_db(merged)

if __name__ == "__main__":
    main()
