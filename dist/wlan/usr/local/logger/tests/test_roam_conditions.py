"""check_roam_conditions 단위 테스트 — 특히 falling trend 3dB 완화의 실효성.

종전 구현은 완화 조항이 선행 `diff < DIFF_TH` 기각 **뒤**에 있어 절대 발동하지
못했다(도달 시점엔 diff ≥ DIFF_TH 가 이미 보장 → reason 문자열만 변경되는 dead code).
PREDICTIVE_ROAM 활성 + falling trend 에서 diff ∈ [DIFF_TH-3, DIFF_TH) 구간이
실제로 통과해야 '하락 추세면 더 쉽게 로밍'이라는 문서화된 의도가 성립한다."""
import sys
import os
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

FALLING = wifi_roam.RSSITrendTracker.TREND_FALLING
STABLE = wifi_roam.RSSITrendTracker.TREND_STABLE


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 10)
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", True)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)


def _st(rssi=-70):
    return {"bssid": "aa:aa:aa:aa:aa:aa", "rssi": rssi, "load": 0}


def _ap(rssi, load=0):
    return {"bssid": "bb:bb:bb:bb:bb:bb", "rssi": rssi, "load": load}


def test_falling_trend_relaxes_threshold_band():
    """[핵심] falling + diff=8 (∈[7,10)) → 완화 구간 통과 + 'Falling trend' 사유."""
    ok, reason = wifi_roam.check_roam_conditions(_st(-70), _ap(-62), FALLING)
    assert ok is True, f"완화 미발동(dead code 회귀): {reason}"
    assert "Falling trend" in reason


def test_stable_trend_keeps_full_threshold():
    """대조군: stable 은 완화 없음 — diff=8 < 10 기각."""
    ok, reason = wifi_roam.check_roam_conditions(_st(-70), _ap(-62), STABLE)
    assert ok is False
    assert "10dB" in reason


def test_falling_trend_floor_still_rejects():
    """완화 하한: falling 이어도 diff=6 < DIFF_TH-3(7) 은 기각(사유에 완화 임계 노출)."""
    ok, reason = wifi_roam.check_roam_conditions(_st(-70), _ap(-64), FALLING)
    assert ok is False
    assert "7dB" in reason


def test_predictive_disabled_no_relaxation(monkeypatch):
    """PREDICTIVE_ROAM=off 면 falling 이어도 완화 없음."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    ok, _ = wifi_roam.check_roam_conditions(_st(-70), _ap(-62), FALLING)
    assert ok is False


def test_full_diff_reasons_unchanged():
    """무회귀: diff ≥ DIFF_TH 는 종전과 동일 판정·사유. 주의 — 'Falling trend' 사유는
    완화 구간 통과 표시가 아니라 **추세 활성 표시**라, diff ≥ DIFF_TH(완화 불필요)여도
    falling 이면 붙는 것이 올바른(종전 유지) 동작이다."""
    ok_f, reason_f = wifi_roam.check_roam_conditions(_st(-70), _ap(-58), FALLING)
    assert ok_f is True and "Falling trend" in reason_f
    ok_s, reason_s = wifi_roam.check_roam_conditions(_st(-70), _ap(-58), STABLE)
    assert ok_s is True and reason_s.startswith("RSSI diff")


def test_relaxation_clamped_at_one(monkeypatch):
    """[가드] DIFF_TH<3 극단 설정에서도 완화 임계는 최소 1dB — effective≤0 이면 음수/0
    diff 후보가 게이트를 통과하고, LOAD 활성 시 load 점수가 score 를 양수로 반전시켜
    나쁜 후보가 채택될 수 있다(리뷰 확증 엣지). max(1, DIFF_TH-3) 클램프를 고정한다."""
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 2)
    ok0, _ = wifi_roam.check_roam_conditions(_st(-70), _ap(-70), FALLING)   # diff=0
    assert ok0 is False, "diff=0 후보가 완화 게이트를 통과함(클램프 부재)"
    ok1, _ = wifi_roam.check_roam_conditions(_st(-70), _ap(-69), FALLING)   # diff=1
    assert ok1 is True                                                       # 클램프 하한=1


def test_load_gate_applies_in_relaxed_band(monkeypatch):
    """완화 구간에서도 load 게이트는 그대로 적용(완화가 load 조건을 우회하면 안 됨)."""
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", True)
    monkeypatch.setattr(wifi_roam, "MAX_ROAM_LOAD", 80)
    monkeypatch.setattr(wifi_roam, "LOAD_DIFF_THRESHOLD", 20)
    ok, reason = wifi_roam.check_roam_conditions(_st(-70), _ap(-62, load=90), FALLING)
    assert ok is False
    assert "load" in reason.lower()
