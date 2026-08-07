"""dmesg 스캔 이벤트 타 iface 혼선 가드 테스트.

dmesg 는 단일 스트림이고 moal 의 COMPLETED 라인에는 iface 가 없다(온타겟 실측
2026-08-07: `wlan: mlan0 START SCAN` / `wlan: SCAN COMPLETED: scanned AP count=15`).
mlan0/mlan1 로거 인스턴스 동시 활성 시 타 iface 의 COMPLETED 를 오소비할 수 있어,
COMPLETED 시점에 최근 타 iface START 와의 시간창(AMBIGUOUS_SCAN_WINDOW_S)으로
소유 모호를 판정해 버린다 — 즉시 리셋 방식은 타 START 가 자기 START 보다 먼저 온
(나중에 시작한 관찰자) 경우를 못 지켰다(#158 Codex 리뷰). mlan0 단독 출하
기본값에서는 타 iface START 자체가 없어 동작 변화 0."""
import os
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_scan

wifi_logger_scan.logger = MagicMock()

# 온타겟 실측 포맷(dmesg 타임스탬프 프리픽스 포함)
OWN_START = "[  123.456] wlan: mlan0 START SCAN"
OTHER_START = "[  123.789] wlan: mlan1 START SCAN"
COMPLETED = "[  124.000] wlan: SCAN COMPLETED: scanned AP count=15"


# ---- classify_scan_line 단위 ----

def test_classify_own_start():
    assert wifi_logger_scan.classify_scan_line(OWN_START, "mlan0") == "own_start"


def test_classify_other_start():
    assert wifi_logger_scan.classify_scan_line(OTHER_START, "mlan0") == "other_start"
    # 대칭: mlan1 관점에서는 mlan0 START 가 타 iface
    assert wifi_logger_scan.classify_scan_line(OWN_START, "mlan1") == "other_start"


def test_classify_completed_has_no_iface():
    """실측 근거: COMPLETED 라인에는 iface 가 없어 어느 관점에서든 completed."""
    assert wifi_logger_scan.classify_scan_line(COMPLETED, "mlan0") == "completed"
    assert wifi_logger_scan.classify_scan_line(COMPLETED, "mlan1") == "completed"


def test_classify_unrelated_lines_none():
    assert wifi_logger_scan.classify_scan_line("usb 1-1: new device", "mlan0") is None
    assert wifi_logger_scan.classify_scan_line(
        "[  99.9] wlan: mlan0 assoc done", "mlan0") is None


# ---- scan_event 시퀀스 (시간창 판정) ----

def _run_scan_event(monkeypatch, lines, interface="mlan0", clock_values=None):
    """fake dmesg 라인 시퀀스를 scan_event 에 먹여 콜백 발화 목록을 반환.

    fake stdout(iterator) 소진 시 for 루프가 끝나 함수가 반환된다(실환경에선
    dmesg --follow 로 무한 블로킹). _clock 은 other_start 기록과 completed 판정
    2곳에서만 읽힌다 — clock_values 미지정 시 1ms 씩 증가하는 가짜 시계를 써서
    같은 라운드 내 이벤트가 항상 시간창 안에 들어온다."""
    events = []
    fake_proc = MagicMock()
    fake_proc.__enter__.return_value = fake_proc
    fake_proc.stdout = iter(lines)
    monkeypatch.setattr(wifi_logger_scan, "get_last_dmesg_line_count", lambda: 0)
    monkeypatch.setattr(
        wifi_logger_scan.subprocess, "Popen", lambda *a, **k: fake_proc)
    if clock_values is not None:
        ticks = iter(clock_values)
        clock = lambda: next(ticks)
    else:
        state = {"t": 0.0}

        def clock():
            state["t"] += 0.001
            return state["t"]

    wifi_logger_scan.scan_event(
        interface, lambda iface: events.append(iface), _clock=clock)
    return events


def test_own_start_then_completed_consumed(monkeypatch):
    """(a) 자기 START→COMPLETED 정상 소비 — 종전 동작 등가(타 START 없음)."""
    events = _run_scan_event(monkeypatch, [OWN_START, COMPLETED])
    assert events == ["mlan0"]


def test_other_start_interleaved_drops_completed(monkeypatch):
    """(b) 자기 START→타 START→COMPLETED = 미소비 — 먼저 시작한 관찰자 보호."""
    events = _run_scan_event(monkeypatch, [OWN_START, OTHER_START, COMPLETED])
    assert events == []


def test_other_start_before_own_drops_completed(monkeypatch):
    """(b') 타 START→자기 START→COMPLETED = 미소비 — 나중에 시작한 관찰자 보호.

    즉시 리셋 방식의 사각지대(#158 Codex 리뷰): scan_started=False 상태의 타 START
    는 무시되어 이후 자기 START 가 타 iface 의 COMPLETED 를 소비했다."""
    events = _run_scan_event(monkeypatch, [OTHER_START, OWN_START, COMPLETED])
    assert events == []


def test_other_start_only_not_consumed(monkeypatch):
    """(c) 타 START→COMPLETED 만 = 미소비 — 타 iface 스캔을 오귀속하지 않음."""
    events = _run_scan_event(monkeypatch, [OTHER_START, COMPLETED])
    assert events == []


def test_completed_without_start_not_consumed(monkeypatch):
    """(d) START 없이 COMPLETED = 미소비 — 기존 scan_started 게이트 등가."""
    events = _run_scan_event(monkeypatch, [COMPLETED])
    assert events == []


def test_recovers_after_window_expiry(monkeypatch):
    """혼선으로 버린 뒤 시간창이 지난 다음 자기 스캔은 정상 소비(자연 복구).

    clock 읽기 순서: 타 START 기록(t=0) → COMPLETED₁ 판정(t=1, 창 내 → 폐기) →
    COMPLETED₂ 판정(t=100, 창 밖 → 소비). 실환경에선 bgscan 주기 60s 가 창(30s)을
    넘겨 다음 라운드가 자연 복구된다."""
    events = _run_scan_event(
        monkeypatch,
        [OWN_START, OTHER_START, COMPLETED, OWN_START, COMPLETED],
        clock_values=[0.0, 1.0, 100.0])
    assert events == ["mlan0"]


def test_stale_other_start_does_not_block_forever(monkeypatch):
    """타 START 의 COMPLETED 라인이 유실돼도 폐기 고착은 창 길이로 한정.

    clock: 타 START(t=0) → 자기 COMPLETED 판정(t=40 > 창 30) → 정상 소비."""
    events = _run_scan_event(
        monkeypatch,
        [OTHER_START, OWN_START, COMPLETED],
        clock_values=[0.0, 40.0])
    assert events == ["mlan0"]
