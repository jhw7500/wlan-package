"""pcap 프레임에서 AP/STA MAC 역할을 자동 감지한다."""
from typing import Dict, List
from collections import Counter
from models import Frame

BROADCAST = "ff:ff:ff:ff:ff:ff"
AP_SUBTYPES = {"5", "8"}  # ProbeResp, Beacon


def detect_roles(frames: List[Frame]) -> Dict[str, Dict]:
    mac_counts = Counter()
    ap_macs = set()

    for f in frames:
        if f.ta and f.ta != BROADCAST:
            mac_counts[f.ta] += 1
        if f.ra and f.ra != BROADCAST:
            mac_counts[f.ra] += 1
        if f.subtype in AP_SUBTYPES and f.ta and f.ta != BROADCAST:
            ap_macs.add(f.ta)

    # AssocResp / ReassocResp 를 보내는 MAC도 AP
    for f in frames:
        if f.subtype in ("1", "3") and f.ta and f.ta != BROADCAST:
            ap_macs.add(f.ta)

    roles = {}
    ap_idx = 1
    sta_idx = 1

    for mac in sorted(ap_macs, key=lambda m: mac_counts.get(m, 0), reverse=True):
        short = mac[-5:].replace(":", "")
        roles[mac] = {"role": "AP", "name": f"AP{ap_idx}({short})", "count": mac_counts[mac]}
        ap_idx += 1

    for mac in sorted(mac_counts, key=lambda m: mac_counts[m], reverse=True):
        if mac in roles or mac == BROADCAST:
            continue
        if mac_counts[mac] < 5:
            continue
        short = mac[-5:].replace(":", "")
        roles[mac] = {"role": "STA", "name": f"STA{sta_idx}({short})", "count": mac_counts[mac]}
        sta_idx += 1

    return roles


def mac_name(mac: str, roles: Dict[str, Dict]) -> str:
    if mac == BROADCAST:
        return "BCAST"
    if mac in roles:
        return roles[mac]["name"]
    return mac[-5:] if mac else "?"
