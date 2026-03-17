"""12. AP 간 로밍 성능 비교"""
from typing import List, Dict
from models import Frame, AnalysisSection
from detector import mac_name


def analyze(frames: List[Frame], roles: Dict) -> AnalysisSection:
    lines = []
    ap_macs = [m for m, r in roles.items() if r["role"] == "AP"]

    if len(ap_macs) < 2:
        return AnalysisSection(
            title="12. AP 비교", lines=["AP 2대 미만 — 비교 불가"],
            summary="비교 불가")

    ap_stats = {}
    for ap in ap_macs:
        name = mac_name(ap, roles)
        tx = [f for f in frames if f.ta == ap]
        rx = [f for f in frames if f.ra == ap]
        all_f = tx + rx
        total = len(all_f)
        retries = sum(1 for f in all_f if f.retry)
        rssis = [f.rssi_first for f in all_f if f.rssi_first is not None]

        roaming_to = sum(1 for f in frames
                        if f.subtype in ("0", "2") and f.ra == ap)

        ap_stats[ap] = {
            "name": name,
            "total": total,
            "retry": retries,
            "retry_pct": retries * 100.0 / total if total else 0,
            "rssi_avg": sum(rssis) / len(rssis) if rssis else None,
            "rssi_min": min(rssis) if rssis else None,
            "roaming_to": roaming_to,
        }

    lines.append(f"{'AP':>15} | {'프레임':>7} | {'Retry%':>7} | {'RSSI avg':>9} | "
                 f"{'RSSI min':>9} | {'로밍 수신':>8}")
    lines.append("-" * 70)
    for ap in ap_macs:
        s = ap_stats[ap]
        rssi_avg = f"{s['rssi_avg']:.0f}" if s["rssi_avg"] else "-"
        rssi_min = f"{s['rssi_min']}" if s["rssi_min"] else "-"
        lines.append(
            f"{s['name']:>15} | {s['total']:>7} | {s['retry_pct']:>6.1f}% | "
            f"{rssi_avg:>9} | {rssi_min:>9} | {s['roaming_to']:>8}")

    lines.append("")
    best = min(ap_stats.values(), key=lambda s: s["retry_pct"])
    worst = max(ap_stats.values(), key=lambda s: s["retry_pct"])
    diff = worst["retry_pct"] - best["retry_pct"]
    if diff > 10:
        lines.append(f"!! {worst['name']}의 retry rate({worst['retry_pct']:.1f}%)가 "
                     f"{best['name']}({best['retry_pct']:.1f}%)보다 현저히 높음")
    else:
        lines.append(f"AP 간 retry rate 차이 미미 "
                     f"({best['retry_pct']:.1f}% ~ {worst['retry_pct']:.1f}%)")

    summary = f"AP {len(ap_macs)}대 비교"
    return AnalysisSection(title="12. AP 비교", lines=lines, summary=summary)
