#!/usr/bin/env python3
import json
import os
import sys
import subprocess

WIFI_IFACE = "mlan0"
SCAN_LOG = f"/var/log/cantops/scan/{WIFI_IFACE}/ap.log"
LINK_JSON = f"/var/log/cantops/json/{WIFI_IFACE}/link.json"
WIFI_INIT_CONF_JSON = "/usr/local/etc/wifi_init_conf.json"


def read_current_bssid(link_json_path=LINK_JSON):
    """Read current connected BSSID from link.json"""
    try:
        with open(link_json_path, "r") as f:
            data = json.load(f)
        return data.get("link", {}).get("address", "").strip().lower()
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return ""


def read_current_ssid(link_json_path=LINK_JSON):
    """Read current connected SSID from link.json"""
    try:
        with open(link_json_path, "r") as f:
            data = json.load(f)
        return data.get("info", {}).get("ssid", "").strip()
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return ""


def load_extra_ssids(iface, conf_path=WIFI_INIT_CONF_JSON):
    """roaming.extra_ssids 로드 (다중 SSID 로밍 허용 목록). 없거나 형식오류면 []."""
    try:
        with open(conf_path, "r") as f:
            roam = json.load(f).get(iface, {}).get("roaming", {})
        extra = roam.get("extra_ssids")
        if isinstance(extra, list):
            return [str(s).strip() for s in extra if str(s).strip()]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AttributeError, TypeError):
        pass
    return []


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
    Return (current_bssid, current_ssid, candidates)

    candidates: list of AP dicts with extra key "is_current"
    All APs are included, including the current one.
    Filtered by allowed SSIDs (current + roaming.extra_ssids).
    Sorted by RSSI (ss) descending (higher is better).
    """
    current_bssid = read_current_bssid(LINK_JSON)
    current_ssid = read_current_ssid(LINK_JSON)
    extra_ssids = load_extra_ssids(WIFI_IFACE)
    allowed = ([current_ssid] if current_ssid else []) + [
        s for s in extra_ssids if s and s != current_ssid
    ]
    aps = parse_last_scan_block(SCAN_LOG)

    if not aps:
        print("No scan block found in ap.log")
        return current_bssid, current_ssid, []

    for ap in aps:
        bssid_low = ap["bssid"].strip().lower()
        ap["is_current"] = (bssid_low == current_bssid)

    # Filter by allowed SSIDs (current + roaming.extra_ssids)
    if allowed:
        aps = [ap for ap in aps if ap["ssid"] in allowed]

    # Sort by RSSI (higher is better; e.g. -40 > -50)
    aps.sort(key=lambda x: x["ss"], reverse=True)

    return current_bssid, current_ssid, aps


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


def roam_to_ap(interface, ap, index_label=None, current_ssid=None):
    """
    Roam to the given AP.
    - 같은 SSID: wpa_cli roam <bssid> (무중단)
    - 다른 SSID: wifi <iface> connect <ssid> <ch> (conf ssid 교체→reconfigure→reassociate, 재연결).
      wpa_cli roam은 같은 network 블록(SSID) 내 BSS만 전환하므로 다른 SSID는 connect로 처리.
    If ap["is_current"] is True, do not roam.
    """
    if ap.get("is_current"):
        print("\nSelected AP is the current AP. No roaming performed.")
        return 0

    bssid = ap["bssid"]
    cross_ssid = bool(current_ssid) and bool(ap.get("ssid")) and ap["ssid"] != current_ssid
    if cross_ssid:
        # freq 생략: wifi connect에 단일 freq를 주면 conf scan_freq가 그 채널로 collapse됨
        cmd = ["/usr/local/bin/wifi", interface, "connect", ap["ssid"]]
    else:
        cmd = ["wpa_cli", "-i", interface, "roam", bssid]

    print(f"\nSelected AP:")
    if index_label is not None:
        print(f"  No:   {index_label}")
    print(f"  BSSID: {bssid}")
    print(f"  SSID:  {ap['ssid']}")
    print(f"  CH:    {ap['ch']}")
    print(f"  RSSI:  {ap['ss']}")
    print(f"  MODE:  {'connect (cross-SSID)' if cross_ssid else 'roam (same-SSID)'}")
    print(f"\nExecuting: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""
        print(f"\noutput: {stdout} {stderr}".rstrip())
        # 한줄 요약 (wifi_periodic_roam.sh에서 grep "ROAM_RESULT"로 추출)
        print(f"ROAM_RESULT: {bssid} ch:{ap['ch']} rssi:{ap['ss']} -> {stdout or stderr or 'unknown'}")
        return result.returncode
    except subprocess.TimeoutExpired:
        print(f"ROAM_RESULT: {bssid} ch:{ap['ch']} rssi:{ap['ss']} -> timeout")
        return 1
    except FileNotFoundError:
        print("roam command not found.")
        return 1


def roam_to_best_non_current(interface, candidates, current_ssid=None):
    """
    Find the best AP excluding the current one and roam to it.
    Candidates are filtered by allowed SSIDs (current + extra_ssids) in build_candidate_list.
    """
    others = [ap for ap in candidates if not ap.get("is_current")]
    if not others:
        print("No other APs available to roam.")
        return 1

    others.sort(key=lambda x: x["ss"], reverse=True)
    best_ap = others[0]
    return roam_to_ap(interface, best_ap, index_label="best_non_current", current_ssid=current_ssid)


def main():
    global WIFI_IFACE, SCAN_LOG, LINK_JSON

    import argparse
    parser = argparse.ArgumentParser(description="Passive roaming tool")
    parser.add_argument("index", nargs="?", type=int, default=None,
                        help="0=best auto, N=roam to Nth AP (RSSI order)")
    parser.add_argument("--iface", default="mlan0",
                        choices=["mlan0", "mlan1"],
                        help="Interface name (default: mlan0)")
    args = parser.parse_args()

    WIFI_IFACE = args.iface
    SCAN_LOG = f"/var/log/cantops/scan/{WIFI_IFACE}/ap.log"
    LINK_JSON = f"/var/log/cantops/json/{WIFI_IFACE}/link.json"

    print(f"Allowed SSIDs: current={read_current_ssid(LINK_JSON)} extra_ssids={load_extra_ssids(WIFI_IFACE)}")

    current_bssid, current_ssid, candidates = build_candidate_list()
    if not candidates:
        sys.exit(1)

    # Always print the list (including current AP)
    print_candidate_list(current_bssid, candidates)

    # No argument: just show list, no roaming
    if args.index is None:
        return

    # Argument 0: auto-roam to best AP excluding current
    if args.index == 0:
        ret = roam_to_best_non_current(WIFI_IFACE, candidates, current_ssid)
        sys.exit(ret)

    # Argument > 0: roam to N-th AP in the printed list
    if args.index < 0 or args.index > len(candidates):
        print(f"Invalid index: {args.index} (valid 0~{len(candidates)})")
        sys.exit(1)

    ap = candidates[args.index - 1]
    ret = roam_to_ap(WIFI_IFACE, ap, index_label=args.index, current_ssid=current_ssid)
    sys.exit(ret)


if __name__ == "__main__":
    main()
