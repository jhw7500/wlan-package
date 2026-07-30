"""good-signal 분기의 backoff streak 리셋 게이트.

배경(정체 로그 18.85h 재생 실측):
  메인루프의 good-signal 분기(`rssi >= threshold`)는 후보없음 backoff streak 를 **무조건**
  리셋했다. 그런데 오탐 리셋 662건 중 624건(94%)이 이 분기였고 그중 623건이 **Δ0dB** —
  임계 바로 위에서 진동할 뿐 위치가 안 변한 경우다. 리셋되면 다음 악화에 backoff 가
  시작값(3초)부터 다시 올라가 스캔이 폭증한다(스캔 3377→1417 = -58.0%,
  airtime duty 5.44%→2.28%). 이동 로그(71개 90.1h)는 -0.0%·추가지연 0건으로 재탐색성 보존.

판정은 "직전 **리셋 시점** 대비 |Δrssi| >= delta_db" 다. 매 tick 직전값과 비교하면 1dB 씩
천천히 이동하는 구간에서 매번 Δ<delta 로 억제돼 이동을 놓치므로, 리셋 이후 누적 변화를 본다.
"""
import sys
import os
import time
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    monkeypatch.setattr(wifi_roam, "ENABLE_GOOD_SIGNAL_GATE", True)
    monkeypatch.setattr(wifi_roam, "GOOD_SIGNAL_GATE_DELTA_DB", 2)
    monkeypatch.setattr(wifi_roam, "GOOD_SIGNAL_GATE_GRACE_SEC", 40)


def allowed(cur, base, assoc_ts=None, now=NOW):
    ok, why = wifi_roam.good_signal_reset_allowed(cur, base, assoc_ts, now=now)
    return ok, why


# ── 무회귀 ──


def test_disabled_always_allows(monkeypatch):
    """[무회귀] enable=false 면 종전대로 무조건 리셋 — Δ0dB 여도 허용."""
    monkeypatch.setattr(wifi_roam, "ENABLE_GOOD_SIGNAL_GATE", False)
    ok, why = allowed(-50, -50)
    assert ok is True and why == "gate-disabled"


def test_no_baseline_allows():
    """기준값(직전 리셋 RSSI)이 없으면 허용 — 프로세스 시작 직후 첫 판정."""
    ok, why = allowed(-50, None)
    assert ok is True and why == "no-baseline"


# ── 핵심: 정체 억제 / 이동 허용 ──


def test_stationary_zero_delta_suppressed():
    """[핵심] Δ0dB 는 억제 — 실측 오탐 리셋 623/624 건이 이 경우다."""
    ok, why = allowed(-50, -50, assoc_ts=NOW - 100)
    assert ok is False
    assert "stationary(0dB)" == why


def test_delta_below_threshold_suppressed():
    """Δ1dB < delta_db(2) → 억제. 1dB 양자화 노이즈를 이동으로 읽지 않는다."""
    ok, _why = allowed(-49, -50, assoc_ts=NOW - 100)
    assert ok is False


def test_delta_at_threshold_allows():
    """Δ2dB == delta_db → 허용(경계 포함)."""
    ok, why = allowed(-48, -50, assoc_ts=NOW - 100)
    assert ok is True and why == "moved(2dB)"


def test_delta_negative_direction_allows():
    """방향 무관 — 신호가 나빠지는 쪽으로 2dB 변해도 '이동'이다(abs)."""
    ok, _why = allowed(-52, -50, assoc_ts=NOW - 100)
    assert ok is True


def test_large_delta_allows():
    """급격한 변화는 당연히 허용 — 이동 로그에서 추가지연 0건이었던 근거."""
    ok, _why = allowed(-40, -55, assoc_ts=NOW - 100)
    assert ok is True


# ── attach ramp grace ──


def test_post_assoc_grace_bypasses_gate():
    """[핵심] 결합 후 GRACE_SEC 이내면 Δ0dB 여도 허용 — attach ramp 보호.

    결합 직후 RSSI 는 25초에 걸쳐 12~14dB 하강한다(TX rate 불변이라 측정 램프).
    그 구간의 큰 Δ 를 '이동'으로 읽으면 게이트가 사실상 무력화되므로 우회한다."""
    ok, why = allowed(-50, -50, assoc_ts=NOW - 10)
    assert ok is True and why == "post-assoc-grace"


def test_after_grace_gate_applies():
    """grace 경과 후에는 게이트가 다시 작동."""
    ok, _why = allowed(-50, -50, assoc_ts=NOW - 41)
    assert ok is False


def test_grace_boundary_exclusive():
    """경계: 정확히 GRACE_SEC 경과면 게이트 적용(< 비교)."""
    ok, _why = allowed(-50, -50, assoc_ts=NOW - 40)
    assert ok is False


def test_assoc_ts_none_skips_grace():
    """결합 시각을 모르면 grace 를 적용하지 않는다(게이트 정상 판정)."""
    ok, _why = allowed(-50, -50, assoc_ts=None)
    assert ok is False


# ── 누적 변화 의미 고정 ──


def test_baseline_is_last_reset_not_previous_tick():
    """[설계 고정] 기준은 '직전 tick'이 아니라 '직전 리셋 시점'.

    1dB 씩 두 번 이동한 경우: 매 tick 비교면 Δ1 로 계속 억제되지만, 리셋 시점 기준이면
    누적 2dB 로 허용된다. 천천히 이동하는 구간을 놓치지 않기 위한 선택이다."""
    base = -50
    assert allowed(-49, base, assoc_ts=NOW - 100)[0] is False   # 누적 1dB
    assert allowed(-48, base, assoc_ts=NOW - 100)[0] is True    # 누적 2dB


def test_delta_db_configurable(monkeypatch):
    """delta_db 를 3 으로 올리면 Δ2dB 는 억제된다."""
    monkeypatch.setattr(wifi_roam, "GOOD_SIGNAL_GATE_DELTA_DB", 3)
    assert allowed(-48, -50, assoc_ts=NOW - 100)[0] is False
    assert allowed(-47, -50, assoc_ts=NOW - 100)[0] is True


# ── 결합 추적 (track_association / invalidate_reset_baseline) ──
# 리뷰가 지적한 세 결함의 회귀 테스트. 종전엔 이 영역 테스트가 없어 놓쳤다.


def _st(bssid="aa:aa:aa:aa:aa:aa"):
    return {"bssid": bssid, "rssi": -50, "freq": 5180}


def test_track_first_observation_is_new_assoc():
    gs = wifi_roam.new_gate_state()
    assert wifi_roam.track_association(_st(), gs) is True
    assert gs["bssid"] == "aa:aa:aa:aa:aa:aa"
    assert gs["assoc_ts"] is not None


def test_track_same_bssid_is_not_new():
    gs = wifi_roam.new_gate_state()
    wifi_roam.track_association(_st(), gs)
    ts = gs["assoc_ts"]
    assert wifi_roam.track_association(_st(), gs) is False
    assert gs["assoc_ts"] == ts, "같은 AP 재관측이 grace 를 갱신하면 게이트가 계속 우회된다"


def test_track_bssid_change_resets_baseline_and_counter():
    """[Claude MEDIUM] BSSID 변경 시 reset_rssi 와 **suppressed 둘 다** 초기화.

    suppressed 를 남기면 구 AP 에서 쌓인 카운트가 새 AP 문맥의 요약 로그에
    "이전 N회 억제"로 찍혀 운용 중 혼선을 준다."""
    gs = wifi_roam.new_gate_state()
    wifi_roam.track_association(_st("aa:aa:aa:aa:aa:aa"), gs)
    gs["reset_rssi"] = -50
    gs["suppressed"] = 7
    assert wifi_roam.track_association(_st("bb:bb:bb:bb:bb:bb"), gs) is True
    assert gs["reset_rssi"] is None, "옛 AP RSSI 와 비교는 무의미"
    assert gs["suppressed"] == 0, "구 AP 억제 카운트가 새 문맥에 남았다"


def test_track_none_station_clears_bssid():
    """[Codex P2] station=None(끊김·link.json stale)이면 bssid 를 비운다."""
    gs = wifi_roam.new_gate_state()
    wifi_roam.track_association(_st(), gs)
    assert wifi_roam.track_association(None, gs) is False
    assert gs["bssid"] is None


def test_track_reassoc_same_ap_after_disconnect_is_detected():
    """[Codex P2 핵심] 끊겼다 **같은 AP** 로 재결합해도 새 결합으로 감지된다.

    bssid 를 비우지 않으면 BSSID 비교가 같아 재결합을 놓치고, attach ramp grace 가
    적용되지 않은 채 끊김 전 baseline·streak 으로 판정한다."""
    gs = wifi_roam.new_gate_state()
    wifi_roam.track_association(_st("aa:aa:aa:aa:aa:aa"), gs)
    gs["reset_rssi"] = -50
    gs["suppressed"] = 3
    wifi_roam.track_association(None, gs)                      # 끊김
    assert wifi_roam.track_association(_st("aa:aa:aa:aa:aa:aa"), gs) is True
    assert gs["reset_rssi"] is None and gs["suppressed"] == 0


def test_track_missing_bssid_field_is_ignored():
    """BSSID 필드가 없으면(파싱 실패 등) 결합 갱신을 하지 않는다 — 잘못된 grace 방지."""
    gs = wifi_roam.new_gate_state()
    assert wifi_roam.track_association({"rssi": -50}, gs) is False
    assert gs["bssid"] is None and gs["assoc_ts"] is None


def test_invalidate_reset_baseline():
    """[Codex P2] good-signal 이 아닌 리셋 경로(bgscan hint / 후보 발견)는 기준을 무효화한다.

    reset_rssi 는 '마지막 리셋 시점 RSSI' 여야 하는데 종전엔 good-signal 분기에서만
    갱신돼, 다른 경로로 리셋된 뒤의 판정이 옛 기준과 비교됐다."""
    gs = wifi_roam.new_gate_state()
    gs["reset_rssi"] = -50
    gs["suppressed"] = 4
    wifi_roam.invalidate_reset_baseline(gs)
    assert gs["reset_rssi"] is None
    assert gs["suppressed"] == 4, "억제 누적은 유지 — 결합이 바뀐 게 아니므로 요약 대상이다"


def test_grace_applies_after_reassoc(monkeypatch):
    """재결합 직후에는 게이트가 우회된다(통합 의미 고정) — track_association 이 갱신한
    assoc_ts 가 good_signal_reset_allowed 의 grace 판정에 실제로 쓰이는지."""
    gs = wifi_roam.new_gate_state()
    wifi_roam.track_association(_st(), gs)
    gs["reset_rssi"] = -50
    ok, why = wifi_roam.good_signal_reset_allowed(
        -50, gs["reset_rssi"], gs["assoc_ts"], now=gs["assoc_ts"] + 5
    )
    assert ok is True and why == "post-assoc-grace"
    # grace 경과 후에는 Δ0dB 가 억제된다
    ok2, _ = wifi_roam.good_signal_reset_allowed(
        -50, gs["reset_rssi"], gs["assoc_ts"], now=gs["assoc_ts"] + 41
    )
    assert ok2 is False


# ── 설정 로드 ──


@pytest.fixture
def restore_globals():
    """load_roaming_config 는 전역을 직접 setattr 로 갱신하므로(monkeypatch 추적 밖의
    다른 키까지 기본값으로 리셋한다) 테스트 후 원복해 다른 테스트를 오염시키지 않는다."""
    keys = [
        "ENABLE_GOOD_SIGNAL_GATE", "GOOD_SIGNAL_GATE_DELTA_DB", "GOOD_SIGNAL_GATE_GRACE_SEC",
        "DIFF_TH", "CHECK_INTERVAL", "WPA_TH_2G", "WPA_TH_5G",
        "ENABLE_PREDICTIVE_ROAM", "ENABLE_LOAD_BASED_ROAM", "ENABLE_STAGED_SCAN",
        "HOME_PASSIVE", "CACHE_FRESH_SEC", "SELF_INDUCED_TAIL_SEC",
    ]
    saved = {k: getattr(wifi_roam, k) for k in keys if hasattr(wifi_roam, k)}
    yield
    for k, v in saved.items():
        setattr(wifi_roam, k, v)


def _load(monkeypatch, roaming):
    """기존 테스트(test_roam_config_guard)와 같은 주입 경로 —
    두 번째 인자 data 는 '파일 경로'가 아니라 이미 파싱된 dict 이므로 쓰지 않고,
    WIFI_INIT_CONF_JSON 전역을 갈아 파일에서 읽게 한다."""
    import json, tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"mlan0": {"roaming": roaming}}, f)
        path = f.name
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", path)
    try:
        return wifi_roam.load_roaming_config("mlan0")
    finally:
        os.unlink(path)


def test_config_load_applies_gate(monkeypatch, restore_globals):
    """JSON 의 GOOD_SIGNAL_RESET_GATE 가 전역에 반영된다."""
    _load(monkeypatch, {"GOOD_SIGNAL_RESET_GATE":
                        {"enable": True, "delta_db": 3, "post_roam_grace_sec": 25}})
    assert wifi_roam.ENABLE_GOOD_SIGNAL_GATE is True
    assert wifi_roam.GOOD_SIGNAL_GATE_DELTA_DB == 3
    assert wifi_roam.GOOD_SIGNAL_GATE_GRACE_SEC == 25


def test_config_default_is_disabled(monkeypatch, restore_globals):
    """[무회귀] 블록이 없으면 기본 off — 기존 배포 동작 불변."""
    _load(monkeypatch, {"DIFF_TH": 8})
    assert wifi_roam.ENABLE_GOOD_SIGNAL_GATE is False


def test_config_zero_delta_clamped(monkeypatch, restore_globals):
    """delta_db=0 은 게이트를 무효화하므로 하한 1 로 클램프된다."""
    _load(monkeypatch, {"GOOD_SIGNAL_RESET_GATE": {"enable": True, "delta_db": 0}})
    assert wifi_roam.GOOD_SIGNAL_GATE_DELTA_DB >= 1


def test_config_zero_grace_clamped(monkeypatch, restore_globals):
    """post_roam_grace_sec=0 은 attach ramp 보호를 없애므로 하한 1 로 클램프."""
    _load(monkeypatch, {"GOOD_SIGNAL_RESET_GATE": {"enable": True, "post_roam_grace_sec": 0}})
    assert wifi_roam.GOOD_SIGNAL_GATE_GRACE_SEC >= 1


def test_config_garbage_falls_back(monkeypatch, restore_globals):
    """타입이 깨진 값은 기본값으로 폴백하고 크래시하지 않는다."""
    _load(monkeypatch, {"GOOD_SIGNAL_RESET_GATE": {"enable": "yes", "delta_db": "abc"}})
    assert isinstance(wifi_roam.GOOD_SIGNAL_GATE_DELTA_DB, int)
    assert wifi_roam.GOOD_SIGNAL_GATE_DELTA_DB >= 1


# ── wifi.sh 기본값 사본과의 동기 ──


def test_wifi_sh_gate_defaults_match_python():
    """`wifi <if> roam gate` 표시용으로 wifi.sh 가 들고 있는 기본값 사본이 이 모듈의
    DEFAULT_GOOD_SIGNAL_GATE_* 와 일치하는지 검증한다.

    사본을 둔 이유: JSON 에 키가 없을 때 데몬이 무엇을 쓰는지 CLI 가 보여주려면 값이
    필요한데, 셸에서 파이썬 상수를 읽는 것은 비싸다. 대신 여기서 두 파일을 파싱해
    비교함으로써 drift 를 차단한다. 경로가 틀리면 skip 이 아니라 **실패**해야 한다
    (조용한 skip 으로 검출력을 잃은 전례가 있다)."""
    sh = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "wifi.sh")
    assert os.path.isfile(sh), f"wifi.sh 경로가 틀렸다: {sh}"
    with open(sh, encoding="utf-8") as fh:
        text = fh.read()

    import re
    def sh_val(name):
        m = re.search(rf"^{name}=(\S+)", text, re.M)
        assert m, f"wifi.sh 에 {name} 정의가 없다"
        return m.group(1)

    assert sh_val("GATE_DEF_ENABLE") == str(wifi_roam.DEFAULT_ENABLE_GOOD_SIGNAL_GATE).lower()
    assert int(sh_val("GATE_DEF_DELTA_DB")) == wifi_roam.DEFAULT_GOOD_SIGNAL_GATE_DELTA_DB
    assert int(sh_val("GATE_DEF_GRACE_SEC")) == wifi_roam.DEFAULT_GOOD_SIGNAL_GATE_GRACE_SEC
