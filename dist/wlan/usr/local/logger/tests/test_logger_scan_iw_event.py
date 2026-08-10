"""iw event(nl80211) 스캔 이벤트 경로 테스트.

dmesg 경로는 COMPLETED 에 iface 가 없어 소유를 시간창으로 추측해야 했고, DBDC 에서
두 iface 스캔이 위상 고정(온타겟 실측: mlan1 START 후 ~7-11s 뒤 mlan0 START, 각 60s
주기)되면 mlan0 의 COMPLETED 가 100% 폐기돼 ap.log 가 영구 동결됐다(실측: 폐기 241건,
ap.log 3시간 2분 정지, `wifi 0 roam 0` 불능).

iw event 는 줄마다 iface 가 찍혀 추측이 아예 필요 없다. 아래 IW_LINES 는 그 장비에서
`iw event -t` 로 받은 실제 출력(cts-wlan, iw 6.9-12, moal, mlan0=5G/mlan1=2.4G)이다."""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_scan

wifi_logger_scan.logger = MagicMock()

# 온타겟 실측 캡처 (iw event -t). mlan0 finished 2건 / mlan1 finished 3건.
IW_LINES = [
    "1785957049.710015: mlan1 (phy #1): scan started\n",
    "1785957049.841422: mlan1 (phy #1): scan finished: 2412,\n",
    "1785957060.444730: mlan0 (phy #0): scan started\n",
    "1785957060.708500: mlan0 (phy #0): scan finished: 5180 5240,\n",
    "1785957110.174799: mlan1 (phy #1): scan started\n",
    "1785957110.305828: mlan1 (phy #1): scan finished: 2412,\n",
    "1785957121.038676: mlan0 (phy #0): scan started\n",
    "1785957121.302736: mlan0 (phy #0): scan finished: 5180 5240,\n",
    "1785957170.727395: mlan1 (phy #1): scan started\n",
    "1785957170.859492: mlan1 (phy #1): scan finished: 2412,\n",
]


# ---- classify_iw_event_line 단위 ----

def test_classify_own_finished():
    assert wifi_logger_scan.classify_iw_event_line(IW_LINES[3], "mlan0") == "finished"


def test_classify_own_started():
    assert wifi_logger_scan.classify_iw_event_line(IW_LINES[2], "mlan0") == "started"


def test_classify_other_iface_is_none():
    """핵심: 타 iface 줄은 우리 관점에서 아예 None — dmesg 처럼 모호하지 않다."""
    assert wifi_logger_scan.classify_iw_event_line(IW_LINES[1], "mlan0") is None
    # 대칭
    assert wifi_logger_scan.classify_iw_event_line(IW_LINES[3], "mlan1") is None
    assert wifi_logger_scan.classify_iw_event_line(IW_LINES[1], "mlan1") == "finished"


def test_classify_without_timestamp_prefix():
    """`-t` 없이 실행하면 epoch 접두가 없다 — 두 형태 모두 받아야 한다."""
    assert wifi_logger_scan.classify_iw_event_line(
        "mlan0 (phy #0): scan finished: 5180 5240,\n", "mlan0") == "finished"


def test_classify_aborted():
    assert wifi_logger_scan.classify_iw_event_line(
        "1785957060.7: mlan0 (phy #0): scan aborted\n", "mlan0") == "aborted"


def test_classify_unrelated_events_none():
    for line in ("1785957060.7: mlan0 (phy #0): connected to 04:ba:d6:ec:0b:08\n",
                 "1785957060.7: mlan0 (phy #0): del station 04:ba:d6:ec:0b:08\n",
                 "1785957060.7: regulatory domain change: set to KR\n"):
        assert wifi_logger_scan.classify_iw_event_line(line, "mlan0") is None


def test_classify_malformed_lines_none():
    """깨진/위조된 iw event 줄은 안전하게 None — 오분류 방지 (#163 OpenCode 리뷰)."""
    malformed = [
        "1785957060.7: mlan0 (phy #0 malformed\n",   # "): " 없음
        "1785957060.7: mlan0 scan started\n",        # phy 토큰 없음
        "mlan0 (phy #): scan started\n",             # phy 번호 없음
        "1785957060.7: mlan0 (phy #0): scan\n",      # 이벤트 이름 미완성
    ]
    for line in malformed:
        assert wifi_logger_scan.classify_iw_event_line(line, "mlan0") is None


# ---- iw_scan_event 스트림 ----

class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""

    def fileno(self):
        return 0


def _run_iw(lines, interface="mlan0", popen_exc=None, clock_values=None,
            idle_timeout=300):
    events = []
    proc = MagicMock()
    proc.stdout = _FakeStdout(lines)

    def popen(*a, **k):
        if popen_exc:
            raise popen_exc
        return proc

    if clock_values is not None:
        ticks = iter(clock_values)
        clock = lambda: next(ticks)
    else:
        clock = lambda: 0.0

    consumed = wifi_logger_scan.iw_scan_event(
        interface, lambda iface: events.append(iface),
        _popen=popen, _select=lambda r, w, x, t: (r, [], []),
        _clock=clock, idle_timeout=idle_timeout)
    return consumed, events


def test_only_own_finished_consumed():
    """실측 스트림에서 mlan0 관점: finished 2건만 소비, mlan1 줄은 전부 무시."""
    consumed, events = _run_iw(IW_LINES, "mlan0")
    assert consumed == 2
    assert events == ["mlan0", "mlan0"]


def test_symmetric_for_other_iface():
    """같은 스트림, mlan1 관점: finished 3건. DBDC 에서 서로를 굶기지 않는다."""
    consumed, events = _run_iw(IW_LINES, "mlan1")
    assert consumed == 3
    assert events == ["mlan1"] * 3


def test_started_and_aborted_do_not_trigger_dump():
    lines = ["mlan0 (phy #0): scan started\n", "mlan0 (phy #0): scan aborted\n"]
    consumed, events = _run_iw(lines, "mlan0")
    assert consumed == 0
    assert events == []


def test_stream_eof_returns_count():
    consumed, events = _run_iw(IW_LINES[:4], "mlan0")
    assert consumed == 1


def test_popen_failure_returns_none():
    """iw 바이너리 부재/실행 불가 → None → 호출측이 폴백을 판단한다."""
    consumed, events = _run_iw(IW_LINES, "mlan0", popen_exc=FileNotFoundError("iw"))
    assert consumed is None
    assert events == []


def test_idle_watchdog_returns_so_stream_restarts():
    """드라이버가 이벤트를 멈춰도 조용히 멎지 않는다 — 유휴 상한에서 반환.

    select 가 항상 not-ready 를 주는 상황(이벤트 없음)에서 시계가 상한을 넘으면
    반환해 호출측이 스트림을 재시작한다."""
    events = []
    proc = MagicMock()
    proc.stdout = _FakeStdout([])
    ticks = iter([0.0, 500.0])
    consumed = wifi_logger_scan.iw_scan_event(
        "mlan0", lambda iface: events.append(iface),
        _popen=lambda *a, **k: proc,
        _select=lambda r, w, x, t: ([], [], []),   # 항상 이벤트 없음
        _clock=lambda: next(ticks), idle_timeout=300)
    assert consumed == 0


# ---- scan_event_source 폴백 ----

def _run_source(monkeypatch, iw_results):
    """iw_scan_event 반환값 시퀀스를 주고 (폴백 호출목록, iw 호출횟수)를 반환."""
    fell_back = []
    calls = {"n": 0}
    results = iter(iw_results)

    def fake_iw(*a, **k):
        calls["n"] += 1
        return next(results)

    monkeypatch.setattr(wifi_logger_scan, "iw_scan_event", fake_iw)
    monkeypatch.setattr(wifi_logger_scan, "scan_event",
                        lambda iface, cb: fell_back.append(iface))
    wifi_logger_scan.scan_event_source(
        "mlan0", lambda iface: None, _clock=lambda: 0.0, _sleep=lambda s: None)
    return fell_back, calls["n"]


def test_unusable_primary_falls_back_immediately(monkeypatch):
    fell_back, calls = _run_source(monkeypatch, [None])
    assert fell_back == ["mlan0"]
    assert calls == 1          # 재시작 없이 즉시 폴백


def test_restart_cap_falls_back(monkeypatch):
    """재시작이 상한에 도달하면 dmesg 로 내려간다 — 무한 재시작 스핀 방지.

    호출 = 최초 1 + 재시작 cap 회. 그 다음 종료에서 폴백하므로 iw 는 cap+1 회
    호출된다(= 재시작 cap 회 수행 후 중단)."""
    cap = wifi_logger_scan.IW_EVENT_MAX_RESTARTS
    fell_back, calls = _run_source(monkeypatch, [1] * (cap + 1))
    assert fell_back == ["mlan0"]
    assert calls == cap + 1


def test_transient_ends_restart_until_primary_unusable(monkeypatch):
    """상한 이내의 종료는 재시작만 한다 — 폴백은 None(사용 불가)에서 일어난다.

    cap-1 회 종료는 모두 재시작으로 흡수되고, 마지막 None 에서만 폴백하므로
    iw 는 정확히 cap 회 호출된다(최초 1 + 재시작 cap-1)."""
    cap = wifi_logger_scan.IW_EVENT_MAX_RESTARTS
    fell_back, calls = _run_source(monkeypatch, [1] * (cap - 1) + [None])
    assert fell_back == ["mlan0"]
    assert calls == cap


def test_stream_is_reaped_on_exit():
    """종료한 `iw event` 를 반드시 wait 로 거둔다 — 재시작 루프의 좀비 누적 방지."""
    proc = MagicMock()
    proc.stdout = _FakeStdout(IW_LINES[:4])
    wifi_logger_scan.iw_scan_event(
        "mlan0", lambda iface: None, _popen=lambda *a, **k: proc,
        _select=lambda r, w, x, t: (r, [], []), _clock=lambda: 0.0)
    proc.terminate.assert_called_once()
    proc.wait.assert_called()
