"""9. 로밍 전후 영향 분석 — 각 로밍 이벤트의 retry/RSSI/ping 변화를 측정"""
from typing import List, Dict
from models import Frame, AnalysisSection
from detector import mac_name

WINDOW_SEC = 3


def _find_roaming_events(frames: List[Frame], roles: Dict) -> List[Dict]:
    sta_macs = {m for m, r in roles.items() if r["role"] == "STA"}
    events = []
    auth_by_sta = {}

    for f in frames:
        if not f.is_roaming_related:
            continue
        if f.subtype == "11" and f.ta in sta_macs:
            auth_by_sta[f.ta] = f
        elif f.subtype in ("0", "2") and f.ta in sta_macs:
            auth_f = auth_by_sta.get(f.ta)
            if auth_f:
                events.append({
                    "sta": f.ta,
                    "ap": f.ra,
                    "auth_frame": auth_f,
                    "assoc_frame": f,
                    "epoch": auth_f.epoch,
                    "handshake_ms": (f.epoch - auth_f.epoch) * 1000,
                })
    return events


def _window_stats(frames: List[Frame], sta: str, center: float,
                  before_sec: float, after_sec: float) -> Dict:
    before = [f for f in frames
              if center - before_sec <= f.epoch < center
              and (f.ta == sta or f.ra == sta)]
    after = [f for f in frames
             if center < f.epoch <= center + after_sec
             and (f.ta == sta or f.ra == sta)]

    def calc(flist):
        if not flist:
            return {"total": 0, "retry": 0, "retry_pct": 0.0, "rssi_avg": None}
        retries = sum(1 for f in flist if f.retry)
        rssis = [f.rssi_first for f in flist if f.rssi_first is not None]
        return {
            "total": len(flist),
            "retry": retries,
            "retry_pct": retries * 100.0 / len(flist),
            "rssi_avg": sum(rssis) / len(rssis) if rssis else None,
        }

    return {"before": calc(before), "after": calc(after)}


def _ping_around(frames: List[Frame], sta: str, center: float,
                 window: float) -> Dict:
    start = center - window
    end = center + window
    requests = {}
    matched = 0

    for f in frames:
        if f.epoch < start or f.epoch > end:
            continue
        if f.is_icmp_request and not f.retry and (f.ta == sta or f.ra == sta):
            requests[(f.ip_src, f.ip_dst)] = f
        elif f.is_icmp_reply and (f.ta == sta or f.ra == sta):
            key = (f.ip_dst, f.ip_src)
            if key in requests:
                del requests[key]
                matched += 1

    lost = len(requests)
    return {"matched": matched, "lost": lost}


def analyze(frames: List[Frame], roles: Dict) -> AnalysisSection:
    lines = []
    events = _find_roaming_events(frames, roles)

    if not events:
        return AnalysisSection(
            title="9. 로밍 영향 분석", lines=["로밍 이벤트 없음"], summary="로밍 없음")

    lines.append(f"총 {len(events)}건의 로밍 이벤트 (전후 {WINDOW_SEC}초 분석)")
    lines.append("")

    problem_count = 0
    for i, ev in enumerate(events):
        sta = mac_name(ev["sta"], roles)
        ap = mac_name(ev["ap"], roles)
        stats = _window_stats(frames, ev["sta"], ev["epoch"], WINDOW_SEC, WINDOW_SEC)
        ping = _ping_around(frames, ev["sta"], ev["epoch"], WINDOW_SEC)

        b = stats["before"]
        a = stats["after"]
        has_problem = (a["retry_pct"] > 50 or ping["lost"] > 0)
        if has_problem:
            problem_count += 1

        marker = "★" if has_problem else " "
        lines.append(f"[로밍 #{i+1}] {marker} {sta}: →{ap}  {ev['auth_frame'].time_short}")
        lines.append(f"  ├─ 핸드셰이크: {ev['handshake_ms']:.0f}ms "
                     f"(#{ev['auth_frame'].number}→#{ev['assoc_frame'].number})")

        if b["total"] > 0:
            rssi_b = f", RSSI {b['rssi_avg']:.0f}dBm" if b["rssi_avg"] else ""
            lines.append(f"  ├─ 전 {WINDOW_SEC}초: retry {b['retry_pct']:.0f}% "
                         f"({b['retry']}/{b['total']}){rssi_b}")
        else:
            lines.append(f"  ├─ 전 {WINDOW_SEC}초: 프레임 없음")

        if a["total"] > 0:
            rssi_a = f", RSSI {a['rssi_avg']:.0f}dBm" if a["rssi_avg"] else ""
            lines.append(f"  ├─ 후 {WINDOW_SEC}초: retry {a['retry_pct']:.0f}% "
                         f"({a['retry']}/{a['total']}){rssi_a}")
        else:
            lines.append(f"  ├─ 후 {WINDOW_SEC}초: 프레임 없음")

        loss_str = f"LOSS {ping['lost']}건 ★" if ping["lost"] else "손실 없음"
        lines.append(f"  └─ Ping: 성공 {ping['matched']}, {loss_str}")
        lines.append("")

    summary = f"로밍 {len(events)}건, 문제 {problem_count}건"
    return AnalysisSection(title="9. 로밍 영향 분석", lines=lines, summary=summary)
