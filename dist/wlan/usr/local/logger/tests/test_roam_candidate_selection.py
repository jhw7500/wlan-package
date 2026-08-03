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


def _st(rssi=-63):
    return {"bssid": SELF_BSSID, "rssi": rssi, "ssid": "TEST"}


def _ap(bssid, rssi):
    return {"bssid": bssid, "rssi": rssi, "ssid": "TEST"}


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


# ── 핑퐁 억제 대상 후보 제외 (스캔→선정→차단 헛돌이 제거) ──
# 억제 중 대상이 선정되면 roam_to_bssid 차단 후 interval 만 자고 재스캔해
# CHECK_INTERVAL 주기로 낭비 스캔이 돌았다. 선정 단계에서 제외하면 (다른
# 후보가 없는 한) no-candidate backoff 가 주기를 압축하고, 제3의 AP 는
# 즉시 선택된다.


def _blocking_preventer(pairs):
    """지정한 (from,to) 쌍만 차단하는 스텁."""
    p = MagicMock()
    p.would_block.side_effect = (
        lambda f, to: "round-trip" if (f, to) in pairs else None
    )
    return p


def test_pingpong_suppressed_target_excluded(monkeypatch):
    """[핵심] 억제 중인 유일 후보는 탈락 → 후보없음(backoff 경로)."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", True)
    monkeypatch.setattr(
        wifi_roam, "ping_pong_preventer",
        _blocking_preventer({(SELF_BSSID, "bb:bb:bb:bb:bb:bb")}),
    )
    best, _reason, _score = _eval(8, [_ap("bb:bb:bb:bb:bb:bb", -52)],
                                  monkeypatch=monkeypatch)
    assert best is None, "억제 중 대상이 선정되면 차단 루프가 CHECK_INTERVAL 로 헛돈다"


def test_pingpong_third_ap_still_selected(monkeypatch):
    """억제는 쌍 단위 — 제3의 AP 는 (RSSI 가 더 낮아도) 즉시 선택된다."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", True)
    monkeypatch.setattr(
        wifi_roam, "ping_pong_preventer",
        _blocking_preventer({(SELF_BSSID, "bb:bb:bb:bb:bb:bb")}),
    )
    best, _reason, _score = _eval(
        8,
        [_ap("bb:bb:bb:bb:bb:bb", -50), _ap("cc:cc:cc:cc:cc:cc", -52)],
        monkeypatch=monkeypatch,
    )
    assert best is not None and best["bssid"] == "cc:cc:cc:cc:cc:cc"


def test_pingpong_disabled_no_exclusion(monkeypatch):
    """ENABLE_PING_PONG_PREVENTION=False 면 제외 없음(무회귀)."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", False)
    monkeypatch.setattr(
        wifi_roam, "ping_pong_preventer",
        _blocking_preventer({(SELF_BSSID, "bb:bb:bb:bb:bb:bb")}),
    )
    best, _reason, _score = _eval(8, [_ap("bb:bb:bb:bb:bb:bb", -52)],
                                  monkeypatch=monkeypatch)
    assert best is not None


def test_pingpong_preventer_none_no_exclusion(monkeypatch):
    """preventer 미생성(기능 off 기동)이면 제외 없음(무회귀)."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", True)
    monkeypatch.setattr(wifi_roam, "ping_pong_preventer", None)
    best, _reason, _score = _eval(8, [_ap("bb:bb:bb:bb:bb:bb", -52)],
                                  monkeypatch=monkeypatch)
    assert best is not None


# ── would_block ↔ is_ping_pong 동등성 + 무로그 계약 ──


def _ap_x(bssid, rssi, ssid):
    return {"bssid": bssid, "rssi": rssi, "ssid": ssid}


def test_pingpong_cross_ssid_suppressed_at_ssid_scope(monkeypatch):
    """[P1 회귀] 모드 A cross 대상은 SSID 통째 제외 — select_network 는 SSID 만
    고정하므로 약한 BSSID 를 후보로 남기면 supplicant 가 억제된 강한 BSSID 로
    재결합해 억제를 우회한다."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", True)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)  # 모드 A
    monkeypatch.setattr(
        wifi_roam, "ping_pong_preventer",
        _blocking_preventer({(SELF_BSSID, "bb:bb:bb:bb:bb:bb")}),  # 강한 쪽만 차단
    )
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 8)
    entries = [
        _ap_x("bb:bb:bb:bb:bb:bb", -50, "EXTRA"),   # 억제된 강한 BSSID
        _ap_x("dd:dd:dd:dd:dd:dd", -52, "EXTRA"),   # 같은 SSID 의 약한 BSSID
    ]
    st = {"bssid": SELF_BSSID, "rssi": -63, "ssid": "TEST"}
    best, _r, _s = wifi_roam.evaluate_candidates(entries, st, STABLE, None, "TEST", -63)
    assert best is None, "약한 BSSID 가 남으면 select_network 가 억제 BSSID 로 우회한다"


def test_pingpong_cross_other_ssid_unaffected(monkeypatch):
    """SSID 단위 제외는 해당 SSID 한정 — 무관한 cross SSID 는 즉시 선택."""
    monkeypatch.setattr(wifi_roam, "ENABLE_PING_PONG_PREVENTION", True)
    monkeypatch.setattr(wifi_roam, "GENERATE_NETWORK_BLOCKS", True)
    monkeypatch.setattr(
        wifi_roam, "ping_pong_preventer",
        _blocking_preventer({(SELF_BSSID, "bb:bb:bb:bb:bb:bb")}),
    )
    monkeypatch.setattr(wifi_roam, "DIFF_TH", 8)
    entries = [
        _ap_x("bb:bb:bb:bb:bb:bb", -50, "EXTRA"),
        _ap_x("ee:ee:ee:ee:ee:ee", -52, "OTHER"),
    ]
    st = {"bssid": SELF_BSSID, "rssi": -63, "ssid": "TEST"}
    best, _r, _s = wifi_roam.evaluate_candidates(entries, st, STABLE, None, "TEST", -63)
    assert best is not None and best["bssid"] == "ee:ee:ee:ee:ee:ee"


def test_would_block_too_many_roams_is_quiet(monkeypatch):
    """무로그 계약을 too-many-roams 분기까지 — 이 분기에 로그가 생겨도 잡는다."""
    pp = wifi_roam.PingPongPreventer(window_seconds=20, max_roams=1)
    pp.add_roam("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")
    wifi_roam.logger.reset_mock()
    assert pp.would_block("cc:cc:cc:cc:cc:cc", "dd:dd:dd:dd:dd:dd") == "too-many-roams"
    wifi_roam.logger.message.assert_not_called()


def test_would_block_matches_is_ping_pong_and_is_quiet(monkeypatch):
    """두 판정은 같은 규칙(위임 구조)이고, would_block 은 로그를 남기지 않는다 —
    후보마다 매 tick 호출되는 자리라 warn 반복을 막는 것이 분리 이유다."""
    monkeypatch.setattr(wifi_roam, "PING_PONG_DETECTION_TIME", 5)
    pp = wifi_roam.PingPongPreventer(window_seconds=20, max_roams=3)
    pp.add_roam("aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb")

    wifi_roam.logger.reset_mock()
    assert pp.would_block("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa") == "round-trip"
    assert pp.would_block("bb:bb:bb:bb:bb:bb", "cc:cc:cc:cc:cc:cc") is None
    wifi_roam.logger.message.assert_not_called()

    assert pp.is_ping_pong("bb:bb:bb:bb:bb:bb", "aa:aa:aa:aa:aa:aa") is True
    wifi_roam.logger.message.assert_called()  # 최종 방어선은 경고를 남긴다
