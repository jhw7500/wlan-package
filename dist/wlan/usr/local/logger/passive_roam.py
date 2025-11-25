#!/usr/bin/env python3
import json
import os
import sys
import subprocess

SCAN_LOG = "/var/log/cantops/scan/mlan0/ap.log"
LINK_JSON = "/var/log/cantops/json/mlan0/link.json"
WIFI_IFACE = "mlan0"


def read_current_bssid(link_json_path=LINK_JSON):
    """Read current connected BSSID from link.json"""
    try:
        with open(link_json_path, "r") as f:
            data = json.load(f)
        return data.get("link", {}).get("address", "").strip().lower()
    except Exception:
        return ""


def parse_last_scan_block(scan_log_path=SCAN_LOG):
    """Parse the last scan block from ap.log"""
    if not os.path.exists(scan_log_path):
        return []

    with open(scan_log_path, "r") as f:
        lines = f.read().splitlines()

    if not lines:
        return []

    # Find last "[YYYY-..]" header
    start_idx = None
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith("[") and line.endswith("]"):
            start_idx = i
            break

    if start_idx is None:
        return []

    block_lines = lines[start_idx + 1:]

    aps = []
    for raw in block_lines:
        line = raw.strip()
        if not line or "|" not in line:
            continue
        if line.startswith("-") or line.startswith("#") or line.startswith("["):
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue

        try:
            aps.append({
                "idx": parts[0],
                "ch": int(parts[1]),
                "ss": int(parts[2]),
                "ld": int(parts[3]),
                "bssid": parts[4],
                "cap": parts[5],
                "ssid": parts[6],
            })
        except ValueError:
            continue

    return aps


def build_candidate_list():
    """
    Return (current_bssid, candidates)

    candidates: list of AP dicts with extra key "is_current"
    All APs are included, including the current one.
    Sorted by RSSI (ss) descending (higher is better).
    """
    current_bssid = read_current_bssid()
    aps = parse_last_scan_block()

    if not aps:
        print("No scan block found in ap.log")
        return current_bssid, []

    for ap in aps:
        bssid_low = ap["bssid"].strip().lower()
        ap["is_current"] = (bssid_low == current_bssid)

    # Sort by RSSI (higher is better; e.g. -40 > -50)
    aps.sort(key=lambda x: x["ss"], reverse=True)

    return current_bssid, aps


def print_candidate_list(current_bssid, candidates):
    print(f"Current BSSID: {current_bssid or '<unknown>'}")
    print("-" * 80)
    print("{:<3} {:>3} {:>4} {:>4} {:17} {:10} {}".format(
        "No", "ch", "ss", "ld", "bssid", "cap", "ssid"))
    print("-" * 80)

    for i, ap in enumerate(candidates, start=1):
        tag = "<current>" if ap.get("is_current") else ""
        print("{:<3} {:>3} {:>4} {:>4} {:17} {:10} {} {}".format(
            i, ap["ch"], ap["ss"], ap["ld"], ap["bssid"], ap["cap"], ap["ssid"], tag
        ))


def roam_to_ap(interface, ap, index_label=None):
    """
    Execute wpa_cli roam to the given AP.
    If ap["is_current"] is True, do not roam.
    """
    if ap.get("is_current"):
        print("\nSelected AP is the current AP. No roaming performed.")
        return 0

    bssid = ap["bssid"]
    cmd = ["wpa_cli", "-i", interface, "roam", bssid]

    print("\nSelected AP:")
    if index_label is not None:
        print(f"  No:   {index_label}")
    print(f"  BSSID: {bssid}")
    print(f"  SSID:  {ap['ssid']}")
    print(f"  CH:    {ap['ch']}")
    print(f"  RSSI:  {ap['ss']}")
    print("\nExecuting: " + " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print("\nwpa_cli output:")
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip())
        return result.returncode
    except FileNotFoundError:
        print("wpa_cli not found.")
        return 1


def roam_to_best_non_current(interface, candidates):
    """
    Find the best AP excluding the current one and roam to it.
    """
    others = [ap for ap in candidates if not ap.get("is_current")]
    if not others:
        print("No other APs available to roam.")
        return 1

    # Already sorted by ss in build_candidate_list, but sort again for safety
    others.sort(key=lambda x: x["ss"], reverse=True)
    best_ap = others[0]
    # index_label is just informational; not the original index
    return roam_to_ap(interface, best_ap, index_label="best_non_current")


def main():
    selected_index = None
    if len(sys.argv) >= 2:
        try:
            selected_index = int(sys.argv[1])
        except ValueError:
            print(f"Invalid argument: {sys.argv[1]} (must be integer)")
            sys.exit(1)

    current_bssid, candidates = build_candidate_list()
    if not candidates:
        sys.exit(1)

    # Always print the list (including current AP)
    print_candidate_list(current_bssid, candidates)

    # No argument: just show list, no roaming
    if selected_index is None:
        return

    # Argument 0: auto-roam to best AP excluding current
    if selected_index == 0:
        ret = roam_to_best_non_current(WIFI_IFACE, candidates)
        sys.exit(ret)

    # Argument > 0: roam to N-th AP in the printed list
    if selected_index < 0 or selected_index > len(candidates):
        print(f"Invalid index: {selected_index} (valid 0~{len(candidates)})")
        sys.exit(1)

    ap = candidates[selected_index - 1]
    ret = roam_to_ap(WIFI_IFACE, ap, index_label=selected_index)
    sys.exit(ret)


if __name__ == "__main__":
    main()
