"""dmesg 스캔 이벤트 타 iface 혼선 가드 테스트.

dmesg 는 단일 스트림이고 moal 의 COMPLETED 라인에는 iface 가 없다(온타겟 실측
2026-08-07: `wlan: mlan0 START SCAN` / `wlan: SCAN COMPLETED: scanned AP count=15`).
mlan0/mlan1 로거 인스턴스 동시 활성 시 자기 START 후 타 iface 의 COMPLETED 를
오소비할 수 있어, 타 START 개입 시 scan_started 를 리셋하고 모호한 COMPLETED 를
버린다(다음 자기 스캔에서 자연 복구). mlan0 단독 출하 기본값에서는 타 iface
START 자체가 없어 동작 변화 0."""
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


# ---- scan_event 시퀀스 (classify 소비 로직) ----

def _run_scan_event(monkeypatch, lines, interface="mlan0"):
    """fake dmesg 라인 시퀀스를 scan_event 에 먹여 콜백 발화 목록을 반환.

    fake stdout(iterator) 소진 시 for 루프가 끝나 함수가 반환된다(실환경에선
    dmesg --follow 로 무한 블로킹)."""
    events = []
    fake_proc = MagicMock()
    fake_proc.__enter__.return_value = fake_proc
    fake_proc.stdout = iter(lines)
    monkeypatch.setattr(wifi_logger_scan, "get_last_dmesg_line_count", lambda: 0)
    monkeypatch.setattr(
        wifi_logger_scan.subprocess, "Popen", lambda *a, **k: fake_proc)
    wifi_logger_scan.scan_event(interface, lambda iface: events.append(iface))
    return events


def test_own_start_then_completed_consumed(monkeypatch):
    """(a) 자기 START→COMPLETED 정상 소비 — 종전 동작 등가."""
    events = _run_scan_event(monkeypatch, [OWN_START, COMPLETED])
    assert events == ["mlan0"]


def test_other_start_interleaved_drops_completed(monkeypatch):
    """(b) 자기 START→타 START→COMPLETED = 미소비(리셋) — 혼선 가드 본체."""
    events = _run_scan_event(monkeypatch, [OWN_START, OTHER_START, COMPLETED])
    assert events == []


def test_other_start_only_not_consumed(monkeypatch):
    """(c) 타 START→COMPLETED 만 = 미소비 — 타 iface 스캔을 오귀속하지 않음."""
    events = _run_scan_event(monkeypatch, [OTHER_START, COMPLETED])
    assert events == []


def test_completed_without_start_not_consumed(monkeypatch):
    """(d) START 없이 COMPLETED = 미소비 — 기존 scan_started 게이트 등가."""
    events = _run_scan_event(monkeypatch, [COMPLETED])
    assert events == []


def test_recovers_on_next_own_scan(monkeypatch):
    """혼선으로 버린 뒤 다음 자기 START→COMPLETED 는 정상 소비(자연 복구)."""
    events = _run_scan_event(
        monkeypatch,
        [OWN_START, OTHER_START, COMPLETED, OWN_START, COMPLETED])
    assert events == ["mlan0"]
