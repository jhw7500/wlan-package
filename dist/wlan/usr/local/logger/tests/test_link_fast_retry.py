import sys
import os
from unittest.mock import MagicMock

# sUTILS pulls heavy runtime deps (paho, serial); stub before importing the module.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_logger_link  # noqa: E402

import pytest  # noqa: E402

_STATION_OK = "Station 11:22:33:44:55:66 (on mlan0)\n\tsignal: -50 dBm"


@pytest.fixture
def link():
    """모듈 전역 IFACE / logger 준비 (logger는 __main__에서만 생성되므로 직접 주입)."""
    wifi_logger_link.IFACE = "mlan0"
    wifi_logger_link.logger = MagicMock()
    return wifi_logger_link


def test_recovers_within_window_and_logs(link, monkeypatch):
    """윈도우 안에서 station 회복 → 출력 반환 + 블립 억제 로그."""
    calls = {"n": 0}

    def fake(cmd):
        calls["n"] += 1
        return _STATION_OK if calls["n"] >= 2 else ""

    monkeypatch.setattr(link, "run_command", fake)
    out = link.fast_retry_station_dump(4, 0.001)
    assert out and "Station" in out
    assert calls["n"] == 2
    assert link.logger.message.called  # 회복 시 진단 로그


def test_genuine_disconnect_returns_none_after_count(link, monkeypatch):
    """끝까지 비면 정확히 count회 시도 후 None (진짜 끊김)."""
    calls = {"n": 0}
    monkeypatch.setattr(link, "run_command",
                        lambda cmd: calls.__setitem__("n", calls["n"] + 1) or "")
    out = link.fast_retry_station_dump(4, 0.001)
    assert out is None
    assert calls["n"] == 4


def test_count_zero_disables_retry(link, monkeypatch):
    """count=0 → 재시도/sleep 없이 즉시 None."""
    calls = {"n": 0}
    monkeypatch.setattr(link, "run_command",
                        lambda cmd: calls.__setitem__("n", calls["n"] + 1) or _STATION_OK)
    assert link.fast_retry_station_dump(0, 0.05) is None
    assert calls["n"] == 0


def test_rejects_non_station_output(link, monkeypatch):
    """station이 아닌 비정상 출력은 거부(validate_station)."""
    monkeypatch.setattr(link, "run_command", lambda cmd: "some unrelated text")
    assert link.fast_retry_station_dump(3, 0.001) is None
