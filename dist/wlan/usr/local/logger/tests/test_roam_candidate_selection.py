"""evaluate_candidates 후보 선택 단위 테스트 — 특히 DIFF_TH=0 의 실효성.

종전 구현은 `best_ap, best_reason, best_score = None, "", 0` 초기값과
`if score > best_score` 갱신 조건이 충돌해, DIFF_TH=0 설정에서 diff=0 후보가
check_roam_conditions 게이트(`diff < effective_diff_th`)를 정상 통과하고도
score=diff*10=0 이라 `0 > 0` 이 거짓이 되어 선택 단계에서 조용히 탈락했다.
로그에는 `Roam candidate ... score=0` 으로 찍혀 채택된 것처럼 보이므로 진단도
어긋난다. 결과적으로 DIFF_TH=0 이 DIFF_TH=1 과 동일하게 동작했다(실측 재생
933 대 933 일치). '0=이득 무관'이라는 문서화된 설정 의미가 성립하려면 첫 유효
후보는 score 와 무관하게 채택되어야 한다."""
import sys
import os
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

STABLE = wifi_roam.RSSITrendTracker.TREND_STABLE
FALLING = wifi_roam.RSSITrendTracker.TREND_FALLING

SELF_BSSID = "aa:aa:aa:aa:aa:aa"


@pytest.fixture(autouse=True)
def _globals(monkeypatch):
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", False)
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", False)


def _st(rssi=-63):
    return {"bssid": SELF_BSSID, "rssi": rssi, "load": 0, "ssid": "TEST"}


def _ap(bssid, rssi, load=0):
    return {"bssid": bssid, "rssi": rssi, "load": load, "ssid": "TEST"}


def _eval(diff_th, entries, own_rssi=-63, monkeypatch=None, trend=STABLE):
    monkeypatch.setattr(wifi_roam, "DIFF_TH", diff_th)
    st = _st(own_rssi)
    return wifi_roam.evaluate_candidates(
        entries, st, trend, None, "TEST", own_rssi
    )


def test_diff_th_zero_accepts_equal_rssi_candidate(monkeypatch):
    """[핵심] DIFF_TH=0 + diff=0dB → 채택. 게이트가 허용한 후보가 탈락하면 회귀."""
    best, _reason, _score = _eval(0, [_ap("bb:bb:bb:bb:bb:bb", -63)], monkeypatch=monkeypatch)
    assert best is not None, "DIFF_TH=0 인데 diff=0 후보가 탈락(best_score 초기값 회귀)"
    assert best["bssid"] == "bb:bb:bb:bb:bb:bb"


def test_diff_th_zero_still_rejects_worse_ap(monkeypatch):
    """DIFF_TH=0 이어도 더 나쁜 AP(diff<0)는 check_roam_conditions 게이트에서 차단."""
    best, _reason, _score = _eval(0, [_ap("bb:bb:bb:bb:bb:bb", -70)], monkeypatch=monkeypatch)
    assert best is None


def test_diff_th_zero_picks_strongest_among_ties(monkeypatch):
    """최댓값 선택 로직 보존 — 0dB 후보가 8dB 후보를 가리면 안 된다."""
    best, _reason, score = _eval(
        0,
        [_ap("bb:bb:bb:bb:bb:bb", -63), _ap("cc:cc:cc:cc:cc:cc", -55)],
        monkeypatch=monkeypatch,
    )
    assert best["bssid"] == "cc:cc:cc:cc:cc:cc"
    assert score == 80


def test_diff_th_zero_order_independent(monkeypatch):
    """엔트리 순서가 뒤바뀌어도 같은 AP 를 고른다(첫 후보 무조건 채택의 부작용 방지)."""
    best, _reason, _score = _eval(
        0,
        [_ap("cc:cc:cc:cc:cc:cc", -55), _ap("bb:bb:bb:bb:bb:bb", -63)],
        monkeypatch=monkeypatch,
    )
    assert best["bssid"] == "cc:cc:cc:cc:cc:cc"


def test_default_diff_th_unchanged_rejects_zero_gain(monkeypatch):
    """무회귀: 출하 기본 DIFF_TH=8 에서 diff=0 후보는 게이트에서 차단(동작 불변)."""
    best, _reason, _score = _eval(8, [_ap("bb:bb:bb:bb:bb:bb", -63)], monkeypatch=monkeypatch)
    assert best is None


def test_default_diff_th_accepts_clear_gain(monkeypatch):
    """무회귀: DIFF_TH=8 + diff=11dB 는 채택되고 score=diff*10."""
    best, _reason, score = _eval(8, [_ap("bb:bb:bb:bb:bb:bb", -52)], monkeypatch=monkeypatch)
    assert best is not None
    assert score == 110


def test_no_candidate_returns_contract_tuple(monkeypatch):
    """후보 없음 반환 계약 (None, "", 0) 유지 — 호출부가 score 를 포맷한다."""
    assert _eval(8, [], monkeypatch=monkeypatch) == (None, "", 0)


def test_self_bssid_excluded(monkeypatch):
    """자기 BSSID 는 DIFF_TH=0 에서도 후보에서 제외."""
    best, _reason, _score = _eval(0, [_ap(SELF_BSSID, -40)], monkeypatch=monkeypatch)
    assert best is None


# ── falling 추세 완화 클램프와의 교차 (check_roam_conditions:2291-2298) ──
# 완화 임계 하한이 상수 1 이면 DIFF_TH=0 이어도 max(1, -3)=1 이 되어 diff=0 후보가
# 차단됐다 — CLI 로 0 을 설정해도 falling 추세에서만 1 처럼 동작하는 불일치.
# 하한을 min(DIFF_TH, 1) 로 바꿔 DIFF_TH=0 일 때만 0 을 허용하고 나머지는 종전 유지.


def test_diff_th_zero_survives_falling_trend_clamp(monkeypatch):
    """[핵심] DIFF_TH=0 + PREDICTIVE + FALLING 에서도 diff=0 후보가 채택된다."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", True)
    best, _reason, _score = _eval(
        0, [_ap("bb:bb:bb:bb:bb:bb", -63)], monkeypatch=monkeypatch, trend=FALLING
    )
    assert best is not None, "DIFF_TH=0 인데 falling 완화 클램프가 diff=0 을 차단(하한 회귀)"


def test_falling_clamp_floor_unchanged_for_normal_diff_th(monkeypatch):
    """무회귀: DIFF_TH=3 + FALLING 은 종전대로 하한 1 — diff=0 은 차단된다."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", True)
    best, _reason, _score = _eval(
        3, [_ap("bb:bb:bb:bb:bb:bb", -63)], monkeypatch=monkeypatch, trend=FALLING
    )
    assert best is None


def test_diff_th_zero_falling_still_rejects_worse_ap(monkeypatch):
    """DIFF_TH=0 + FALLING 이어도 음수 diff(더 나쁜 AP)는 차단 — 클램프 본래 목적."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PREDICTIVE_ROAM", True)
    best, _reason, _score = _eval(
        0, [_ap("bb:bb:bb:bb:bb:bb", -70)], monkeypatch=monkeypatch, trend=FALLING
    )
    assert best is None


# ── LOAD_BASED_ROAM 교차: score 가 음수가 될 수 있는 경로 ──


def _load_globals(monkeypatch):
    monkeypatch.setattr(wifi_roam, "ENABLE_LOAD_BASED_ROAM", True)
    monkeypatch.setattr(wifi_roam, "MAX_ROAM_LOAD", 80)
    monkeypatch.setattr(wifi_roam, "LOAD_DIFF_THRESHOLD", 20)


def test_diff_th_zero_accepts_first_candidate_with_negative_score(monkeypatch):
    """DIFF_TH=0 + LOAD 활성: diff=0 이고 후보 부하가 더 높으면 score<0 이 되는데도 채택된다.

    'DIFF_TH=0 = 이득 무관' 의미에는 부합하지만 종전(`score > best_score`, 초기 0)과
    달라지는 지점이라 의도된 동작으로 고정한다. load 게이트(roam_load > current_load +
    LOAD_DIFF_THRESHOLD)를 통과하는 범위 안에서만 발생한다."""
    _load_globals(monkeypatch)
    best, _reason, score = _eval(
        0, [_ap("bb:bb:bb:bb:bb:bb", -63, load=10)], monkeypatch=monkeypatch
    )
    assert best is not None
    assert score < 0, f"부하 패널티로 score<0 이어야 한다(got {score})"


def test_low_diff_th_with_load_penalty_also_accepts_negative_score(monkeypatch):
    """[범위 고정] 이 변경은 DIFF_TH=0 에 국한되지 않는다 — DIFF_TH<=3 + LOAD 활성에서도
    첫 후보가 음수 score 로 채택된다.

    DIFF_TH=2, diff=2, current_load=30, roam_load=49 이면
      load 게이트: 49 > 30+20=50 → 거짓이라 통과
      score = 2*10 + (30-49)*2 = -18
    구 코드(`score > best_score`, 초기 0)는 미채택, 신 코드는 `best_ap is None` 으로 채택.
    출하 기본 DIFF_TH=8 은 최소 score 가 80-38=42 라 영향이 없다."""
    _load_globals(monkeypatch)
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 2)
    st = {"bssid": SELF_BSSID, "rssi": -63, "load": 30, "ssid": "TEST"}
    entries = [_ap("bb:bb:bb:bb:bb:bb", -61, load=49)]
    best, _reason, score = wifi_roam.evaluate_candidates(
        entries, st, STABLE, None, "TEST", -63
    )
    assert best is not None
    assert score == -18, f"score 계산이 바뀌었다(got {score})"


def test_negative_score_candidate_does_not_mask_better_one(monkeypatch):
    """음수 score 후보가 먼저 와도 더 나은 후보가 최종 선택된다(최댓값 로직 보존)."""
    _load_globals(monkeypatch)
    best, _reason, _score = _eval(
        0,
        [_ap("bb:bb:bb:bb:bb:bb", -63, load=10), _ap("cc:cc:cc:cc:cc:cc", -55, load=0)],
        monkeypatch=monkeypatch,
    )
    assert best["bssid"] == "cc:cc:cc:cc:cc:cc"
