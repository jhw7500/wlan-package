"""8. 초당 통계 (retry 수, 제어트래픽 수, 전체 프레임 수)"""
from collections import Counter
from typing import List, Dict
from models import Frame, AnalysisSection
import time as _time


def analyze(frames: List[Frame], roles: Dict) -> AnalysisSection:
    lines = []
    if not frames:
        return AnalysisSection(title="8. 초당 통계", lines=["프레임 없음"], summary="없음")

    sec_total = Counter()
    sec_retry = Counter()
    sec_ctrl = Counter()
    sec_ctrl_retry = Counter()

    for f in frames:
        sec = int(f.epoch)
        sec_total[sec] += 1
        if f.retry:
            sec_retry[sec] += 1
        if f.is_control_traffic:
            sec_ctrl[sec] += 1
            if f.retry:
                sec_ctrl_retry[sec] += 1

    all_secs = sorted(sec_total.keys())

    lines.append(f"{'Time':>12} | {'Total':>6} | {'Retry':>6} | {'R%':>5} | {'Ctrl':>5} | {'Ctrl(R)':>7} | Bar(Retry)")
    lines.append("-" * 75)

    for sec in all_secs:
        ts = _time.strftime("%H:%M:%S", _time.localtime(sec))
        t = sec_total[sec]
        r = sec_retry[sec]
        c = sec_ctrl[sec]
        cr = sec_ctrl_retry[sec]
        rpct = r * 100.0 / t if t > 0 else 0
        bar = "#" * min(int(r / 5), 40)
        lines.append(f"{ts:>12} | {t:>6} | {r:>6} | {rpct:>4.0f}% | {c:>5} | {cr:>7} | {bar}")

    hotspots = [(sec, sec_retry[sec]) for sec in all_secs if sec_retry[sec] > 100]
    if hotspots:
        lines.append("")
        lines.append("Retry 핫스팟 (>100/s):")
        for sec, cnt in hotspots:
            ts = _time.strftime("%H:%M:%S", _time.localtime(sec))
            lines.append(f"  {ts}: {cnt} retries/s")

    summary = f"{len(all_secs)}초, 핫스팟 {len(hotspots) if hotspots else 0}건"
    return AnalysisSection(title="8. 초당 통계", lines=lines, summary=summary)
