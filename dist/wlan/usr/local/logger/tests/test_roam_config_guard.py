"""_apply_runtime_globals 의 _num 가드 — 잘못된 설정값이 데몬을 망가뜨리지 않는가.

타입 가드(문자열/None/리스트 → 기본값 폴백)는 종전부터 있었으나 **값 범위**는 막지
않았다. sleep/interval 계열에 0·음수가 들어오면 크래시가 아니라 더 나쁜 상태가 된다:

  - interruptible_sleep(:1700)이 `seconds <= 0` 이면 즉시 반환 → 대기 없는 바쁜 루프.
    매 tick 스캔이 폭주해 CPU·airtime 을 잠식하고, 크래시가 아니라 감지도 어렵다.
  - ROAM_SUCCESS_SLEEP 은 time.sleep 직접 호출(:2946/:2953)이라 음수면 ValueError 로
    데몬이 죽는다. Restart=always 와 겹치면 3초마다 재시작을 무한 반복한다.

스키마의 `minimum` 은 WebUI 힌트일 뿐 데몬이 강제하지 않으므로, 직접 편집이나
마이그레이션으로 들어오는 값을 런타임에서 막아야 한다.
"""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock

sys.modules.setdefault("sUTILS", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wifi_roam

import pytest

wifi_roam.logger = MagicMock()

# 모듈 로드 시점의 유효 기본값 — 거부된 값은 여기로 폴백해야 한다.
DEFAULTS = {
    "SCAN_NO_RESULT_SLEEP": wifi_roam.DEFAULT_SCAN_NO_RESULT_SLEEP,
    "ROAM_SUCCESS_SLEEP": wifi_roam.DEFAULT_ROAM_SUCCESS_SLEEP,
    # CHECK_INTERVAL 은 전용 DEFAULT_ 상수 없이 :44 에서 직접 정의된다(=2).
    "CHECK_INTERVAL": wifi_roam.CHECK_INTERVAL,
}

# STAGED_SCAN 계열 — 중첩 경로는 _positive_int caster 가, flat 덮어쓰기 경로는
# _num(minimum=1) 이 각각 막는다. 두 경로 모두 이 전역을 건드리므로 복원 대상이다.
STAGED_DEFAULTS = {
    "CACHE_FRESH_SEC": wifi_roam.DEFAULT_CACHE_FRESH_SEC,
    "SELF_INDUCED_TAIL_SEC": wifi_roam.DEFAULT_SELF_INDUCED_TAIL_SEC,
}


@pytest.fixture(autouse=True)
def _restore_globals():
    """각 테스트가 전역을 오염시키지 않도록 기본값으로 되돌린다."""
    keys = {**DEFAULTS, **STAGED_DEFAULTS}
    saved = {k: getattr(wifi_roam, k) for k in keys}
    for k, v in keys.items():
        setattr(wifi_roam, k, v)
    yield
    for k, v in saved.items():
        setattr(wifi_roam, k, v)


def _load(roaming: dict, monkeypatch):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"mlan0": {"roaming": roaming}}, f)
        path = f.name
    monkeypatch.setattr(wifi_roam, "WIFI_INIT_CONF_JSON", path)
    try:
        wifi_roam.load_roaming_config("mlan0")
    finally:
        os.unlink(path)


@pytest.mark.parametrize("bad", ["bad", None, [1, 2], {"a": 1}])
def test_wrong_type_falls_back_to_default(bad, monkeypatch):
    """타입 오류는 종전부터 방어됨 — 기본값 유지, 크래시 없음."""
    _load({"SCAN_NO_RESULT_SLEEP": bad}, monkeypatch)
    assert wifi_roam.SCAN_NO_RESULT_SLEEP == DEFAULTS["SCAN_NO_RESULT_SLEEP"]


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_non_positive_sleep_rejected(bad, monkeypatch):
    """[핵심] 0·음수 sleep 은 거부하고 기본값 유지 — 바쁜 루프/ValueError 방지."""
    _load(
        {
            "SCAN_NO_RESULT_SLEEP": bad,
            "ROAM_SUCCESS_SLEEP": bad,
            "CHECK_INTERVAL": bad,
        },
        monkeypatch,
    )
    for key, default in DEFAULTS.items():
        assert getattr(wifi_roam, key) == default, f"{key} 가 {bad} 로 오염됐다"



def test_non_positive_does_not_produce_zero_backoff(monkeypatch):
    """거부 후 backoff 시퀀스가 0/음수로 떨어지지 않는다(바쁜 루프 회귀 감지)."""
    _load({"SCAN_NO_RESULT_SLEEP": 0}, monkeypatch)
    streak = 0
    for _ in range(8):
        backoff, streak = wifi_roam.advance_no_candidate_backoff(streak)
        assert backoff >= 1, f"backoff={backoff} — 대기 없는 루프가 된다"


def test_valid_values_still_applied(monkeypatch):
    """무회귀: 정상 값은 그대로 반영된다."""
    _load(
        {"SCAN_NO_RESULT_SLEEP": 4, "CHECK_INTERVAL": 5},
        monkeypatch,
    )
    assert wifi_roam.SCAN_NO_RESULT_SLEEP == 4
    assert wifi_roam.CHECK_INTERVAL == 5


def test_max_sleep_backdoor_closed(monkeypatch):
    """ROAM_NO_RESULT_MAX_SLEEP 은 JSON 으로 바꿀 수 없다(감사 D2 — 뒷문 봉쇄 고정).

    과거엔 로더가 .get() 으로 읽어 템플릿·스키마에 없는 키가 몰래 실효됐다.
    누군가 로드를 되살리면 이 테스트가 먼저 깨진다."""
    _load({"ROAM_NO_RESULT_MAX_SLEEP": 40}, monkeypatch)
    assert wifi_roam.ROAM_NO_RESULT_MAX_SLEEP == \
        wifi_roam.DEFAULT_ROAM_NO_RESULT_MAX_SLEEP


def test_cache_fresh_sec_zero_rejected_via_nested_caster(monkeypatch):
    """중첩 경로(STAGED_SCAN.cache_fresh_sec)는 **기존** _positive_int caster 가 막는다.

    이 테스트는 이번에 추가한 _num(minimum=) 가드가 아니라 종전부터 있던 방어를
    확인한다(무회귀용). 새 가드 검증은 아래 flat 경로 테스트가 담당한다."""
    before = wifi_roam.CACHE_FRESH_SEC
    _load({"STAGED_SCAN": {"cache_fresh_sec": 0}}, monkeypatch)
    assert wifi_roam.CACHE_FRESH_SEC >= 1
    assert wifi_roam.CACHE_FRESH_SEC == before


@pytest.mark.parametrize("bad", [0, -1])
def test_cache_fresh_sec_zero_rejected_via_flat_override(bad, monkeypatch):
    """[새 가드] flat 덮어쓰기 경로는 _positive_int 를 우회하므로 _num(minimum=1) 이 막는다.

    roaming 블록에 대문자 키를 직접 넣으면 `for key in config.keys()` 루프가 caster 를
    거치지 않고 raw 로 덮어쓴다. CACHE_FRESH_SEC=0 이면 scan_block_fresh 가 항상 False
    가 되어 Stage 2 교차채널 캐시가 영구 비활성화된다."""
    _load({"CACHE_FRESH_SEC": bad, "SELF_INDUCED_TAIL_SEC": bad}, monkeypatch)
    for key, default in STAGED_DEFAULTS.items():
        assert getattr(wifi_roam, key) == default, f"{key} 가 {bad} 로 오염됐다"
