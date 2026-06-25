import sys
import os
from unittest.mock import MagicMock

# sUTILS has heavy runtime deps (paho, serial, numpy); stub it before importing wifi_roam.
sys.modules.setdefault("sUTILS", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam
from wifi_roam import CrossSsidCooldown

import pytest

# `logger` is only assigned at runtime inside main(); stub so any error path is safe.
wifi_roam.logger = MagicMock()


@pytest.fixture
def fake_clock(monkeypatch):
    """wifi_roam.time.time()을 제어 가능한 가짜 시계로 교체."""
    state = {"now": 1000.0}
    monkeypatch.setattr(wifi_roam.time, "time", lambda: state["now"])
    return state


@pytest.fixture(autouse=True)
def _backoff_consts(monkeypatch):
    # compute_no_result_backoff가 읽는 전역을 고정(START=3, MAX=30).
    monkeypatch.setattr(wifi_roam, "SCAN_NO_RESULT_SLEEP", 3)
    monkeypatch.setattr(wifi_roam, "ROAM_NO_RESULT_MAX_SLEEP", 30)


def test_within_retry_count_not_cooling(fake_clock):
    # retry_count=2: 1~2회 실패는 cooldown 없음(즉시 재시도 가능).
    cd = CrossSsidCooldown(retry_count=2)
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is False
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is False


def test_exceeding_retry_count_starts_backoff(fake_clock):
    # 3회째(over=1) → until = now + backoff(1) = now + 3 → cooling True.
    cd = CrossSsidCooldown(retry_count=2)
    for _ in range(3):
        cd.register_failure("Office")
    assert cd.is_cooling("Office") is True
    # backoff(1)=3s 경과 직전/직후 경계
    fake_clock["now"] += 2.9
    assert cd.is_cooling("Office") is True
    fake_clock["now"] += 0.2  # 총 3.1s 경과
    assert cd.is_cooling("Office") is False


def test_backoff_grows_after_expiry(fake_clock):
    # 만료 후 재실패하면 fails 유지되어 backoff가 더 길어진다(over=2 → 6s).
    cd = CrossSsidCooldown(retry_count=2)
    for _ in range(3):
        cd.register_failure("Office")   # fails=3, until=now+3
    fake_clock["now"] += 5              # 만료
    assert cd.is_cooling("Office") is False
    cd.register_failure("Office")        # fails=4, over=2 → backoff(2)=6s
    assert cd.is_cooling("Office") is True
    fake_clock["now"] += 5.9
    assert cd.is_cooling("Office") is True
    fake_clock["now"] += 0.2            # 총 6.1s
    assert cd.is_cooling("Office") is False


def test_clear_resets(fake_clock):
    cd = CrossSsidCooldown(retry_count=2)
    for _ in range(3):
        cd.register_failure("Office")
    assert cd.is_cooling("Office") is True
    cd.clear("Office")
    assert cd.is_cooling("Office") is False
    # clear 후 재실패는 fails=1부터(다시 retry 단계, cooldown 없음)
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is False


def test_unknown_ssid_not_cooling(fake_clock):
    cd = CrossSsidCooldown(retry_count=2)
    assert cd.is_cooling("NeverSeen") is False


def test_empty_ssid_ignored(fake_clock):
    # ssid가 빈 문자열/None이면 등록하지 않는다(방어).
    cd = CrossSsidCooldown(retry_count=2)
    cd.register_failure("")
    cd.register_failure(None)
    assert cd.is_cooling("") is False


def test_independent_ssids(fake_clock):
    # SSID별 카운트 독립.
    cd = CrossSsidCooldown(retry_count=2)
    for _ in range(3):
        cd.register_failure("Office")
    assert cd.is_cooling("Office") is True
    assert cd.is_cooling("Guest") is False


def test_retry_count_zero_immediate_cooldown(fake_clock):
    # retry_count=0: 첫 실패(over=1)부터 즉시 backoff.
    cd = CrossSsidCooldown(retry_count=0)
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is True
