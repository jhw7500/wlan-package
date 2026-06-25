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


def test_post_sleep_extends_cooldown(fake_clock):
    # post_sleep을 주면 cooldown until에 더해져, 실패 후 메인루프가 sleep하는 동안
    # cooldown이 만료돼 무효화되는 것을 막는다(Codex P2 / Claude MEDIUM).
    # retry_count=2, 3회째(over=1): until = now + post_sleep(5) + backoff(1)=3 = now+8.
    cd = CrossSsidCooldown(retry_count=2)
    for _ in range(3):
        cd.register_failure("Office", post_sleep=5)
    assert cd.is_cooling("Office") is True
    fake_clock["now"] += 7.9
    assert cd.is_cooling("Office") is True   # 8s 전 — 여전히 cooldown 유지
    fake_clock["now"] += 0.2                 # 총 8.1s
    assert cd.is_cooling("Office") is False


def test_post_sleep_ignored_within_retry(fake_clock):
    # retry 단계(fails<=retry_count)는 post_sleep과 무관하게 cooldown 없음(즉시 재시도 허용).
    cd = CrossSsidCooldown(retry_count=2)
    cd.register_failure("Office", post_sleep=5)
    assert cd.is_cooling("Office") is False
    cd.register_failure("Office", post_sleep=5)
    assert cd.is_cooling("Office") is False


def test_negative_retry_count_clamped(fake_clock):
    # retry_count 음수(잘못된 config) → 0으로 clamp → 첫 실패부터 즉시 cooldown.
    cd = CrossSsidCooldown(retry_count=-5)
    assert cd.retry_count == 0
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is True


# --- 메인루프 통합 경로(record_cross_ssid_result / skip 조건) ---

def test_record_cross_result_ok_clears(fake_clock):
    # 전환 성공(ok=True) → clear → cooldown 해제.
    cd = CrossSsidCooldown(retry_count=0)
    cd.register_failure("Office")
    assert cd.is_cooling("Office") is True
    wifi_roam.record_cross_ssid_result(cd, "Office", True, 0)
    assert cd.is_cooling("Office") is False


def test_record_cross_result_fail_registers(fake_clock):
    # 전환 실패(ok=False) → register_failure(post_sleep). retry_count=0, over=1:
    # until = now + post_sleep(5) + backoff(1)=3 = now+8.
    cd = CrossSsidCooldown(retry_count=0)
    wifi_roam.record_cross_ssid_result(cd, "Office", False, 5)
    assert cd.is_cooling("Office") is True
    fake_clock["now"] += 8.1
    assert cd.is_cooling("Office") is False


def test_record_cross_result_none_safe(fake_clock):
    # cooldown None(모드 B)이어도 예외 없이 무동작.
    wifi_roam.record_cross_ssid_result(None, "Office", False, 5)


def test_integration_cooldown_skip_condition(monkeypatch, fake_clock):
    # 메인루프 skip 조건: 모드 A에서 cross 대상(should_cross_connect) + cooldown(is_cooling) → skip.
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    cd = CrossSsidCooldown(retry_count=0)
    cd.register_failure("Office")
    base = {"Home"}
    # extra SSID(cross 대상) + cooldown 중 → 메인루프가 후보에서 skip
    assert wifi_roam.should_cross_connect("Office", base) is True
    assert cd.is_cooling("Office") is True
    # 현재 ESS SSID(same)는 cross 대상이 아님 → cooldown과 무관(skip 안 함)
    assert wifi_roam.should_cross_connect("Home", base) is False
