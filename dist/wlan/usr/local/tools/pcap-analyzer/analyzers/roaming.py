"""4. 로밍 이벤트 탐지 (Auth → Assoc/Reassoc → EAPOL 4-Way)"""
from typing import List, Dict
from models import Frame, AnalysisSection
from detector import mac_name


def analyze(frames: List[Frame], roles: Dict) -> AnalysisSection:
    lines = []

    roaming_frames = [f for f in frames if f.is_roaming_related]
    if not roaming_frames:
        return AnalysisSection(title="4. 로밍 이벤트", lines=["로밍 관련 프레임 없음"], summary="로밍 없음")

    lines.append(f"로밍 관련 프레임: {len(roaming_frames)}건")
    lines.append("")
    lines.append(f"{'Frame':>6} | {'Timestamp':>15} | {'Type':>12} | {'R':>1} | {'TA→RA':>30} | {'Seq':>5}")
    lines.append("-" * 80)

    for f in roaming_frames:
        r = "R" if f.retry else " "
        ta = mac_name(f.ta, roles)
        ra = mac_name(f.ra, roles)
        lines.append(
            f"{f.number:>6} | {f.time_short:>15} | {f.subtype_name:>12} | {r} | "
            f"{ta:>13}→{ra:<13} | {f.seq:>5}"
        )

    # 로밍 시퀀스 감지
    lines.append("")
    lines.append("로밍 시퀀스 감지:")

    sta_macs = {m for m, r in roles.items() if r["role"] == "STA"}
    sequences = []
    auth_events = {}

    for f in roaming_frames:
        if f.subtype == "11" and f.ta in sta_macs:
            auth_events[f.ta] = f
        elif f.subtype in ("0", "2") and f.ta in sta_macs:
            if f.ta in auth_events:
                auth_f = auth_events[f.ta]
                gap = (f.epoch - auth_f.epoch) * 1000
                sequences.append({
                    "sta": f.ta,
                    "auth_fnum": auth_f.number,
                    "assoc_fnum": f.number,
                    "auth_ts": auth_f.time_short,
                    "assoc_type": f.subtype_name,
                    "ap": mac_name(f.ra, roles),
                    "gap_ms": gap,
                })

    if sequences:
        lines.append(f"{'#':>3} | {'STA':>15} | {'Auth→Assoc':>14} | {'Gap':>8} | {'AP':>15} | {'Type':>12}")
        lines.append("-" * 80)
        for i, s in enumerate(sequences):
            sta_name = mac_name(s["sta"], roles)
            lines.append(
                f"{i+1:>3} | {sta_name:>15} | #{s['auth_fnum']}→#{s['assoc_fnum']} | "
                f"{s['gap_ms']:>6.1f}ms | {s['ap']:>15} | {s['assoc_type']}"
            )
    else:
        lines.append("  완전한 로밍 시퀀스 없음 (부분적 이벤트만 존재)")

    summary = f"로밍 프레임 {len(roaming_frames)}건, 시퀀스 {len(sequences)}건"
    return AnalysisSection(title="4. 로밍 이벤트", lines=lines, summary=summary)
