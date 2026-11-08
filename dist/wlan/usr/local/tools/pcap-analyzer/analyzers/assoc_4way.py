"""13. 연결·4-way 판정 (#460)

AP→STA (재)연결 응답을 시도 단위로 묶고, 시도마다 응답 재전송, STA ACK,
EAPOL-Key M1~M4 첫 도착 시각으로 4-way 가 어디서 멈췄는지 판정한다.
판정은 "캡처에서 보이지 않음"을 뜻한다 — 모니터가 놓친 프레임도 없는 것으로 보인다.
"""

from dataclasses import dataclass, field
import bisect
import importlib
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import AnalysisSection as AnalysisSectionType
    from ..models import Frame as FrameType
else:
    AnalysisSectionType = Any
    FrameType = Any

try:
    from ..detector import mac_name
    from ..models import AnalysisSection
except (ImportError, ValueError):
    mac_name = importlib.import_module("detector").mac_name
    AnalysisSection = importlib.import_module("models").AnalysisSection

TITLE = "13. 연결·4-way 판정"
RESPONSE_SUBTYPES = {"1", "3"}       # AssocResp, ReassocResp
ACK_SUBTYPE = "29"
NEW_ATTEMPT_GAP_S = 0.5              # 이보다 떨어진 복사본은 새 시도
WINDOW_S = 10.0                      # 응답 뒤 M1~M4 를 기다리는 창
MESSAGES = ("1", "2", "3", "4")
VERDICT_BY_HIGHEST = {"4": "OK", "3": "STA_NO_M4", "2": "AP_NO_M3", "1": "STA_NO_M2", None: "AP_NO_M1"}
VERDICT_ORDER = ("OK", "AP_NO_M1", "STA_NO_M2", "AP_NO_M3", "STA_NO_M4", "ASSOC_REJECTED", "CAPTURE_END")
MAX_DETAIL_LINES = 50


@dataclass
class Attempt:
    ap: str
    sta: str
    first: FrameType
    status: str
    copies: List[FrameType] = field(default_factory=list)
    retransmissions: int = 0     # Retry=1 복사본 수
    acked: int = 0               # 다음 번호 프레임이 AP 를 받는 ACK 인 복사본 수
    ack_checked: int = 0         # 다음 번호 프레임이 캡처에 있는 복사본 수
    ack_unknown: int = 0         # 다음 번호 프레임이 캡처에 없는 복사본 수(필터·손실)
    msg_ms: Dict[str, float] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    verdict: str = ""


def _ack_state(frames: List[FrameType], index: int) -> Optional[bool]:
    """frames[index] 바로 다음 번호 프레임이 그 송신자를 받는 ACK 인지. 그 번호가 없으면 None."""
    copy = frames[index]
    if index + 1 >= len(frames):
        return None
    nxt = frames[index + 1]
    if nxt.number != copy.number + 1:
        return None
    return nxt.subtype == ACK_SUBTYPE and nxt.ra == copy.ta


def _continues(prev: Attempt, frame: FrameType) -> bool:
    """frame 이 prev 시도의 재전송 복사본인지(Retry=1, 같은 seq, 0.5초 이내)."""
    last = prev.copies[-1]
    return bool(frame.retry) and frame.seq == last.seq and frame.epoch - last.epoch <= NEW_ATTEMPT_GAP_S


def _verdict(attempt: Attempt, window_complete: bool) -> Tuple[str, List[str]]:
    if attempt.status not in ("", "0"):
        return "ASSOC_REJECTED", []
    seen = [m for m in MESSAGES if m in attempt.msg_ms]
    highest = seen[-1] if seen else None
    missing = [f"M{m}" for m in MESSAGES if highest and m < highest and m not in attempt.msg_ms]
    if highest != "4" and not window_complete:
        return "CAPTURE_END", missing
    return VERDICT_BY_HIGHEST[highest], missing


def collect_attempts(frames: List[FrameType]) -> List[Attempt]:
    """응답 복사본을 시도로 묶고, 시도마다 ACK·M1~M4·판정을 채운다(frames 는 캡처 순서)."""
    attempts: List[Attempt] = []
    current: Dict[Tuple[str, str], Attempt] = {}
    eapol: List[FrameType] = []
    for index, frame in enumerate(frames):
        if frame.eapol_msgnr in MESSAGES:
            eapol.append(frame)
            continue
        if frame.subtype not in RESPONSE_SUBTYPES:
            continue
        key = (frame.ta, frame.ra)
        attempt = current.get(key)
        if attempt is None or not _continues(attempt, frame):
            attempt = Attempt(ap=frame.ta, sta=frame.ra, first=frame, status=frame.status_code)
            attempts.append(attempt)
            current[key] = attempt
        attempt.copies.append(frame)
        if frame.retry:
            attempt.retransmissions += 1
        state = _ack_state(frames, index)
        if state is None:
            attempt.ack_unknown += 1
        else:
            attempt.ack_checked += 1
            attempt.acked += int(state)

    last_epoch = frames[-1].epoch if frames else 0.0
    eapol_numbers = [f.number for f in eapol]
    for position, attempt in enumerate(attempts):
        following = next((a for a in attempts[position + 1:] if a.sta == attempt.sta), None)
        end_number = following.first.number if following else None
        end_epoch = attempt.first.epoch + WINDOW_S
        for frame in eapol[bisect.bisect_right(eapol_numbers, attempt.first.number):]:
            if end_number is not None and frame.number >= end_number:
                break
            if frame.epoch > end_epoch:
                continue
            msg = frame.eapol_msgnr
            expected = (attempt.ap, attempt.sta) if msg in ("1", "3") else (attempt.sta, attempt.ap)
            if (frame.ta, frame.ra) != expected:
                continue
            attempt.msg_ms.setdefault(msg, (frame.epoch - attempt.first.epoch) * 1000.0)
            if "4" in attempt.msg_ms:
                break
        window_complete = following is not None or last_epoch - attempt.first.epoch >= WINDOW_S
        attempt.verdict, attempt.missing = _verdict(attempt, window_complete)
    return attempts


def _ms(value: Optional[float]) -> str:
    return "-" if value is None else f"+{value:.0f}ms"


def analyze(
    frames: List[FrameType], roles: Dict[str, Dict[str, Any]], index: Any = None
) -> AnalysisSectionType:
    del index
    attempts = collect_attempts(frames)
    if not attempts:
        return AnalysisSection(title=TITLE, lines=["(재)연결 응답 프레임 없음"], summary="연결 응답 없음")

    counts = {verdict: 0 for verdict in VERDICT_ORDER}
    retx_hist: Dict[int, int] = {}
    for attempt in attempts:
        counts[attempt.verdict] += 1
        retx_hist[attempt.retransmissions] = retx_hist.get(attempt.retransmissions, 0) + 1
    verdict_text = ", ".join(f"{v} {counts[v]}" for v in VERDICT_ORDER if counts[v])
    retx_avg = sum(a.retransmissions for a in attempts) / len(attempts)
    acked = sum(a.acked for a in attempts)
    checked = sum(a.ack_checked for a in attempts)
    unknown = sum(a.ack_unknown for a in attempts)

    lines = [
        f"(재)연결 응답 {len(attempts)}건 (STA {len({a.sta for a in attempts})}, AP {len({a.ap for a in attempts})})",
        f"판정: {verdict_text}",
        "응답 재전송(Retry=1): " + ", ".join(f"{k}회 {retx_hist[k]}건" for k in sorted(retx_hist)),
        f"STA ACK(응답 바로 다음 프레임): {acked}/{checked}"
        + (f", 판정 불가 {unknown} (다음 번호 프레임이 캡처에 없음 — --mac/--sta 필터면 ACK 가 빠진다)" if unknown else ""),
        "",
        f"{'#':>3} | {'Frame':>8} | {'Time':>15} | {'STA':>8} | {'AP':>8} | {'Type':<11} | "
        f"{'Status':>6} | {'Retx':>4} | {'ACK':>5} | {'M1':>7} {'M2':>7} {'M3':>7} {'M4':>7} | 판정",
        "-" * 120,
    ]
    for number, attempt in enumerate(attempts[:MAX_DETAIL_LINES], start=1):
        ack = f"{attempt.acked}/{attempt.ack_checked}" + ("?" if attempt.ack_unknown else "")
        msgs = " ".join(f"{_ms(attempt.msg_ms.get(m)):>7}" for m in MESSAGES)
        note = f" (캡처 누락 {','.join(attempt.missing)})" if attempt.missing else ""
        lines.append(
            f"{number:>3} | #{attempt.first.number:<7} | {attempt.first.time_short:>15} | "
            f"{mac_name(attempt.sta, roles):>8} | {mac_name(attempt.ap, roles):>8} | "
            f"{attempt.first.subtype_name:<11} | {attempt.status or '-':>6} | {attempt.retransmissions:>4} | "
            f"{ack:>5} | {msgs} | {attempt.verdict}{note}"
        )
    if len(attempts) > MAX_DETAIL_LINES:
        lines.append(f"... 외 {len(attempts) - MAX_DETAIL_LINES}건")
    lines += [
        "",
        "판정 뜻(캡처에서 보이는 것 기준): OK=M4까지, AP_NO_M1=응답 뒤 M1 없음, STA_NO_M2=M1 뒤 M2 없음,",
        "  AP_NO_M3=M2 뒤 M3 없음, STA_NO_M4=M3 뒤 M4 없음, ASSOC_REJECTED=상태≠0,",
        f"  CAPTURE_END=캡처가 창({WINDOW_S:.0f}초) 안에서 끝남. '캡처 누락'은 더 높은 메시지가 보여 받았음이 확실한 메시지.",
    ]

    summary = f"연결 응답 {len(attempts)}건: {verdict_text}, 응답 재전송 평균 {retx_avg:.1f}회"
    return AnalysisSection(title=TITLE, lines=lines, summary=summary)
